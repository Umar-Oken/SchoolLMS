import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class Question(models.Model):
    class Type(models.TextChoices):
        SINGLE_CHOICE = 'single_choice', 'Single choice'
        ESSAY = 'essay', 'Essay'

    class State(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED = 'archived', 'Archived'

    course = models.ForeignKey(
        'academics.CourseOffering',
        on_delete=models.PROTECT,
        related_name='questions',
    )
    stem = models.TextField()
    family_id = models.UUIDField(default=uuid.uuid4, editable=False)
    type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.SINGLE_CHOICE,
    )
    version = models.PositiveSmallIntegerField(default=1)
    marking_note = models.TextField(blank=True, default='')
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.DRAFT,
    )
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ('pk',)
        constraints = [
            models.UniqueConstraint(
                fields=('family_id', 'version'),
                name='assessments_question_family_version_unique',
            ),
        ]

    def __str__(self):
        return self.stem[:80]


class Option(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='options',
    )
    text = models.TextField()
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ('pk',)

    def __str__(self):
        return self.text[:80]


class Exam(models.Model):
    class State(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'

    course = models.ForeignKey(
        'academics.CourseOffering',
        on_delete=models.PROTECT,
        related_name='exams',
    )
    term = models.ForeignKey(
        'academics.Term',
        on_delete=models.PROTECT,
        related_name='exams',
    )
    title = models.CharField(max_length=200)
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    duration = models.DurationField()
    attempt_limit = models.PositiveSmallIntegerField()
    result_release_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.DRAFT,
    )
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('opens_at', 'pk')
        constraints = [
            models.CheckConstraint(
                condition=Q(opens_at__lt=F('closes_at')),
                name='assessments_exam_opens_before_closes',
            ),
            models.CheckConstraint(
                condition=Q(duration__gt=timedelta(0)),
                name='assessments_exam_duration_positive',
            ),
            models.CheckConstraint(
                condition=Q(attempt_limit__gt=0),
                name='assessments_exam_attempt_limit_positive',
            ),
        ]

    def clean(self):
        super().clean()
        if self.course_id and self.term_id and (
            self.course.school_class.year_id != self.term.year_id
        ):
            raise ValidationError({
                'term': 'Term va course bir o‘quv yiliga tegishli bo‘lishi kerak.'
            })


class ExamItem(models.Model):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name='items',
    )
    question = models.ForeignKey(
        Question,
        on_delete=models.PROTECT,
        related_name='exam_references',
    )
    question_version = models.PositiveSmallIntegerField()
    points = models.DecimalField(max_digits=8, decimal_places=2)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ('position', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('exam', 'position'),
                name='assessments_exam_item_position_unique',
            ),
            models.CheckConstraint(
                condition=Q(question_version__gt=0),
                name='assessments_exam_item_question_version_positive',
            ),
            models.CheckConstraint(
                condition=Q(points__gt=0),
                name='assessments_exam_item_points_positive',
            ),
        ]


class ExamPublishAudit(models.Model):
    exam = models.OneToOneField(
        Exam,
        on_delete=models.PROTECT,
        related_name='publish_audit',
    )
    actor = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='exam_publish_actions',
    )
    total_points = models.DecimalField(max_digits=8, decimal_places=2)
    published_at = models.DateTimeField()


class ExamSnapshotItem(models.Model):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name='snapshot_items',
    )
    source_question_id = models.PositiveBigIntegerField()
    source_question_version = models.PositiveSmallIntegerField()
    stem = models.TextField()
    type = models.CharField(max_length=20, choices=Question.Type.choices)
    marking_note = models.TextField(blank=True, default='')
    points = models.DecimalField(max_digits=8, decimal_places=2)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ('position', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('exam', 'position'),
                name='assessments_snapshot_item_position_unique',
            ),
            models.CheckConstraint(
                condition=Q(source_question_version__gt=0),
                name='assessments_snapshot_question_version_positive',
            ),
            models.CheckConstraint(
                condition=Q(points__gt=0),
                name='assessments_snapshot_item_points_positive',
            ),
        ]


class ExamSnapshotOption(models.Model):
    snapshot_item = models.ForeignKey(
        ExamSnapshotItem,
        on_delete=models.CASCADE,
        related_name='options',
    )
    text = models.TextField()
    is_correct = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ('position', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('snapshot_item', 'position'),
                name='assessments_snapshot_option_position_unique',
            ),
        ]


