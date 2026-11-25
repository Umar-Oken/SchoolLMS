from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from accounts.models import User


ATTENDANCE_STATUS_VALUES = ('present', 'absent', 'late', 'excused', 'unmarked')


class TimetableLock(models.Model):
    key = models.CharField(max_length=32, primary_key=True, default='school')
    schedule_version = models.PositiveBigIntegerField(default=1)

    class Meta:
        db_table = 'scheduling_timetable_lock'


class Room(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ('name', 'pk')

    def __str__(self):
        return self.name


class WeeklySlot(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 0, 'Dushanba'
        TUESDAY = 1, 'Seshanba'
        WEDNESDAY = 2, 'Chorshanba'
        THURSDAY = 3, 'Payshanba'
        FRIDAY = 4, 'Juma'
        SATURDAY = 5, 'Shanba'
        SUNDAY = 6, 'Yakshanba'

    course = models.ForeignKey(
        'academics.CourseOffering',
        on_delete=models.PROTECT,
        related_name='weekly_slots',
    )
    teacher = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='weekly_slots',
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name='weekly_slots',
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    valid_from = models.DateField()
    valid_to = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ('weekday', 'start_time', 'pk')
        constraints = [
            models.CheckConstraint(
                condition=Q(start_time__lt=F('end_time')),
                name='scheduling_slot_times_ordered',
            ),
            models.CheckConstraint(
                condition=Q(weekday__gte=0, weekday__lte=6),
                name='scheduling_slot_weekday_valid',
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gte=F('valid_from')),
                name='scheduling_slot_dates_ordered',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.start_time
            and self.end_time
            and self.start_time >= self.end_time
        ):
            errors['end_time'] = (
                'Tugash vaqti boshlanish vaqtidan keyin bo‘lishi kerak.'
            )
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            errors['valid_to'] = (
                'Amal qilish tugash sanasi boshlanish sanasidan oldin '
                'bo‘lishi mumkin emas.'
            )
        if self.teacher_id:
            if self.teacher.role != User.Role.TEACHER:
                errors['teacher'] = (
                    'Faqat teacher rolidagi foydalanuvchini tanlash mumkin.'
                )
            elif self.course_id and not self.course.teaching_assignments.filter(
                teacher_id=self.teacher_id,
            ).exists():
                errors['teacher'] = 'Teacher ushbu kursga biriktirilmagan.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.course} — {self.get_weekday_display()} {self.start_time}'


class LessonSession(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Rejalashtirilgan'
        MOVED = 'moved', 'Ko‘chirilgan'
        CANCELLED = 'cancelled', 'Bekor qilingan'

    slot = models.ForeignKey(
        WeeklySlot,
        on_delete=models.PROTECT,
        related_name='sessions',
    )
    local_date = models.DateField()
    scheduled_date = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    teacher_override = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='replacement_lesson_sessions',
    )
    room_override = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='replacement_lesson_sessions',
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    class Meta:
        ordering = ('starts_at', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('slot', 'local_date'),
                name='scheduling_session_slot_local_date_unique',
            ),
            models.CheckConstraint(
                condition=Q(starts_at__lt=F('ends_at')),
                name='scheduling_session_times_ordered',
            ),
        ]

    def __str__(self):
        return f'{self.slot} — {self.local_date}'

    @property
    def effective_teacher_id(self):
        return self.teacher_override_id or self.slot.teacher_id

    @property
    def effective_room_id(self):
        return self.room_override_id or self.slot.room_id


class ScheduleException(models.Model):
    class Action(models.TextChoices):
        MOVED = 'moved', 'Ko‘chirilgan'
        CANCELLED = 'cancelled', 'Bekor qilingan'

    session = models.ForeignKey(
        LessonSession,
        on_delete=models.PROTECT,
        related_name='exceptions',
    )
    action = models.CharField(max_length=10, choices=Action.choices)
    old_date = models.DateField()
    new_date = models.DateField()
    old_starts_at = models.DateTimeField()
    old_ends_at = models.DateTimeField()
    new_starts_at = models.DateTimeField()
    new_ends_at = models.DateTimeField()
    old_teacher = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='schedule_exceptions_as_old_teacher',
    )
    new_teacher = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='schedule_exceptions_as_new_teacher',
    )
    old_room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name='schedule_exceptions_as_old_room',
    )
    new_room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name='schedule_exceptions_as_new_room',
    )
    actor = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='schedule_exceptions_made',
    )
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')
        constraints = [
            models.CheckConstraint(
                condition=Q(old_starts_at__lt=F('old_ends_at')),
                name='scheduling_exception_old_times_ordered',
            ),
            models.CheckConstraint(
                condition=Q(new_starts_at__lt=F('new_ends_at')),
                name='scheduling_exception_new_times_ordered',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('ScheduleException yozuvi o‘zgartirilmaydi.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('ScheduleException yozuvi o‘chirilmaydi.')


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = 'present', 'Kelgan'
        ABSENT = 'absent', 'Kelmagan'
        LATE = 'late', 'Kechikkan'
        EXCUSED = 'excused', 'Sababli'
        UNMARKED = 'unmarked', 'Belgilanmagan'

    session = models.ForeignKey(
        LessonSession,
        on_delete=models.PROTECT,
        related_name='attendance_records',
    )
    pupil = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='attendance_records',
    )
    status = models.CharField(max_length=10, choices=Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('pupil_id', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('session', 'pupil'),
                name='scheduling_attendance_session_pupil_unique',
            ),
            models.CheckConstraint(
                condition=Q(status__in=ATTENDANCE_STATUS_VALUES),
                name='scheduling_attendance_status_valid',
            ),
        ]


class AttendanceAudit(models.Model):
    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.PROTECT,
        related_name='audit_entries',
    )
    old_status = models.CharField(max_length=10, choices=Attendance.Status.choices)
    new_status = models.CharField(max_length=10, choices=Attendance.Status.choices)
    reason = models.TextField()
    actor = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='attendance_changes_made',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(old_status__in=ATTENDANCE_STATUS_VALUES)
                    & Q(new_status__in=ATTENDANCE_STATUS_VALUES)
                ),
                name='scheduling_attendance_audit_status_valid',
            ),
            models.CheckConstraint(
                condition=~Q(old_status=F('new_status')),
                name='scheduling_attendance_audit_status_changed',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('AttendanceAudit yozuvi o‘zgartirilmaydi.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('AttendanceAudit yozuvi o‘chirilmaydi.')
