from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class AcademicStatus(models.TextChoices):
    UPCOMING = 'upcoming', 'Upcoming'
    ACTIVE = 'active', 'Active'
    CLOSED = 'closed', 'Closed'


class AcademicYear(models.Model):
    start_date = models.DateField()
    end_date = models.DateField()
    label = models.CharField(max_length=100)
    status = models.CharField(
        max_length=10,
        choices=AcademicStatus.choices,
        default=AcademicStatus.UPCOMING,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(start_date__lte=F('end_date')),
                name='academics_year_dates_ordered',
            ),
        ]
        ordering = ['start_date', 'id']

    def __str__(self):
        return self.label


class Term(models.Model):
    year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='terms',
    )
    order = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=10,
        choices=AcademicStatus.choices,
        default=AcademicStatus.UPCOMING,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(start_date__lte=F('end_date')),
                name='academics_term_dates_ordered',
            ),
            models.UniqueConstraint(
                fields=['year', 'order'],
                name='academics_term_year_order_unique',
            ),
            models.CheckConstraint(
                condition=Q(order__gte=1),
                name='academics_term_order_positive',
            ),
        ]
        ordering = ['year', 'order']

    def clean(self):
        super().clean()
        if not self.year_id or not self.start_date or not self.end_date:
            return

        if self.start_date < self.year.start_date:
            raise ValidationError({
                'start_date': 'Term must start on or after its academic year starts.'
            })
        if self.end_date > self.year.end_date:
            raise ValidationError({
                'end_date': 'Term must end on or before its academic year ends.'
            })

    def __str__(self):
        return f'{self.year.label} — term {self.order}'


class SchoolClass(models.Model):
    year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='classes',
    )
    grade_level = models.PositiveSmallIntegerField()
    section = models.CharField(max_length=10)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(grade_level__gte=1, grade_level__lte=11),
                name='academics_class_grade_level_valid',
            ),
            models.UniqueConstraint(
                fields=['year', 'grade_level', 'section'],
                name='academics_class_year_grade_section_unique',
            ),
        ]
        ordering = ['year', 'grade_level', 'section']

    def __str__(self):
        return f'{self.grade_level}-{self.section}'


class Subject(models.Model):
    code = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ['title', 'id']

    def __str__(self):
        return self.title


class CourseOffering(models.Model):
    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.PROTECT,
        related_name='course_offerings',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name='course_offerings',
    )
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['school_class', 'subject'],
                name='academics_offering_class_subject_unique',
            ),
        ]
        ordering = ['school_class', 'subject']

    def __str__(self):
        return f'{self.school_class} — {self.subject}'


class TeachingAssignment(models.Model):
    course = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name='teaching_assignments',
    )
    teacher = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='teaching_assignments',
    )
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    can_grade = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F('start_date')),
                name='academics_assignment_dates_ordered',
            ),
        ]
        ordering = ['course', 'start_date', 'id']

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({
                'end_date': 'Tugash sanasi boshlanish sanasidan oldin bo‘lishi mumkin emas.'
            })

    def __str__(self):
        return f'{self.teacher} — {self.course}'


class MembershipStatus(models.TextChoices):
    ACTIVE = 'active', 'Faol'
    CLOSED = 'closed', 'Yopilgan'


class ClassMembership(models.Model):
    pupil = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='class_memberships',
    )
    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.PROTECT,
        related_name='memberships',
    )
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=10,
        choices=MembershipStatus.choices,
        default=MembershipStatus.ACTIVE,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F('start_date')),
                name='academics_membership_dates_ordered',
            ),
            models.CheckConstraint(
                condition=(
                    Q(status=MembershipStatus.ACTIVE, end_date__isnull=True)
                    | Q(status=MembershipStatus.CLOSED, end_date__isnull=False)
                ),
                name='academics_membership_status_dates_match',
            ),
            models.UniqueConstraint(
                fields=['pupil'],
                condition=Q(status=MembershipStatus.ACTIVE),
                name='academics_one_active_class_per_pupil',
            ),
        ]
        ordering = ['pupil', 'start_date', 'id']

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({
                'end_date': 'Tugash sanasi boshlanish sanasidan oldin bo‘lishi mumkin emas.'
            })

    def __str__(self):
        return f'{self.pupil} — {self.school_class}'


class Enrollment(models.Model):
    pupil = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='course_enrollments',
    )
    course = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name='pupil_enrollments',
    )
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=10,
        choices=MembershipStatus.choices,
        default=MembershipStatus.ACTIVE,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F('start_date')),
                name='academics_enrollment_dates_ordered',
            ),
            models.CheckConstraint(
                condition=(
                    Q(status=MembershipStatus.ACTIVE, end_date__isnull=True)
                    | Q(status=MembershipStatus.CLOSED, end_date__isnull=False)
                ),
                name='academics_enrollment_status_dates_match',
            ),
            models.UniqueConstraint(
                fields=['pupil', 'course'],
                condition=Q(status=MembershipStatus.ACTIVE),
                name='academics_one_active_enrollment_per_course',
            ),
        ]
        ordering = ['pupil', 'course', 'start_date', 'id']

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({
                'end_date': 'Tugash sanasi boshlanish sanasidan oldin bo‘lishi mumkin emas.'
            })

    def __str__(self):
        return f'{self.pupil} — {self.course}'
