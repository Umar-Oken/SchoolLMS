from decimal import Decimal
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from assessments.models import (
    Answer,
    Attempt,
    AttemptItem,
    AttemptOption,
    AttemptGrade,
    AttemptGradeAudit,
    AttemptResult,
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


@transaction.atomic
def start_attempt(*, exam_id, pupil):
    now = timezone.now()
    exam = Exam.objects.select_for_update().get(pk=exam_id)
    if exam.state != Exam.State.PUBLISHED or not (exam.opens_at <= now < exam.closes_at):
        raise ValidationError({'detail': 'Exam hozir ochiq emas.'})
    from academics.models import Enrollment, MembershipStatus
    enrollment = Enrollment.objects.select_for_update().filter(
        pupil=pupil, course=exam.course, status=MembershipStatus.ACTIVE,
        end_date__isnull=True,
    ).first()
    if enrollment is None:
        raise ValidationError({'detail': 'Faol enrollment topilmadi.'})
    active = Attempt.objects.filter(exam=exam, pupil=pupil, state=Attempt.State.ACTIVE).first()
    if active is not None:
        return active
    attempt_count = Attempt.objects.filter(exam=exam, pupil=pupil).count()
    if attempt_count >= exam.attempt_limit:
        raise ValidationError({'detail': 'Attempt limiti tugagan.'})
    snapshots = list(ExamSnapshotItem.objects.filter(exam=exam).prefetch_related('options'))
    if not snapshots:
        raise ValidationError({'detail': 'Exam snapshot topilmadi.'})
    attempt = Attempt.objects.create(
        exam=exam, pupil=pupil, enrollment=enrollment,
        attempt_number=attempt_count + 1, started_at=now,
        expires_at=min(now + exam.duration, exam.closes_at),
    )
    items = AttemptItem.objects.bulk_create([
        AttemptItem(attempt=attempt, source_snapshot_item_id=item.pk, stem=item.stem, type=item.type, position=item.position)
        for item in snapshots
    ])
    AttemptOption.objects.bulk_create([
        AttemptOption(attempt_item=attempt_item, text=option.text, position=option.position)
        for attempt_item, snapshot_item in zip(items, snapshots)
        for option in snapshot_item.options.all()
    ])
    return attempt


@transaction.atomic
def save_answer(*, attempt_id, pupil, item_id, validated_data):
    attempt = Attempt.objects.select_for_update().filter(
        pk=attempt_id,
        pupil=pupil,
    ).first()
    if attempt is None:
        raise ValidationError({'detail': 'Attempt topilmadi.'})
    now = timezone.now()
    if attempt.state != Attempt.State.ACTIVE or now >= attempt.expires_at:
        raise ValidationError({'detail': 'Attempt javob qabul qilish uchun faol emas.'})

    item = AttemptItem.objects.select_for_update().filter(
        pk=item_id,
        attempt=attempt,
    ).first()
    if item is None:
        raise ValidationError({'detail': 'Savol bu attemptga tegishli emas.'})

    selected_option = validated_data.get('selected_option')
    if selected_option is not None and selected_option.attempt_item_id != item.pk:
        raise ValidationError({
            'selected_option': 'Option aynan shu attempt savoliga tegishli bo‘lishi kerak.',
        })

    answer = Answer.objects.select_for_update().filter(attempt_item=item).first()
    expected_version = validated_data['expected_version']
    if answer is None:
        if expected_version != 0:
            raise ValidationError({'expected_version': 'Javob versiyasi eskirgan.'})
        answer = Answer.objects.create(
            attempt_item=item,
            selected_option=selected_option,
            essay_text=validated_data.get('essay_text', ''),
            version=1,
            saved_at=now,
        )
        return answer

    if expected_version != answer.version:
        raise ValidationError({'expected_version': 'Javob versiyasi eskirgan.'})
    answer.selected_option = selected_option
    answer.essay_text = validated_data.get('essay_text', '')
    answer.version += 1
    answer.saved_at = now
    answer.save(update_fields=(
        'selected_option', 'essay_text', 'version', 'saved_at',
    ))
    return answer


@transaction.atomic
def finalize_attempt(*, attempt_id, pupil):
    attempt = Attempt.objects.select_for_update().filter(
        pk=attempt_id,
        pupil=pupil,
    ).first()
    if attempt is None:
        raise ValidationError({'detail': 'Attempt topilmadi.'})

    existing_result = AttemptResult.objects.filter(attempt=attempt).first()
    if existing_result is not None:
        return existing_result
    if attempt.state != Attempt.State.ACTIVE:
        raise ValidationError({'detail': 'Attempt yakunlanmaydigan holatda.'})

    items = list(
        AttemptItem.objects.select_for_update().filter(attempt=attempt).order_by(
            'position', 'pk',
        )
    )
    snapshots = {
        snapshot.pk: snapshot
        for snapshot in ExamSnapshotItem.objects.filter(
            exam=attempt.exam,
            pk__in=[item.source_snapshot_item_id for item in items],
        )
    }
    answers = {
        answer.attempt_item_id: answer
        for answer in Answer.objects.select_related('selected_option').filter(
            attempt_item__in=items,
        )
    }
    correct_positions = {}
    for option in ExamSnapshotOption.objects.filter(
        snapshot_item_id__in=snapshots,
        is_correct=True,
    ):
        correct_positions[option.snapshot_item_id] = option.position

    objective_score = Decimal('0')
    objective_max_score = Decimal('0')
    manual_max_score = Decimal('0')
    has_essay = False
    for item in items:
        snapshot = snapshots.get(item.source_snapshot_item_id)
        if snapshot is None:
            raise ValidationError({'detail': 'Attempt snapshoti topilmadi.'})
        if item.type == Question.Type.ESSAY:
            has_essay = True
            manual_max_score += snapshot.points
            continue
        if item.type != Question.Type.SINGLE_CHOICE:
            raise ValidationError({'detail': 'Noma’lum attempt savol turi.'})
        correct_position = correct_positions.get(snapshot.pk)
        if correct_position is None:
            raise ValidationError({'detail': 'Single-choice kaliti topilmadi.'})
        objective_max_score += snapshot.points
        answer = answers.get(item.pk)
        if (
            answer is not None
            and answer.selected_option is not None
            and answer.selected_option.attempt_item_id == item.pk
            and answer.selected_option.position == correct_position
        ):
            objective_score += snapshot.points

    result = AttemptResult.objects.create(
        attempt=attempt,
        status=(
            AttemptResult.Status.PENDING_MANUAL
            if has_essay else AttemptResult.Status.FINALIZED
        ),
        objective_score=objective_score,
        objective_max_score=objective_max_score,
        manual_score=Decimal('0'),
        manual_max_score=manual_max_score,
        total_score=None if has_essay else objective_score,
        total_max_score=objective_max_score + manual_max_score,
        finished_at=timezone.now(),
    )
    attempt.state = Attempt.State.FINISHED
    attempt.save(update_fields=('state',))
    return result


@transaction.atomic
def grade_essay_attempt_item(*, attempt_id, item_id, grader, validated_data):
    attempt = Attempt.objects.select_for_update().filter(pk=attempt_id).first()
    if attempt is None:
        raise ValidationError({'detail': 'Attempt topilmadi.'})
    result = AttemptResult.objects.select_for_update().filter(attempt=attempt).first()
    if result is None or attempt.state != Attempt.State.FINISHED:
        raise ValidationError({'detail': 'Avval attempt yakunlanishi kerak.'})
    item = AttemptItem.objects.select_for_update().filter(
        pk=item_id,
        attempt=attempt,
        type=Question.Type.ESSAY,
    ).first()
    if item is None:
        raise ValidationError({'detail': 'Essay item bu attemptga tegishli emas.'})
    snapshot = ExamSnapshotItem.objects.filter(
        pk=item.source_snapshot_item_id,
        exam=attempt.exam,
        type=Question.Type.ESSAY,
    ).first()
    if snapshot is None:
        raise ValidationError({'detail': 'Essay snapshot topilmadi.'})
    if validated_data['points'] > snapshot.points:
        raise ValidationError({'points': 'Ball item max_score qiymatidan katta.'})

    grade = AttemptGrade.objects.select_for_update().filter(
        attempt_item=item,
    ).first()
    expected_version = validated_data['expected_version']
    now = timezone.now()
    if grade is None:
        if expected_version != 0:
            raise ValidationError({'expected_version': 'Grade versiyasi eskirgan.'})
        grade = AttemptGrade.objects.create(
            attempt_item=item,
            grader=grader,
            points=validated_data['points'],
            feedback=validated_data['feedback'],
            version=1,
            graded_at=now,
        )
        AttemptGradeAudit.objects.create(
            grade=grade, changed_by=grader, old_points=None,
            new_points=grade.points, old_feedback='', new_feedback=grade.feedback,
            version=grade.version, changed_at=now,
        )
    else:
        if expected_version != grade.version:
            raise ValidationError({'expected_version': 'Grade versiyasi eskirgan.'})
        old_points, old_feedback = grade.points, grade.feedback
        grade.grader = grader
        grade.points = validated_data['points']
        grade.feedback = validated_data['feedback']
        grade.version += 1
        grade.graded_at = now
        grade.save(update_fields=(
            'grader', 'points', 'feedback', 'version', 'graded_at',
        ))
        AttemptGradeAudit.objects.create(
            grade=grade, changed_by=grader, old_points=old_points,
            new_points=grade.points, old_feedback=old_feedback,
            new_feedback=grade.feedback, version=grade.version, changed_at=now,
        )

    essay_items = list(AttemptItem.objects.filter(
        attempt=attempt,
        type=Question.Type.ESSAY,
    ))
    snapshots = {
        snapshot.pk: snapshot
        for snapshot in ExamSnapshotItem.objects.filter(
            exam=attempt.exam,
            pk__in=[essay.source_snapshot_item_id for essay in essay_items],
        )
    }
    grades = {
        essay_grade.attempt_item_id: essay_grade
        for essay_grade in AttemptGrade.objects.filter(attempt_item__in=essay_items)
    }
    result.manual_max_score = sum(
        (snapshots[essay.source_snapshot_item_id].points for essay in essay_items),
        Decimal('0'),
    )
    result.manual_score = sum(
        (grades[essay.pk].points for essay in essay_items if essay.pk in grades),
        Decimal('0'),
    )
    if len(grades) == len(essay_items):
        result.status = AttemptResult.Status.FINALIZED
        result.total_score = result.objective_score + result.manual_score
    else:
        result.status = AttemptResult.Status.PENDING_MANUAL
        result.total_score = None
    result.total_max_score = result.objective_max_score + result.manual_max_score
    result.save(update_fields=(
        'status', 'manual_score', 'manual_max_score', 'total_score',
        'total_max_score',
    ))
    return grade
