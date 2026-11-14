import hashlib
import json
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound

from academics.models import (
    Enrollment,
    MembershipStatus,
    TeachingAssignment,
)
from assignments.models import (
    Assignment,
    Criterion,
    CriterionScore,
    Grade,
    GradeRevision,
    Level,
    Submission,
    SubmissionAttachment,
    SubmissionIdempotencyReceipt,
    SubmissionReturn,
    SubmissionRevision,
)
from assignments.time import assignment_time_status
from files.models import Attachment


class StaleDraftVersion(APIException):
    status_code = 409
    default_detail = 'Draft boshqa versiyada yangilangan; uni qayta yuklang.'
    default_code = 'stale_draft_version'


class DuplicateSubmission(APIException):
    status_code = 409
    default_detail = 'Bu draft versiyasi avval yuborilgan.'
    default_code = 'duplicate_submission'


class IdempotencyKeyConflict(APIException):
    status_code = 409
    default_detail = 'Idempotency-Key avval boshqa input bilan ishlatilgan.'
    default_code = 'idempotency_key_conflict'


class SubmissionNotReturned(APIException):
    status_code = 409
    default_detail = 'Yangi draft uchun oxirgi ish teacher tomonidan qaytarilishi kerak.'
    default_code = 'submission_not_returned'


class StaleGradeVersion(APIException):
    status_code = 409
    default_detail = 'Baho drafti boshqa versiyada yangilangan.'
    default_code = 'stale_grade_version'


class StaleGradeRevisionVersion(APIException):
    status_code = 409
    default_detail = 'Submitted revision versiyasi mos kelmaydi.'
    default_code = 'stale_grade_revision_version'


def submission_input_fingerprint(*, expected_version):
    canonical_input = json.dumps(
        {'draft_version': expected_version},
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical_input.encode('utf-8')).hexdigest()


@transaction.atomic
def publish_assignment(assignment_id):
    assignment = (
        Assignment.objects.select_for_update()
        .filter(pk=assignment_id)
        .first()
    )
    if assignment is None:
        raise NotFound()
    if assignment.state == Assignment.State.PUBLISHED:
        return assignment

    errors = {}
    if not assignment.title.strip():
        errors['title'] = 'Sarlavha bo‘sh bo‘lmasligi kerak.'
    if not assignment.instructions.strip():
        errors['instructions'] = 'Shart bo‘sh bo‘lmasligi kerak.'
    if not all((
        assignment.available_at,
        assignment.due_at,
        assignment.closes_at,
    )):
        errors['deadlines'] = 'Barcha deadline qiymatlari kiritilishi kerak.'
    elif not (
        assignment.available_at <= assignment.due_at <= assignment.closes_at
    ):
        errors['deadlines'] = (
            'Deadline tartibi available_at <= due_at <= closes_at bo‘lishi kerak.'
        )
    if not assignment.max_score or assignment.max_score <= 0:
        errors['max_score'] = 'Maksimal ball musbat bo‘lishi kerak.'

    criteria = list(
        Criterion.objects.select_for_update()
        .filter(assignment=assignment)
        .prefetch_related('levels')
    )
    if not criteria:
        errors['criteria'] = 'Publish uchun kamida bitta criterion kerak.'
    elif sum(
        (criterion.max_points for criterion in criteria),
        start=assignment.max_score.__class__('0'),
    ) != assignment.max_score:
        errors['criteria'] = (
            'Criterion max_points yig‘indisi assignment max_scorega teng bo‘lishi kerak.'
        )

    for criterion in criteria:
        levels = list(criterion.levels.all())
        if not criterion.label.strip():
            errors[f'criterion_{criterion.pk}'] = (
                'Criterion label bo‘sh bo‘lmasligi kerak.'
            )
        if not levels or not any(level.points == 0 for level in levels):
            errors[f'criterion_{criterion.pk}_levels'] = (
                'Har bir criterion kamida bitta 0 ballli levelga ega bo‘lishi kerak.'
            )
        if any(level.points > criterion.max_points for level in levels):
            errors[f'criterion_{criterion.pk}_levels'] = (
                'Level ballari criterion max_pointsdan oshmasligi kerak.'
            )
        if any(not level.description.strip() for level in levels):
            errors[f'criterion_{criterion.pk}_levels'] = (
                'Level tavsifi bo‘sh bo‘lmasligi kerak.'
            )

    if errors:
        raise serializers.ValidationError(errors)

    assignment.state = Assignment.State.PUBLISHED
    assignment.published_at = timezone.now()
    assignment.rubric_version = 1
    assignment.save(
        update_fields=('state', 'published_at', 'rubric_version'),
    )
    return assignment