class Attempt(models.Model):
    class State(models.TextChoices):
        ACTIVE = 'active', 'Active'
        FINISHED = 'finished', 'Finished'

    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name='attempts')
    pupil = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='exam_attempts')
    enrollment = models.ForeignKey('academics.Enrollment', on_delete=models.PROTECT, related_name='exam_attempts')
    attempt_number = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)

    class Meta:
        ordering = ('attempt_number', 'pk')
        constraints = [
            models.UniqueConstraint(fields=('exam', 'pupil', 'attempt_number'), name='assessments_attempt_number_unique'),
            models.UniqueConstraint(fields=('exam', 'pupil'), condition=Q(state='active'), name='assessments_one_active_attempt'),
            models.CheckConstraint(condition=Q(attempt_number__gt=0), name='assessments_attempt_number_positive'),
            models.CheckConstraint(condition=Q(expires_at__gt=F('started_at')), name='assessments_attempt_expires_after_start'),
        ]


class AttemptItem(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name='items')
    source_snapshot_item_id = models.PositiveBigIntegerField()
    stem = models.TextField()
    type = models.CharField(max_length=20, choices=Question.Type.choices)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ('position', 'pk')
        constraints = [models.UniqueConstraint(fields=('attempt', 'position'), name='assessments_attempt_item_position_unique')]


class AttemptOption(models.Model):
    attempt_item = models.ForeignKey(AttemptItem, on_delete=models.CASCADE, related_name='options')
    text = models.TextField()
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ('position', 'pk')
        constraints = [models.UniqueConstraint(fields=('attempt_item', 'position'), name='assessments_attempt_option_position_unique')]


class Answer(models.Model):
    attempt_item = models.OneToOneField(
        AttemptItem,
        on_delete=models.CASCADE,
        related_name='answer',
    )
    selected_option = models.ForeignKey(
        AttemptOption,
        on_delete=models.PROTECT,
        related_name='answers',
        blank=True,
        null=True,
    )
    essay_text = models.TextField(blank=True, default='')
    version = models.PositiveIntegerField(default=1)
    saved_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(version__gt=0),
                name='assessments_answer_version_positive',
            ),
        ]


class AttemptResult(models.Model):
    class Status(models.TextChoices):
        FINALIZED = 'finalized', 'Finalized'
        PENDING_MANUAL = 'pending_manual', 'Pending manual'

    attempt = models.OneToOneField(
        Attempt,
        on_delete=models.PROTECT,
        related_name='result',
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    objective_score = models.DecimalField(max_digits=8, decimal_places=2)
    objective_max_score = models.DecimalField(max_digits=8, decimal_places=2)
    manual_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    manual_max_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    total_score = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
    )
    total_max_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    finished_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(objective_score__gte=0),
                name='assessments_result_score_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(objective_max_score__gte=0),
                name='assessments_result_max_score_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(objective_score__lte=F('objective_max_score')),
                name='assessments_result_score_within_max',
            ),
            models.CheckConstraint(
                condition=Q(manual_score__gte=0),
                name='assessments_result_manual_score_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(manual_max_score__gte=0),
                name='assessments_result_manual_max_score_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(manual_score__lte=F('manual_max_score')),
                name='assessments_result_manual_score_within_max',
            ),
            models.CheckConstraint(
                condition=Q(total_max_score__gte=0),
                name='assessments_result_total_max_score_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(total_score__isnull=True) | Q(total_score__gte=0),
                name='assessments_result_total_score_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(total_score__isnull=True) | Q(total_score__lte=F('total_max_score')),
                name='assessments_result_total_score_within_max',
            ),
        ]


class AttemptGrade(models.Model):
    attempt_item = models.OneToOneField(
        AttemptItem,
        on_delete=models.PROTECT,
        related_name='manual_grade',
    )
    grader = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='attempt_grades_given',
    )
    points = models.DecimalField(max_digits=8, decimal_places=2)
    feedback = models.TextField(blank=True, default='')
    version = models.PositiveIntegerField(default=1)
    graded_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(points__gte=0),
                name='assessments_attempt_grade_points_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(version__gt=0),
                name='assessments_attempt_grade_version_positive',
            ),
        ]


class AttemptGradeAudit(models.Model):
    grade = models.ForeignKey(
        AttemptGrade,
        on_delete=models.PROTECT,
        related_name='audits',
    )
    changed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='attempt_grade_audits',
    )
    old_points = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
    )
    new_points = models.DecimalField(max_digits=8, decimal_places=2)
    old_feedback = models.TextField(blank=True, default='')
    new_feedback = models.TextField(blank=True, default='')
    version = models.PositiveIntegerField()
    changed_at = models.DateTimeField()

    class Meta:
        ordering = ('version', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('grade', 'version'),
                name='assessments_attempt_grade_audit_version_unique',
            ),
            models.CheckConstraint(
                condition=Q(new_points__gte=0),
                name='assessments_attempt_grade_audit_points_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(version__gt=0),
                name='assessments_attempt_grade_audit_version_positive',
            ),
        ]
