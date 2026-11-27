from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from academics.models import Enrollment, TeachingAssignment
from scheduling.models import (
    Attendance,
    AttendanceAudit,
    LessonSession,
    ScheduleException,
    TimetableLock,
    WeeklySlot,
)


SCHOOL_TIME_ZONE = ZoneInfo('Asia/Tashkent')
MAX_MATERIALIZATION_DAYS = 31


@transaction.atomic
def save_session_attendance(*, session_id, actor, rows):
    session = (
        LessonSession.objects.select_for_update()
        .select_related('slot', 'slot__course')
        .get(pk=session_id)
    )
    if session.status == LessonSession.Status.CANCELLED:
        raise DRFValidationError({
            'session': 'Bekor qilingan darsga davomat belgilab bo‘lmaydi.',
        })

    assignment = None
    if (
        actor.role == actor.Role.TEACHER
        and actor.pk == session.effective_teacher_id
    ):
        assignment = TeachingAssignment.objects.select_for_update().filter(
            course_id=session.slot.course_id,
            teacher_id=actor.pk,
            start_date__lte=session.scheduled_date,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=session.scheduled_date),
        ).first()
    if assignment is None:
        raise DRFValidationError({
            'session': 'Faqat shu sanada kursga biriktirilgan teacher davomat belgilaydi.',
        })

    errors = {}
    pupil_ids = [row['pupil'].pk for row in rows]
    if len(pupil_ids) != len(set(pupil_ids)):
        raise DRFValidationError({
            'attendance': 'Bir pupil so‘rovda faqat bir marta bo‘lishi mumkin.',
        })

    enrollments = set(
        Enrollment.objects.select_for_update().filter(
            course_id=session.slot.course_id,
            pupil_id__in=pupil_ids,
            start_date__lte=session.scheduled_date,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=session.scheduled_date),
        ).values_list('pupil_id', flat=True)
    )
    current_records = {
        record.pupil_id: record
        for record in Attendance.objects.select_for_update().filter(
            session=session,
            pupil_id__in=pupil_ids,
        )
    }

    for index, row in enumerate(rows):
        pupil = row['pupil']
        if (
            pupil.role != pupil.Role.PUPIL
            or not pupil.is_active
            or pupil.pk not in enrollments
        ):
            errors[index] = {
                'pupil': 'Pupil dars sanasida ushbu kursga yozilmagan.',
            }
            continue
        current = current_records.get(pupil.pk)
        if (
            current
            and current.status != row['status']
            and not row.get('reason', '').strip()
        ):
            errors[index] = {
                'reason': 'Mavjud davomat holatini o‘zgartirishda sabab majburiy.',
            }

    if errors:
        raise DRFValidationError({'attendance': errors})

    now = timezone.now()
    create_records = []
    update_records = []
    audit_entries = []
    for row in rows:
        pupil = row['pupil']
        current = current_records.get(pupil.pk)
        if current is None:
            create_records.append(Attendance(
                session=session,
                pupil=pupil,
                status=row['status'],
            ))
        elif current.status != row['status']:
            audit_entries.append(AttendanceAudit(
                attendance=current,
                old_status=current.status,
                new_status=row['status'],
                reason=row['reason'].strip(),
                actor=actor,
            ))
            current.status = row['status']
            current.updated_at = now
            update_records.append(current)

    if create_records:
        Attendance.objects.bulk_create(create_records)
    if update_records:
        Attendance.objects.bulk_update(update_records, ('status', 'updated_at'))
    if audit_entries:
        AttendanceAudit.objects.bulk_create(audit_entries)

    saved_records = {
        record.pupil_id: record
        for record in Attendance.objects.filter(
            session=session,
            pupil_id__in=pupil_ids,
        ).select_related('pupil')
    }
    return [saved_records[pupil_id] for pupil_id in pupil_ids]


def _lock_timetable():
    TimetableLock.objects.get_or_create(pk='school')
    return TimetableLock.objects.select_for_update().get(pk='school')


def _bump_timetable_version(lock):
    lock.schedule_version += 1
    lock.save(update_fields=('schedule_version',))


@transaction.atomic
def save_weekly_slot(*, validated_data, instance=None):
    timetable_lock = _lock_timetable()

    if instance is None:
        slot = WeeklySlot(**validated_data)
    else:
        slot = WeeklySlot.objects.select_for_update().get(pk=instance.pk)
        for field, value in validated_data.items():
            setattr(slot, field, value)

    slot.full_clean()
    conflicts = _find_conflicts(slot)
    if conflicts:
        raise ValidationError(conflicts)

    slot.save()
    _bump_timetable_version(timetable_lock)
    return slot