@transaction.atomic
def save_submission_draft(
    assignment_id,
    pupil,
    *,
    draft_text,
    expected_version,
    attachment_ids=None,
):
    assignment = (
        Assignment.objects.select_for_update()
        .select_related('topic')
        .filter(
            pk=assignment_id,
            state=Assignment.State.PUBLISHED,
            topic__state='published',
            topic__archived_at__isnull=True,
        )
        .first()
    )
    if assignment is None:
        raise NotFound()
    is_enrolled = Enrollment.objects.filter(
        pupil=pupil,
        course_id=assignment.topic.course_id,
        status=MembershipStatus.ACTIVE,
        end_date__isnull=True,
    ).exists()
    if not is_enrolled:
        raise NotFound()

    submission, _ = Submission.objects.get_or_create(
        assignment=assignment,
        pupil=pupil,
    )
    latest_revision = submission.revisions.order_by(
        '-revision_number',
    ).first()
    if (
        latest_revision is not None
        and not SubmissionReturn.objects.filter(
            revision=latest_revision,
        ).exists()
    ):
        raise SubmissionNotReturned()
    if attachment_ids is not None:
        if len(attachment_ids) > 3 or len(attachment_ids) != len(
            set(attachment_ids)
        ):
            raise serializers.ValidationError({
                'attachment_ids': (
                    'Uchta gacha, takrorlanmagan fayl ID yuboring.'
                )
            })
        attachments = list(
            Attachment.objects.select_for_update()
            .filter(pk__in=attachment_ids)
            .order_by('pk')
        )
        attachments_by_id = {
            attachment.pk: attachment for attachment in attachments
        }
        for attachment_id in attachment_ids:
            attachment = attachments_by_id.get(attachment_id)
            if attachment is None:
                raise serializers.ValidationError({
                    'attachment_ids': f'Fayl topilmadi: {attachment_id}.'
                })
            if attachment.owner_id != pupil.pk:
                raise serializers.ValidationError({
                    'attachment_ids': (
                        f'Fayl sizga tegishli emas: {attachment_id}.'
                    )
                })
            if attachment.scan_status != Attachment.ScanStatus.CLEAN:
                raise serializers.ValidationError({
                    'attachment_ids': (
                        f'Faqat clean faylni biriktirish mumkin: {attachment_id}.'
                    )
                })

        links = {
            link.attachment_id: link
            for link in SubmissionAttachment.objects.select_for_update()
            .filter(attachment_id__in=attachment_ids)
        }
        for attachment_id, link in links.items():
            if (
                link.submission_id != submission.pk
                or link.revision_id is not None
            ):
                raise serializers.ValidationError({
                    'attachment_ids': (
                        f'Fayl boshqa draft yoki revisionga biriktirilgan: '
                        f'{attachment_id}.'
                    )
                })

        SubmissionAttachment.objects.filter(
            submission=submission,
        ).exclude(
            attachment_id__in=attachment_ids,
        ).delete()
        SubmissionAttachment.objects.bulk_create([
            SubmissionAttachment(
                submission=submission,
                attachment=attachments_by_id[attachment_id],
            )
            for attachment_id in attachment_ids
            if attachment_id not in links
        ])

    updated = Submission.objects.filter(
        pk=submission.pk,
        draft_version=expected_version,
    ).update(
        draft_text=draft_text,
        draft_version=F('draft_version') + 1,
        updated_at=timezone.now(),
    )
    if not updated:
        raise StaleDraftVersion()
    return Submission.objects.get(pk=submission.pk)


