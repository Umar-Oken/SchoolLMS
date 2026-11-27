from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework import serializers

from accounts.models import User
from academics.views import teacher_course_scope
from ai.models import AIDailyQuota, AIJob, AIJobResultDraft
from ai.providers import ResourceSnapshot, StructuredQuestionDraft
from files.models import Attachment
from learning.models import Resource
from assessments.models import Option, Question
from outbox.services import enqueue_outbox_event


class AIDailyQuotaExceeded(APIException):
    status_code = 429
    default_detail = 'Bugungi AI job limiti tugagan.'
    default_code = 'ai_daily_quota_exceeded'


class AIIdempotencyKeyConflict(APIException):
    status_code = 409
    default_detail = 'Idempotency-Key avval boshqa AI so‘rovi uchun ishlatilgan.'
    default_code = 'idempotency_key_conflict'


@transaction.atomic
def create_ai_job(*, actor, resource, prompt_version=1, provider_model='unconfigured'):
    """Create only a queued job for a teacher's active course resource."""
    if not isinstance(actor, User) or actor.role != User.Role.TEACHER:
        raise NotFound()

    resource = (
        Resource.objects.select_for_update(of=('self',))
        .select_related('topic__course')
        .filter(
            pk=resource.pk,
            topic__course_id__in=teacher_course_scope(
                actor,
                active_only=True,
            ).values('pk'),
        )
        .first()
    )
    if resource is None:
        raise NotFound()

    job = AIJob(
        actor=actor,
        course=resource.topic.course,
        resource=resource,
        resource_version=resource.version,
        prompt_version=prompt_version,
        provider_model=provider_model,
    )
    try:
        job.full_clean()
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict) from exc
    job.save()
    return job


def request_question_drafts(
    *,
    actor,
    resource_id,
    resource_version,
    idempotency_key,
    daily_limit,
):
    """Atomically reserve one daily job, create it, and enqueue an outbox signal."""
    if (
        not idempotency_key
        or idempotency_key != idempotency_key.strip()
        or len(idempotency_key) > 255
    ):
        raise ValidationError({
            'Idempotency-Key': 'Header must be non-empty and at most 255 characters.',
        })

    # The actor/key unique constraint handles simultaneous retries. Retrying the
    # short transaction rolls back a losing request's quota reservation too.
    for attempt in range(2):
        try:
            with transaction.atomic():
                existing = (
                    AIJob.objects.select_for_update()
                    .filter(actor=actor, idempotency_key=idempotency_key)
                    .first()
                )
                if existing is not None:
                    if (
                        existing.resource_id != resource_id
                        or existing.resource_version != resource_version
                    ):
                        raise AIIdempotencyKeyConflict()
                    return existing, False

                if not isinstance(actor, User) or actor.role != User.Role.TEACHER:
                    raise NotFound()
                resource = (
                    Resource.objects.select_for_update(of=('self',))
                    .select_related('topic__course', 'attachment')
                    .filter(
                        pk=resource_id,
                        version=resource_version,
                        kind=Resource.Kind.PDF,
                        state=Resource.State.PUBLISHED,
                        topic__state='published',
                        topic__archived_at__isnull=True,
                        attachment__scan_status=Attachment.ScanStatus.CLEAN,
                        attachment__detected_mime='application/pdf',
                        topic__course_id__in=teacher_course_scope(
                            actor,
                            active_only=True,
                        ).values('pk'),
                    )
                    .first()
                )
                if resource is None:
                    raise NotFound()

                quota_day = timezone.localdate()
                try:
                    with transaction.atomic():
                        AIDailyQuota.objects.get_or_create(
                            actor=actor,
                            day=quota_day,
                        )
                except IntegrityError:
                    if attempt == 0:
                        continue
                    raise
                quota = AIDailyQuota.objects.select_for_update().get(
                    actor=actor,
                    day=quota_day,
                )
                if quota.jobs_reserved >= daily_limit:
                    raise AIDailyQuotaExceeded()
                quota.jobs_reserved += 1
                quota.save(update_fields=('jobs_reserved', 'updated_at'))

                job = AIJob.objects.create(
                    actor=actor,
                    course=resource.topic.course,
                    resource=resource,
                    resource_version=resource.version,
                    requested_question_count=5,
                    idempotency_key=idempotency_key,
                    provider_model='unconfigured',
                )
                enqueue_outbox_event(
                    kind='ai.question_draft.requested',
                    payload={'job_id': job.pk},
                    dedup_key=f'ai-question-draft:{job.pk}',
                )
                return job, True
        except IntegrityError:
            if attempt == 0:
                continue
            raise
    raise RuntimeError('AI job reservation retry exhausted.')


class ProviderCitationSerializer(serializers.Serializer):
    source_id = serializers.IntegerField(min_value=1)
    resource_version = serializers.IntegerField(min_value=1)
    resource_sha256 = serializers.RegexField(r'^[0-9a-f]{64}$')
    page_number = serializers.IntegerField(min_value=1)