def _find_conflicts(slot):
    overlapping_slots = WeeklySlot.objects.filter(
        weekday=slot.weekday,
        start_time__lt=slot.end_time,
        end_time__gt=slot.start_time,
    )
    if slot.valid_to is None:
        overlapping_slots = overlapping_slots.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=slot.valid_from),
        )
    else:
        overlapping_slots = overlapping_slots.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=slot.valid_from),
            valid_from__lte=slot.valid_to,
        )
    if slot.pk:
        overlapping_slots = overlapping_slots.exclude(pk=slot.pk)

    conflicts = {}
    if overlapping_slots.filter(room_id=slot.room_id).exists():
        conflicts['room'] = 'Bu xona uchun shu vaqtda jadval mavjud.'
    if overlapping_slots.filter(teacher_id=slot.teacher_id).exists():
        conflicts['teacher'] = 'Bu teacher uchun shu vaqtda jadval mavjud.'
    if overlapping_slots.filter(
        course__school_class_id=slot.course.school_class_id,
    ).exists():
        conflicts['course'] = 'Bu sinf uchun shu vaqtda jadval mavjud.'
    return conflicts


@transaction.atomic
def materialize_lessons(*, from_date, to_date):
    timetable_lock = _lock_timetable()
    if from_date > to_date:
        raise ValidationError({
            'to': 'Tugash sanasi boshlanish sanasidan oldin bo‘lishi mumkin emas.',
        })
    if (to_date - from_date).days + 1 > MAX_MATERIALIZATION_DAYS:
        raise ValidationError({
            'to': 'Bir martada ko‘pi bilan 31 kalendar kun yaratiladi.',
        })

    dates = [
        from_date + timedelta(days=offset)
        for offset in range((to_date - from_date).days + 1)
    ]
    weekdays = {local_date.weekday() for local_date in dates}
    slots = WeeklySlot.objects.filter(
        weekday__in=weekdays,
        valid_from__lte=to_date,
    ).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=from_date),
    )

    created_sessions = []
    for slot in slots.iterator():
        for local_date in dates:
            if (
                local_date.weekday() != slot.weekday
                or local_date < slot.valid_from
                or (slot.valid_to is not None and local_date > slot.valid_to)
            ):
                continue
            starts_at = _local_datetime_to_utc(local_date, slot.start_time)
            ends_at = _local_datetime_to_utc(local_date, slot.end_time)
            session, created = LessonSession.objects.get_or_create(
                slot=slot,
                local_date=local_date,
                defaults={
                    'scheduled_date': local_date,
                    'starts_at': starts_at,
                    'ends_at': ends_at,
                },
            )
            if created:
                created_sessions.append(session)
    if created_sessions:
        _bump_timetable_version(timetable_lock)
    return created_sessions


def _local_datetime_to_utc(local_date, local_time):
    local_datetime = datetime.combine(
        local_date,
        local_time,
        tzinfo=SCHOOL_TIME_ZONE,
    )
    return local_datetime.astimezone(datetime_timezone.utc)