@transaction.atomic
def submit_work(
    assignment_id,
    pupil,
    *,
    expected_version,
    idempotency_key,
):
    assignment = (
        Assignment.objects.select_for_update()
        .select_related('topic')
        .filter(
            pk=assignment_id,
            state=Assignment.State.PUBLISHED,
            topic__state='published',
            topic__archived_at__isnull=True,
        )
        .first()
    )
    if assignment is None:
        raise NotFound()
    fingerprint = submission_input_fingerprint(
        expected_version=expected_version,
    )
    receipt = SubmissionIdempotencyReceipt.objects.filter(
        assignment=assignment,
        pupil=pupil,
        key=idempotency_key,
    ).first()
    if receipt is not None:
        if receipt.input_fingerprint != fingerprint:
            raise IdempotencyKeyConflict()
        return receipt.response_payload

    if not Enrollment.objects.filter(
        pupil=pupil,
        course_id=assignment.topic.course_id,
        status=MembershipStatus.ACTIVE,
        end_date__isnull=True,
    ).exists():
        raise NotFound()

    submission = (
        Submission.objects.select_for_update()
        .filter(assignment=assignment, pupil=pupil)
        .first()
    )
    if submission is None:
        raise serializers.ValidationError(
            {'detail': 'Yuborish uchun draft matni yoki fayl kerak.'}
        )
    if submission.draft_version != expected_version:
        raise StaleDraftVersion()

    now = timezone.now()
    time_status = assignment_time_status(assignment, now=now)
    if time_status['availability_status'] == 'upcoming':
        raise serializers.ValidationError({
            'detail': 'Vazifani available_at vaqtidan oldin yuborib bo‘lmaydi.'
        })
    if time_status['availability_status'] == 'closed':
        raise serializers.ValidationError({
            'detail': 'Vazifaning qabul qilish muddati tugagan.'
        })

    latest_revision = (
        SubmissionRevision.objects.select_for_update()
        .filter(submission=submission)
        .order_by('-revision_number')
        .first()
    )
    if (
        latest_revision is not None
        and latest_revision.submitted_version == expected_version
    ):
        raise DuplicateSubmission()
    attempt_count = submission.revisions.count()
    if attempt_count >= assignment.max_attempts:
        raise serializers.ValidationError({
            'detail': 'Vazifaning maksimal urinishlar soni tugagan.'
        })

    attachment_ids = list(
        SubmissionAttachment.objects.filter(
            submission=submission,
        )
        .order_by('attachment_id')
        .values_list('attachment_id', flat=True)
    )
    attachments = list(
        Attachment.objects.select_for_update()
        .filter(pk__in=attachment_ids)
        .order_by('pk')
    )
    attachments_by_id = {
        attachment.pk: attachment for attachment in attachments
    }
    if any(
        attachment_id not in attachments_by_id
        or attachments_by_id[attachment_id].scan_status
        != Attachment.ScanStatus.CLEAN
        for attachment_id in attachment_ids
    ):
        raise serializers.ValidationError({
            'attachment_ids': 'Yuborilayotgan fayllar clean bo‘lishi kerak.'
        })
    if not submission.draft_text.strip() and not attachment_ids:
        raise serializers.ValidationError(
            {'detail': 'Yuborish uchun draft matni yoki fayl kerak.'}
        )

    try:
        with transaction.atomic():
            receipt = SubmissionIdempotencyReceipt.objects.create(
                assignment=assignment,
                pupil=pupil,
                key=idempotency_key,
                input_fingerprint=fingerprint,
                response_payload={},
            )
    except IntegrityError as exc:
        receipt = SubmissionIdempotencyReceipt.objects.filter(
            assignment=assignment,
            pupil=pupil,
            key=idempotency_key,
        ).first()
        if receipt is None:
            raise exc
        if receipt.input_fingerprint != fingerprint:
            raise IdempotencyKeyConflict() from exc
        return receipt.response_payload

    draft_links = list(
        SubmissionAttachment.objects.select_for_update()
        .filter(submission=submission)
        .order_by('attachment_id')
    )
    revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=attempt_count + 1,
        submitted_version=expected_version,
        submitted_text=submission.draft_text,
        submitted_at=now,
        is_late=now > assignment.due_at,
    )
    SubmissionAttachment.objects.filter(
        pk__in=[link.pk for link in draft_links],
    ).update(
        submission=None,
        revision=revision,
    )
    receipt.response_payload = {
        'submission_id': revision.submission_id,
        'revision_id': revision.pk,
        'revision_number': revision.revision_number,
        'draft_version': revision.submitted_version,
        'submitted_at': revision.submitted_at.isoformat(),
        'is_late': revision.is_late,
        'attachment_ids': attachment_ids,
    }
    receipt.save(update_fields=('response_payload',))
    return receipt.response_payload


