from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models import User
from academics.models import (
    AcademicYear,
    CourseOffering,
    SchoolClass,
    Subject,
    TeachingAssignment,
    Term,
)
from ai.models import AIDailyQuota, AIJob, AIJobResultDraft
from ai.providers import FakeProvider, ProviderTimeout, StructuredQuestionDraft, get_configured_provider
from ai.serializers import AIJobCreateSerializer
from ai.services import create_ai_job, resource_snapshot, store_result_drafts
from ai.tasks import generate_question_drafts
from assignments.models import Grade
from files.models import Attachment
from learning.models import Resource, Topic
from outbox.models import OutboxEvent
from outbox.tasks import consume_outbox_event


@pytest.fixture
def ai_data(db):
    year = AcademicYear.objects.create(
        label='AI 2026', start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
    )
    term = Term.objects.create(
        year=year, order=1, start_date=date(2026, 1, 1), end_date=date(2026, 6, 1),
    )
    subject = Subject.objects.create(code='AI-TEST', title='AI test')
    course_a = CourseOffering.objects.create(
        school_class=SchoolClass.objects.create(year=year, grade_level=7, section='A'),
        subject=subject,
    )
    course_b = CourseOffering.objects.create(
        school_class=SchoolClass.objects.create(year=year, grade_level=7, section='B'),
        subject=subject,
    )
    teacher_a = User.objects.create_user(
        username='ai-teacher-a', password='safe-test-password', role=User.Role.TEACHER,
    )
    teacher_b = User.objects.create_user(
        username='ai-teacher-b', password='safe-test-password', role=User.Role.TEACHER,
    )
    TeachingAssignment.objects.create(course=course_a, teacher=teacher_a, start_date=date(2026, 1, 1))
    TeachingAssignment.objects.create(course=course_b, teacher=teacher_b, start_date=date(2026, 1, 1))
    topic_a = Topic.objects.create(
        course=course_a, term=term, title='AI A', objective='obj', order=1,
        state=Topic.State.PUBLISHED, published_at=timezone.now(),
    )
    topic_b = Topic.objects.create(
        course=course_b, term=term, title='AI B', objective='obj', order=1,
        state=Topic.State.PUBLISHED, published_at=timezone.now(),
    )
    resource_a = Resource.objects.create(
        topic=topic_a, kind=Resource.Kind.LINK, title='A resource', url='https://youtube.com/watch?v=a', version=3,
    )
    resource_b = Resource.objects.create(
        topic=topic_b, kind=Resource.Kind.LINK, title='B resource', url='https://youtube.com/watch?v=b', version=2,
    )
    attachment_a = Attachment.objects.create(
        owner=teacher_a, key=uuid4(), original_name='ai-a.pdf', size=9,
        detected_mime='application/pdf', scan_status=Attachment.ScanStatus.CLEAN,
    )
    attachment_b = Attachment.objects.create(
        owner=teacher_b, key=uuid4(), original_name='ai-b.pdf', size=9,
        detected_mime='application/pdf', scan_status=Attachment.ScanStatus.CLEAN,
    )
    pdf_a = Resource.objects.create(
        topic=topic_a, kind=Resource.Kind.PDF, title='AI PDF A', attachment=attachment_a,
        state=Resource.State.PUBLISHED, version=4,
    )
    pdf_b = Resource.objects.create(
        topic=topic_b, kind=Resource.Kind.PDF, title='AI PDF B', attachment=attachment_b,
        state=Resource.State.PUBLISHED, version=2,
    )
    return {
        'course_a': course_a,
        'course_b': course_b,
        'teacher_a': teacher_a,
        'teacher_b': teacher_b,
        'resource_a': resource_a,
        'resource_b': resource_b,
        'pdf_a': pdf_a,
        'pdf_b': pdf_b,
    }