@transaction.atomic
def apply_schedule_exception(
    *,
    session_id,
    action,
    actor,
    reason,
    scheduled_date=None,
    start_time=None,
    end_time=None,
    teacher=None,
    room=None,
):
    timetable_lock = _lock_timetable()
    session = (
        LessonSession.objects.select_for_update()
        .select_related('slot', 'slot__course', 'slot__teacher', 'slot__room')
        .get(pk=session_id)
    )
    if session.status == LessonSession.Status.CANCELLED:
        raise ValidationError({
            'session': 'Bekor qilingan darsga qayta o‘zgartirish kiritib bo‘lmaydi.',
        })

    old_date = session.scheduled_date
    old_starts_at = session.starts_at
    old_ends_at = session.ends_at
    old_teacher_id = session.effective_teacher_id
    old_room_id = session.effective_room_id

    if action == ScheduleException.Action.CANCELLED:
        updated_session = _record_schedule_exception(
            session=session,
            action=action,
            actor=actor,
            reason=reason,
            old_date=old_date,
            new_date=old_date,
            old_starts_at=old_starts_at,
            old_ends_at=old_ends_at,
            new_starts_at=old_starts_at,
            new_ends_at=old_ends_at,
            old_teacher_id=old_teacher_id,
            new_teacher_id=old_teacher_id,
            old_room_id=old_room_id,
            new_room_id=old_room_id,
            status=LessonSession.Status.CANCELLED,
            teacher_override=session.teacher_override,
            room_override=session.room_override,
        )
        _bump_timetable_version(timetable_lock)
        return updated_session

    new_teacher = teacher or (
        session.teacher_override
        if session.teacher_override_id
        else session.slot.teacher
    )
    new_room = room or (
        session.room_override
        if session.room_override_id
        else session.slot.room
    )
    if not new_teacher.is_active or new_teacher.role != new_teacher.Role.TEACHER:
        raise ValidationError({
            'teacher': 'O‘rinbosar faol teacher bo‘lishi kerak.',
        })
    if not TeachingAssignment.objects.filter(
        course_id=session.slot.course_id,
        teacher_id=new_teacher.pk,
        start_date__lte=scheduled_date,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=scheduled_date),
    ).exists():
        raise ValidationError({
            'teacher': 'Teacher ko‘chirish sanasida ushbu kursga biriktirilmagan.',
        })

    new_starts_at = _local_datetime_to_utc(scheduled_date, start_time)
    new_ends_at = _local_datetime_to_utc(scheduled_date, end_time)
    candidate_slot = WeeklySlot(
        pk=session.slot_id if scheduled_date == session.local_date else None,
        course=session.slot.course,
        teacher=new_teacher,
        room=new_room,
        weekday=scheduled_date.weekday(),
        start_time=start_time,
        end_time=end_time,
        valid_from=scheduled_date,
        valid_to=scheduled_date,
    )
    slot_conflicts = _find_conflicts(candidate_slot)
    session_conflicts = _find_session_conflicts(
        session=session,
        scheduled_date=scheduled_date,
        starts_at=new_starts_at,
        ends_at=new_ends_at,
        teacher_id=new_teacher.pk,
        room_id=new_room.pk,
    )
    slot_conflicts.update(session_conflicts)
    if slot_conflicts:
        raise ValidationError(slot_conflicts)

    updated_session = _record_schedule_exception(
        session=session,
        action=ScheduleException.Action.MOVED,
        actor=actor,
        reason=reason,
        old_date=old_date,
        new_date=scheduled_date,
        old_starts_at=old_starts_at,
        old_ends_at=old_ends_at,
        new_starts_at=new_starts_at,
        new_ends_at=new_ends_at,
        old_teacher_id=old_teacher_id,
        new_teacher_id=new_teacher.pk,
        old_room_id=old_room_id,
        new_room_id=new_room.pk,
        status=LessonSession.Status.MOVED,
        teacher_override=new_teacher,
        room_override=new_room,
    )
    _bump_timetable_version(timetable_lock)
    return updated_session


def _find_session_conflicts(
    *,
    session,
    scheduled_date,
    starts_at,
    ends_at,
    teacher_id,
    room_id,
):
    conflicts = {}
    sessions = LessonSession.objects.filter(
        scheduled_date=scheduled_date,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).exclude(pk=session.pk).exclude(status=LessonSession.Status.CANCELLED)
    sessions = sessions.select_related(
        'slot',
        'slot__course',
    )
    for existing in sessions:
        if existing.effective_teacher_id == teacher_id:
            conflicts['teacher'] = 'Bu teacher uchun shu vaqtda dars mavjud.'
        if existing.effective_room_id == room_id:
            conflicts['room'] = 'Bu xona uchun shu vaqtda dars mavjud.'
        if (
            existing.slot.course.school_class_id
            == session.slot.course.school_class_id
        ):
            conflicts['course'] = 'Bu sinf uchun shu vaqtda dars mavjud.'
    return conflicts


def _record_schedule_exception(
    *,
    session,
    action,
    actor,
    reason,
    old_date,
    new_date,
    old_starts_at,
    old_ends_at,
    new_starts_at,
    new_ends_at,
    old_teacher_id,
    new_teacher_id,
    old_room_id,
    new_room_id,
    status,
    teacher_override=None,
    room_override=None,
):
    ScheduleException.objects.create(
        session=session,
        action=action,
        old_date=old_date,
        new_date=new_date,
        old_starts_at=old_starts_at,
        old_ends_at=old_ends_at,
        new_starts_at=new_starts_at,
        new_ends_at=new_ends_at,
        old_teacher_id=old_teacher_id,
        new_teacher_id=new_teacher_id,
        old_room_id=old_room_id,
        new_room_id=new_room_id,
        actor=actor,
        reason=reason,
    )
    session.scheduled_date = new_date
    session.starts_at = new_starts_at
    session.ends_at = new_ends_at
    session.teacher_override = teacher_override
    session.room_override = room_override
    session.status = status
    session.save(update_fields=(
        'scheduled_date',
        'starts_at',
        'ends_at',
        'teacher_override',
        'room_override',
        'status',
    ))
    return session