@transaction.atomic
def return_work(submission_id, teacher, *, reason):
    submission_ref = (
        Submission.objects.select_related('assignment__topic')
        .filter(pk=submission_id)
        .first()
    )
    if submission_ref is None:
        raise NotFound()
    assignment = (
        Assignment.objects.select_for_update()
        .filter(
            pk=submission_ref.assignment_id,
            state=Assignment.State.PUBLISHED,
            topic__state='published',
            topic__archived_at__isnull=True,
        )
        .first()
    )
    if assignment is None:
        raise NotFound()
    today = timezone.localdate()
    is_assigned_teacher = TeachingAssignment.objects.filter(
        teacher=teacher,
        course_id=assignment.topic.course_id,
        start_date__lte=today,
    ).filter(
        Q(end_date__gte=today) | Q(end_date__isnull=True),
    ).exists()
    if not is_assigned_teacher:
        raise NotFound()
    submission = (
        Submission.objects.select_for_update()
        .filter(pk=submission_id, assignment=assignment)
        .first()
    )
    if submission is None:
        raise NotFound()
    revision = (
        SubmissionRevision.objects.select_for_update()
        .filter(submission=submission)
        .order_by('-revision_number')
        .first()
    )
    if revision is None:
        raise serializers.ValidationError(
            {'detail': 'Qaytarish uchun yuborilgan revision topilmadi.'}
        )
    if SubmissionReturn.objects.filter(revision=revision).exists():
        raise serializers.ValidationError(
            {'detail': 'Bu revision avval qaytarilgan.'}
        )

    returned = SubmissionReturn.objects.create(
        revision=revision,
        returned_by=teacher,
        reason=reason,
        returned_at=timezone.now(),
    )
    Submission.objects.filter(pk=submission.pk).update(
        draft_text=revision.submitted_text,
        draft_version=F('draft_version') + 1,
        updated_at=timezone.now(),
    )
    submission.refresh_from_db(fields=('draft_version',))
    return {
        'submission_id': submission.pk,
        'revision_id': revision.pk,
        'revision_number': revision.revision_number,
        'draft_version': submission.draft_version,
        'returned_at': returned.returned_at.isoformat(),
        'reason': returned.reason,
    }


