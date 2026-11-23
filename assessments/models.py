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