def test_job_derives_course_and_snapshot_and_rejects_foreign_scope(ai_data):
    job = create_ai_job(actor=ai_data['teacher_a'], resource=ai_data['resource_a'])

    assert job.course_id == ai_data['course_a'].pk
    assert job.resource_version == 3
    assert job.state == AIJob.State.QUEUED

    from rest_framework.exceptions import NotFound

    with pytest.raises(NotFound):
        create_ai_job(actor=ai_data['teacher_a'], resource=ai_data['resource_b'])
    with pytest.raises(NotFound):
        create_ai_job(actor=ai_data['teacher_b'], resource=ai_data['resource_a'])


def test_model_and_database_protect_job_invariants(ai_data):
    invalid = AIJob(
        actor=ai_data['teacher_a'],
        course=ai_data['course_a'],
        resource=ai_data['resource_b'],
        resource_version=2,
        provider_model='disabled',
    )
    with pytest.raises(ValidationError, match='Resource must belong'):
        invalid.full_clean()

    job = create_ai_job(actor=ai_data['teacher_a'], resource=ai_data['resource_a'])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AIJob.objects.filter(pk=job.pk).update(state='invalid')


def test_create_serializer_derives_server_fields_and_rejects_injection(ai_data):
    request = APIRequestFactory().post('/not-yet-an-api/')
    # No view exists in P085 yet, so provide the same authenticated actor that
    # DRF will place on Request in the eventual endpoint.
    request.user = ai_data['teacher_a']
    serializer = AIJobCreateSerializer(
        data={'resource': ai_data['resource_a'].pk, 'course': ai_data['course_b'].pk},
        context={'request': request},
    )
    assert not serializer.is_valid()
    assert 'course' in serializer.errors

    serializer = AIJobCreateSerializer(
        data={'resource': ai_data['resource_a'].pk, 'prompt_version': 2},
        context={'request': request},
    )
    assert serializer.is_valid(), serializer.errors
    job = serializer.save()
    assert job.actor_id == ai_data['teacher_a'].pk
    assert job.course_id == ai_data['course_a'].pk


def test_fake_provider_is_structured_and_cannot_run_in_production(ai_data):
    with override_settings(APP_ENV='test', AI_PROVIDER='fake'):
        provider = get_configured_provider()
    assert isinstance(provider, FakeProvider)

    job = create_ai_job(actor=ai_data['teacher_a'], resource=ai_data['resource_a'])
    job.state = AIJob.State.RUNNING
    job.save(update_fields=('state',))
    before_grades = Grade.objects.count()
    job = store_result_drafts(
        job=job,
        drafts=provider.generate_question_drafts(
            resource=resource_snapshot(ai_data['resource_a']), prompt_version=job.prompt_version,
        ),
        tokens=17,
    )
    assert job.state == AIJob.State.REVIEW
    assert job.tokens == 17
    assert AIJobResultDraft.objects.filter(job=job).count() == 5
    assert Grade.objects.count() == before_grades
    assert 'grade' not in {field.name for field in AIJob._meta.get_fields()}

    with override_settings(APP_ENV='production', AI_PROVIDER='fake'):
        with pytest.raises(ImproperlyConfigured, match='not allowed'):
            get_configured_provider()


def _request_draft(*, user, resource, key):
    client = APIClient()
    client.force_authenticate(user=user)
    with patch('outbox.services.wake_event_dispatcher'):
        return client.post(
            '/api/v1/ai/question-drafts/',
            {'resource': resource.pk, 'resource_version': resource.version},
            format='json',
            HTTP_IDEMPOTENCY_KEY=key,
        )