@transaction.atomic
def save_grade_draft(
    revision_id,
    grader,
    *,
    expected_version,
    feedback,
    criterion_scores,
):
    revision = (
        SubmissionRevision.objects.select_for_update()
        .select_related('submission__assignment__topic')
        .filter(
            pk=revision_id,
            submission__assignment__state=Assignment.State.PUBLISHED,
            submission__assignment__topic__state='published',
            submission__assignment__topic__archived_at__isnull=True,
        )
        .first()
    )
    if revision is None:
        raise NotFound()
    criteria = list(
        Criterion.objects.select_for_update()
        .filter(assignment=revision.submission.assignment)
        .order_by('pk')
    )
    criteria_by_id = {criterion.pk: criterion for criterion in criteria}
    submitted_ids = [item['criterion_id'] for item in criterion_scores]
    if (
        len(submitted_ids) != len(set(submitted_ids))
        or set(submitted_ids) != set(criteria_by_id)
    ):
        raise serializers.ValidationError({
            'criterion_scores': (
                'Barcha rubric criterion’lari uchun bittadan ball yuboring.'
            )
        })
    total = Decimal('0.00')
    for item in criterion_scores:
        criterion = criteria_by_id[item['criterion_id']]
        if item['points'] < 0 or item['points'] > criterion.max_points:
            raise serializers.ValidationError({
                'criterion_scores': (
                    f'{criterion.label} uchun ball 0 va '
                    f'{criterion.max_points} oralig‘ida bo‘lishi kerak.'
                )
            })
        total += item['points']

    grade = (
        Grade.objects.select_for_update()
        .filter(revision=revision)
        .first()
    )
    if grade is None:
        if expected_version != 0:
            raise StaleGradeVersion()
        grade = Grade.objects.create(
            revision=revision,
            grader=grader,
            state=Grade.State.DRAFT,
            total=total,
            feedback=feedback,
            version=1,
        )
    else:
        if grade.state != Grade.State.DRAFT:
            raise serializers.ValidationError({
                'detail': 'Faqat draft bahoni tahrirlash mumkin.'
            })
        if grade.version != expected_version:
            raise StaleGradeVersion()
        grade.grader = grader
        grade.total = total
        grade.feedback = feedback
        grade.version += 1
        grade.save(update_fields=(
            'grader',
            'total',
            'feedback',
            'version',
            'updated_at',
        ))

    grade.criterion_scores.all().delete()
    CriterionScore.objects.bulk_create([
        CriterionScore(
            grade=grade,
            criterion_id=item['criterion_id'],
            points=item['points'],
            feedback=item['feedback'],
        )
        for item in criterion_scores
    ])
    return Grade.objects.prefetch_related(
        'criterion_scores',
    ).get(pk=grade.pk)


@transaction.atomic
def publish_grade(
    revision_id,
    teacher,
    *,
    expected_grade_version,
    expected_revision_version,
):
    revision = (
        SubmissionRevision.objects.select_for_update()
        .select_related('submission__assignment__topic')
        .filter(
            pk=revision_id,
            submission__assignment__state=Assignment.State.PUBLISHED,
            submission__assignment__topic__state='published',
            submission__assignment__topic__archived_at__isnull=True,
        )
        .first()
    )
    if revision is None:
        raise NotFound()
    today = timezone.localdate()
    if not TeachingAssignment.objects.filter(
        teacher=teacher,
        course_id=revision.submission.assignment.topic.course_id,
        can_grade=True,
        start_date__lte=today,
    ).filter(
        Q(end_date__gte=today) | Q(end_date__isnull=True),
    ).exists():
        raise NotFound()
    if revision.submitted_version != expected_revision_version:
        raise StaleGradeRevisionVersion()

    grade = (
        Grade.objects.select_for_update()
        .filter(revision=revision)
        .first()
    )
    if grade is None:
        raise NotFound()
    if grade.state == Grade.State.PUBLISHED:
        return Grade.objects.prefetch_related(
            'criterion_scores',
        ).get(pk=grade.pk)
    if grade.version != expected_grade_version:
        raise StaleGradeVersion()

    assignment = revision.submission.assignment
    criteria = list(
        Criterion.objects.select_for_update()
        .filter(assignment=assignment)
        .order_by('pk')
    )
    scores = list(
        CriterionScore.objects.select_for_update()
        .filter(grade=grade)
        .order_by('criterion_id')
    )
    criteria_by_id = {criterion.pk: criterion for criterion in criteria}
    rubric_total = sum(
        (criterion.max_points for criterion in criteria),
        Decimal('0.00'),
    )
    if rubric_total != assignment.max_score:
        raise serializers.ValidationError({
            'rubric': 'Rubric yig‘indisi assignment max_score qiymatiga mos emas.'
        })
    scores_by_id = {score.criterion_id: score for score in scores}
    if not criteria or set(criteria_by_id) != set(scores_by_id):
        raise serializers.ValidationError({
            'criterion_scores': (
                'Publish uchun barcha rubric criterion’lariga ball kerak.'
            )
        })
    computed_total = Decimal('0.00')
    for criterion_id, score in scores_by_id.items():
        criterion = criteria_by_id[criterion_id]
        if score.points < 0 or score.points > criterion.max_points:
            raise serializers.ValidationError({
                'criterion_scores': (
                    f'{criterion.label} uchun ball rubric limitidan tashqarida.'
                )
            })
        computed_total += score.points
    if (
        grade.total is None
        or grade.total != computed_total
        or computed_total > assignment.max_score
    ):
        raise serializers.ValidationError({
            'total': 'Grade.total server hisoblagan rubric yig‘indisiga mos emas.'
        })

    now = timezone.now()
    previous_total = grade.total
    grade.state = Grade.State.PUBLISHED
    grade.published_at = now
    grade.grader = teacher
    grade.version += 1
    grade.save(update_fields=(
        'state',
        'published_at',
        'grader',
        'version',
        'updated_at',
    ))
    GradeRevision.objects.create(
        grade=grade,
        changed_by=teacher,
        old_total=previous_total,
        new_total=grade.total,
        reason='Bahoni pupilga e’lon qilish.',
    )
    return Grade.objects.prefetch_related(
        'criterion_scores',
    ).get(pk=grade.pk)