class ProviderDraftSerializer(serializers.Serializer):
    question_text = serializers.CharField(trim_whitespace=True, allow_blank=False)
    choices = serializers.ListField(child=serializers.CharField(trim_whitespace=True, allow_blank=False), min_length=2)
    correct_choice_index = serializers.IntegerField(min_value=0)
    explanation = serializers.CharField(trim_whitespace=True, allow_blank=False)
    citation = ProviderCitationSerializer()

    def validate(self, attrs):
        if attrs['correct_choice_index'] >= len(attrs['choices']):
            raise serializers.ValidationError({'correct_choice_index': 'Must identify one choice.'})
        if len(set(attrs['choices'])) != len(attrs['choices']):
            raise serializers.ValidationError({'choices': 'Choices must be distinct.'})
        return attrs


@transaction.atomic
def store_result_drafts(*, job, drafts, tokens=0, allowed_citations=None):
    """Persist provider-shaped drafts and move a running job to review."""
    job = AIJob.objects.select_for_update().get(pk=job.pk)
    if job.state != AIJob.State.RUNNING:
        raise ValidationError({'state': 'Only running jobs can receive results.'})
    if tokens < 0:
        raise ValidationError({'tokens': 'Tokens must not be negative.'})

    if len(drafts) != job.requested_question_count:
        raise ValidationError({'drafts': 'Provider must return exactly five drafts.'})
    rows = []
    for position, draft in enumerate(drafts, start=1):
        if not isinstance(draft, StructuredQuestionDraft):
            raise ValidationError({'drafts': 'Provider returned an invalid draft.'})
        serializer = ProviderDraftSerializer(data={
            'question_text': draft.question_text, 'choices': list(draft.choices),
            'correct_choice_index': draft.correct_choice_index, 'explanation': draft.explanation,
            'citation': draft.citation,
        })
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        citation = dict(data['citation'])
        if allowed_citations is not None and tuple(sorted(citation.items())) not in allowed_citations:
            raise ValidationError({'citation': 'Citation is not from this PDF snapshot.'})
        rows.append(AIJobResultDraft(
            job=job,
            position=position,
            question_text=data['question_text'], choices=data['choices'],
            correct_choice_index=data['correct_choice_index'], explanation=data['explanation'],
            citation=citation,
        ))
    AIJobResultDraft.objects.bulk_create(rows)
    job.state = AIJob.State.REVIEW
    job.tokens = tokens
    job.error = ''
    job.save(update_fields=('state', 'tokens', 'error', 'updated_at'))
    return job


def resource_snapshot(resource):
    return ResourceSnapshot(
        resource_id=resource.pk,
        resource_version=resource.version,
        title=resource.title,
        text=resource.transcript_text or resource.description,
    )


@transaction.atomic
def approve_ai_job(*, actor, job_id, draft_version):
    job = AIJob.objects.select_for_update().select_related('resource').filter(pk=job_id, actor=actor).first()
    if job is None:
        raise NotFound()
    if job.draft_version != draft_version or job.resource.version != job.resource_version:
        raise ValidationError({'draft_version': 'Stale draft or source version.'})
    drafts = list(job.result_drafts.select_for_update().order_by('position'))
    if job.state == AIJob.State.APPROVED:
        return [draft.approved_question_id for draft in drafts]
    if job.state != AIJob.State.REVIEW:
        raise ValidationError({'state': 'Only reviewed drafts can be approved.'})
    for draft in drafts:
        question = Question.objects.create(course=job.course, stem=draft.question_text, type=Question.Type.SINGLE_CHOICE, marking_note=draft.explanation)
        Option.objects.bulk_create([Option(question=question, text=text, is_correct=index == draft.correct_choice_index) for index, text in enumerate(draft.choices)])
        draft.approved_question = question
        draft.save(update_fields=('approved_question',))
    job.state = AIJob.State.APPROVED
    job.save(update_fields=('state', 'updated_at'))
    return [draft.approved_question_id for draft in drafts]


@transaction.atomic
def reject_ai_job(*, actor, job_id, draft_version):
    job = AIJob.objects.select_for_update().filter(pk=job_id, actor=actor).first()
    if job is None:
        raise NotFound()
    if job.draft_version != draft_version:
        raise ValidationError({'draft_version': 'Stale draft.'})
    job.state = AIJob.State.REJECTED
    job.save(update_fields=('state', 'updated_at'))
    return job


@transaction.atomic
def edit_ai_draft(*, actor, job_id, draft_id, draft_version, data):
    job = AIJob.objects.select_for_update().filter(pk=job_id, actor=actor, state=AIJob.State.REVIEW).first()
    if job is None:
        raise NotFound()
    if job.draft_version != draft_version or job.resource.version != job.resource_version:
        raise ValidationError({'draft_version': 'Stale draft or source version.'})
    draft = AIJobResultDraft.objects.select_for_update().filter(pk=draft_id, job=job).first()
    if draft is None:
        raise NotFound()
    draft.question_text = data['question_text']
    draft.choices = data['choices']
    draft.correct_choice_index = data['correct_choice_index']
    draft.explanation = data['explanation']
    draft.save(update_fields=('question_text', 'choices', 'correct_choice_index', 'explanation'))
    job.draft_version += 1
    job.save(update_fields=('draft_version', 'updated_at'))
    return draft, job