def test_question_draft_request_is_accepted_idempotent_and_status_is_private(ai_data):
    first = _request_draft(
        user=ai_data['teacher_a'], resource=ai_data['pdf_a'], key='p086-duplicate',
    )
    assert first.status_code == status.HTTP_202_ACCEPTED
    assert first.data['status'] == AIJob.State.QUEUED
    assert first.data['status_url'].endswith(f"/api/v1/ai/jobs/{first.data['job_id']}/")
    assert 'result_drafts' not in first.data
    assert 'request_id' in first.data
    assert AIJob.objects.count() == 1
    assert AIDailyQuota.objects.get(actor=ai_data['teacher_a']).jobs_reserved == 1
    event = OutboxEvent.objects.get(kind='ai.question_draft.requested')
    with patch('ai.tasks.generate_question_drafts.apply_async') as enqueue:
        assert consume_outbox_event.run(event.pk) == OutboxEvent.Status.PROCESSED
    enqueue.assert_called_once_with(args=(first.data['job_id'],), queue='ai')
    assert AIJob.objects.get(pk=first.data['job_id']).state == AIJob.State.QUEUED

    retry = _request_draft(
        user=ai_data['teacher_a'], resource=ai_data['pdf_a'], key='p086-duplicate',
    )
    assert retry.status_code == status.HTTP_202_ACCEPTED
    assert retry.data['job_id'] == first.data['job_id']
    assert AIJob.objects.count() == 1
    assert AIDailyQuota.objects.get(actor=ai_data['teacher_a']).jobs_reserved == 1

    client = APIClient()
    client.force_authenticate(user=ai_data['teacher_a'])
    assert client.get(first.data['status_url']).status_code == status.HTTP_200_OK
    client.force_authenticate(user=ai_data['teacher_b'])
    assert client.get(first.data['status_url']).status_code == status.HTTP_404_NOT_FOUND


@override_settings(AI_DAILY_JOB_LIMIT=1)
def test_question_draft_rejects_foreign_resource_and_exhausted_quota(ai_data):
    foreign = _request_draft(
        user=ai_data['teacher_a'], resource=ai_data['pdf_b'], key='p086-foreign',
    )
    assert foreign.status_code == status.HTTP_404_NOT_FOUND
    assert not AIDailyQuota.objects.exists()

    accepted = _request_draft(
        user=ai_data['teacher_a'], resource=ai_data['pdf_a'], key='p086-first',
    )
    assert accepted.status_code == status.HTTP_202_ACCEPTED
    exhausted = _request_draft(
        user=ai_data['teacher_a'], resource=ai_data['pdf_a'], key='p086-second',
    )
    assert exhausted.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert exhausted.data['code'] == 'ai_daily_quota_exceeded'


def test_worker_rejects_malformed_or_missing_source_and_retries_timeout(ai_data):
    job = AIJob.objects.create(actor=ai_data['teacher_a'], course=ai_data['course_a'],
        resource=ai_data['pdf_a'], resource_version=4, provider_model='fake')
    chunk = SimpleNamespace(source_id=job.resource_id, resource_version=4,
        resource_sha256='a' * 64, page_number=1, text='source text')
    extraction = SimpleNamespace(chunks=(chunk,))
    bad = [StructuredQuestionDraft('Q?', ('A', 'B'), 0, 'why', None)] * 5
    with patch('ai.tasks.extract_pdf_text', return_value=extraction), patch(
        'ai.tasks.get_configured_provider', return_value=SimpleNamespace(
            generate_question_drafts=lambda **kwargs: bad,
        ),
    ):
        assert generate_question_drafts.run(job.pk) == 'failed'
    assert AIJob.objects.get(pk=job.pk).error == 'invalid_provider_output'

    job.state = AIJob.State.QUEUED
    job.save(update_fields=('state',))
    timeout_provider = SimpleNamespace(
        generate_question_drafts=lambda **kwargs: (_ for _ in ()).throw(ProviderTimeout()),
    )
    with patch('ai.tasks.extract_pdf_text', return_value=extraction), patch(
        'ai.tasks.get_configured_provider', return_value=timeout_provider,
    ), pytest.raises(ProviderTimeout):
        generate_question_drafts.run(job.pk)
    assert AIJob.objects.get(pk=job.pk).state == AIJob.State.QUEUED