@transaction.atomic
def regrade_submission(
    revision_id,
    teacher,
    *,
    expected_version,
    criterion_scores,
    reason,
):
    revision = (
        SubmissionRevision.objects.select_for_update()
        .select_related('submission__assignment__topic')
        .filter(
            pk=revision_id,
            submission__assignment__state=Assignment.State.PUBLISHED,
            submission__assignment__topic__state='published',
            submission__assignment__topic__archived_at__isnull=True,
        )
        .first()
    )
    if revision is None:
        raise NotFound()
    today = timezone.localdate()
    if not TeachingAssignment.objects.filter(
        teacher=teacher,
        course_id=revision.submission.assignment.topic.course_id,
        can_grade=True,
        start_date__lte=today,
    ).filter(
        Q(end_date__gte=today) | Q(end_date__isnull=True),
    ).exists():
        raise NotFound()

    grade = (
        Grade.objects.select_for_update()
        .filter(revision=revision)
        .first()
    )
    if grade is None or grade.state != Grade.State.PUBLISHED:
        raise NotFound()
    if grade.version != expected_version:
        raise StaleGradeVersion()

    assignment = revision.submission.assignment
    criteria = list(
        Criterion.objects.select_for_update()
        .filter(assignment=assignment)
        .order_by('pk')
    )
    criteria_by_id = {criterion.pk: criterion for criterion in criteria}
    submitted_ids = [item['criterion_id'] for item in criterion_scores]
    if (
        len(submitted_ids) != len(set(submitted_ids))
        or set(submitted_ids) != set(criteria_by_id)
    ):
        raise serializers.ValidationError({
            'criterion_scores': (
                'Barcha rubric criterion’lari uchun bittadan ball yuboring.'
            )
        })

    total = Decimal('0.00')
    for item in criterion_scores:
        criterion = criteria_by_id[item['criterion_id']]
        if item['points'] < 0 or item['points'] > criterion.max_points:
            raise serializers.ValidationError({
                'criterion_scores': (
                    f'{criterion.label} uchun ball 0 va '
                    f'{criterion.max_points} oralig‘ida bo‘lishi kerak.'
                )
            })
        total += item['points']
    if sum(
        (criterion.max_points for criterion in criteria),
        Decimal('0.00'),
    ) != assignment.max_score or total > assignment.max_score:
        raise serializers.ValidationError({
            'total': 'Rubric yig‘indisi assignment max_score qiymatiga mos emas.'
        })

    old_scores = list(
        CriterionScore.objects.select_for_update()
        .filter(grade=grade)
        .order_by('criterion_id')
    )
    old_snapshot = {
        'state': grade.state,
        'total': str(grade.total) if grade.total is not None else None,
        'version': grade.version,
        'feedback': grade.feedback,
        'grader_id': grade.grader_id,
        'published_at': (
            grade.published_at.isoformat() if grade.published_at else None
        ),
        'criterion_scores': [
            {
                'criterion_id': score.criterion_id,
                'points': str(score.points),
                'feedback': score.feedback,
            }
            for score in old_scores
        ],
    }

    now = timezone.now()
    old_total = grade.total
    grade.total = total
    grade.grader = teacher
    grade.version += 1
    grade.published_at = now
    grade.save(update_fields=(
        'total',
        'grader',
        'version',
        'published_at',
        'updated_at',
    ))
    grade.criterion_scores.all().delete()
    CriterionScore.objects.bulk_create([
        CriterionScore(
            grade=grade,
            criterion_id=item['criterion_id'],
            points=item['points'],
            feedback=item['feedback'],
        )
        for item in criterion_scores
    ])
    new_snapshot = {
        'state': grade.state,
        'total': str(total),
        'version': grade.version,
        'feedback': grade.feedback,
        'grader_id': teacher.pk,
        'published_at': now.isoformat(),
        'criterion_scores': [
            {
                'criterion_id': item['criterion_id'],
                'points': str(item['points']),
                'feedback': item['feedback'],
            }
            for item in sorted(
                criterion_scores,
                key=lambda score: score['criterion_id'],
            )
        ],
    }
    GradeRevision.objects.create(
        grade=grade,
        changed_by=teacher,
        old_total=old_total,
        new_total=total,
        old_snapshot=old_snapshot,
        new_snapshot=new_snapshot,
        reason=reason,
    )
    return Grade.objects.prefetch_related(
        'criterion_scores',
    ).get(pk=grade.pk)


