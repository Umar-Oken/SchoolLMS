from decimal import Decimal
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from assessments.models import (
    Exam,
    ExamItem,
    ExamPublishAudit,
    ExamSnapshotItem,
    ExamSnapshotOption,
    Option,
    Question,
)


@transaction.atomic
def publish_exam(*, exam_id, actor):
    exam = Exam.objects.select_for_update().get(pk=exam_id)
    if exam.state == Exam.State.PUBLISHED:
        return exam

    errors = {}
    if exam.opens_at >= exam.closes_at:
        errors['closes_at'] = 'closes_at opens_at dan keyin bo‘lishi kerak.'
    if exam.duration <= timedelta(0):
        errors['duration'] = 'duration musbat bo‘lishi kerak.'
    if exam.duration > exam.closes_at - exam.opens_at:
        errors['duration'] = 'duration exam oynasidan katta bo‘lishi mumkin emas.'
    if exam.attempt_limit <= 0:
        errors['attempt_limit'] = 'attempt_limit musbat bo‘lishi kerak.'
    if exam.result_release_at is None or exam.result_release_at < exam.closes_at:
        errors['result_release_at'] = (
            'result_release_at closes_at dan oldin bo‘lishi mumkin emas.'
        )
    if errors:
        raise ValidationError(errors)

    items = list(
        ExamItem.objects.select_for_update().filter(exam=exam).order_by(
            'position', 'pk',
        )
    )
    if not items:
        raise ValidationError({'items': 'Publish uchun kamida bitta savol kerak.'})

    questions = {
        question.pk: question
        for question in Question.objects.select_for_update().filter(
            pk__in=[item.question_id for item in items],
        )
    }
    options_by_question = {}
    for option in Option.objects.select_for_update().filter(
        question_id__in=questions,
    ).order_by('question_id', 'pk'):
        options_by_question.setdefault(option.question_id, []).append(option)

    total_points = Decimal('0')
    snapshot_payload = []
    for item in items:
        question = questions.get(item.question_id)
        if question is None or question.course_id != exam.course_id:
            raise ValidationError({'items': 'Savol exam course’iga tegishli bo‘lishi kerak.'})
        if question.state == Question.State.ARCHIVED:
            raise ValidationError({'items': 'Arxivlangan savol publish qilinmaydi.'})
        if question.version != item.question_version:
            raise ValidationError({
                'items': 'Exam item question versioni bankdagi versiyaga mos emas.',
            })
        options = options_by_question.get(question.pk, [])
        if question.type == Question.Type.SINGLE_CHOICE:
            if sum(option.is_correct for option in options) != 1:
                raise ValidationError({
                    'items': 'Single-choice savolda aynan bitta correct option kerak.',
                })
        elif question.type == Question.Type.ESSAY:
            if options:
                raise ValidationError({
                    'items': 'Essay savolda option bo‘lmasligi kerak.',
                })
        else:
            raise ValidationError({'items': 'Noma’lum savol turi.'})
        total_points += item.points
        snapshot_payload.append((item, question, options))

    if total_points <= 0:
        raise ValidationError({'items': 'Umumiy max ball musbat bo‘lishi kerak.'})

    published_at = timezone.now()
    snapshot_items = ExamSnapshotItem.objects.bulk_create([
        ExamSnapshotItem(
            exam=exam,
            source_question_id=question.pk,
            source_question_version=question.version,
            stem=question.stem,
            type=question.type,
            marking_note=question.marking_note,
            points=item.points,
            position=item.position,
        )
        for item, question, _ in snapshot_payload
    ])
    snapshot_options = []
    for snapshot_item, (_, _, options) in zip(snapshot_items, snapshot_payload):
        snapshot_options.extend(
            ExamSnapshotOption(
                snapshot_item=snapshot_item,
                text=option.text,
                is_correct=option.is_correct,
                position=position,
            )
            for position, option in enumerate(options)
        )
    ExamSnapshotOption.objects.bulk_create(snapshot_options)
    exam.state = Exam.State.PUBLISHED
    exam.published_at = published_at
    exam.save(update_fields=('state', 'published_at'))
    ExamPublishAudit.objects.create(
        exam=exam,
        actor=actor,
        total_points=total_points,
        published_at=published_at,
    )
    return exam