@transaction.atomic
def replace_assignment_rubric(assignment_id, criteria_data):
    assignment = (
        Assignment.objects.select_for_update()
        .filter(pk=assignment_id, state=Assignment.State.DRAFT)
        .first()
    )
    if assignment is None:
        raise NotFound()
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(
                'SET CONSTRAINTS '
                'assignments_criterion_position_unique, '
                'assignments_level_position_unique DEFERRED'
            )

    criteria = list(
        Criterion.objects.select_for_update().filter(
            assignment=assignment,
        )
    )
    criteria_by_id = {criterion.pk: criterion for criterion in criteria}
    retained_criterion_ids = {
        item['id'] for item in criteria_data if 'id' in item
    }
    Criterion.objects.filter(
        assignment=assignment,
    ).exclude(pk__in=retained_criterion_ids).delete()

    for criterion_data in criteria_data:
        levels_data = criterion_data['levels']
        criterion_id = criterion_data.get('id')
        if criterion_id is None:
            criterion = Criterion.objects.create(
                assignment=assignment,
                label=criterion_data['label'],
                max_points=criterion_data['max_points'],
                position=criterion_data['position'],
            )
        else:
            criterion = criteria_by_id[criterion_id]
            criterion.label = criterion_data['label']
            criterion.max_points = criterion_data['max_points']
            criterion.position = criterion_data['position']
            criterion.save(update_fields=('label', 'max_points', 'position'))

        existing_levels = {
            level.pk: level
            for level in Level.objects.select_for_update().filter(
                criterion=criterion,
            )
        }
        retained_level_ids = {
            item['id'] for item in levels_data if 'id' in item
        }
        Level.objects.filter(
            criterion=criterion,
        ).exclude(pk__in=retained_level_ids).delete()

        for level_data in levels_data:
            level_id = level_data.get('id')
            if level_id is None:
                Level.objects.create(
                    criterion=criterion,
                    description=level_data['description'],
                    points=level_data['points'],
                    position=level_data['position'],
                )
            else:
                level = existing_levels[level_id]
                level.description = level_data['description']
                level.points = level_data['points']
                level.position = level_data['position']
                level.save(
                    update_fields=('description', 'points', 'position'),
                )

    return assignment
