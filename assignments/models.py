from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Deferrable
from django.db.models import Q
from django.utils import timezone


class Assignment(models.Model):
    class State(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'

    topic = models.ForeignKey(
        'learning.Topic',
        on_delete=models.PROTECT,
        related_name='assignments',
    )
    title = models.CharField(max_length=200)
    instructions = models.TextField()
    available_at = models.DateTimeField()
    due_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    max_score = models.DecimalField(max_digits=8, decimal_places=2)
    max_attempts = models.PositiveSmallIntegerField()
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.DRAFT,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    rubric_version = models.PositiveSmallIntegerField(default=0)
    deadline_version = models.PositiveIntegerField(default=1)

    class Meta:
        indexes = [
            models.Index(
                fields=('topic', 'state'),
                name='assignments_topic_state_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(available_at__lte=models.F('due_at')),
                name='assignments_available_before_due',
            ),
            models.CheckConstraint(
                condition=Q(due_at__lte=models.F('closes_at')),
                name='assignments_due_before_close',
            ),
            models.CheckConstraint(
                condition=Q(max_score__gt=0),
                name='assignments_max_score_positive',
            ),
            models.CheckConstraint(
                condition=Q(max_attempts__gt=0),
                name='assignments_max_attempts_positive',
            ),
            models.CheckConstraint(
                condition=Q(deadline_version__gte=1),
                name='assignments_deadline_version_positive',
            ),
        ]
        ordering = ('available_at', 'pk')


class Criterion(models.Model):
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='criteria',
    )
    label = models.CharField(max_length=200)
    max_points = models.DecimalField(max_digits=8, decimal_places=2)
    position = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('assignment', 'position'),
                name='assignments_criterion_position_unique',
                deferrable=Deferrable.IMMEDIATE,
            ),
            models.CheckConstraint(
                condition=Q(max_points__gt=0),
                name='assignments_criterion_max_points_positive',
            ),
        ]
        ordering = ('position', 'pk')


class Level(models.Model):
    criterion = models.ForeignKey(
        Criterion,
        on_delete=models.CASCADE,
        related_name='levels',
    )
    description = models.TextField()
    points = models.DecimalField(max_digits=8, decimal_places=2)
    position = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('criterion', 'position'),
                name='assignments_level_position_unique',
                deferrable=Deferrable.IMMEDIATE,
            ),
            models.CheckConstraint(
                condition=Q(points__gte=0),
                name='assignments_level_points_nonnegative',
            ),
        ]
        ordering = ('position', 'pk')

    def clean(self):
        super().clean()
        if self.criterion_id is None or self.points is None:
            return

        max_points = Criterion.objects.filter(
            pk=self.criterion_id,
        ).values_list('max_points', flat=True).first()
        if max_points is not None and self.points > max_points:
            raise ValidationError({
                'points': 'Level points cannot exceed its criterion max_points.'
            })


class Submission(models.Model):
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.PROTECT,
        related_name='submissions',
    )
    pupil = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='assignment_submissions',
    )
    attachments = models.ManyToManyField(
        'files.Attachment',
        through='assignments.SubmissionAttachment',
        related_name='submission_drafts',
    )
    draft_text = models.TextField(blank=True, default='')
    draft_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('assignment', 'pupil'),
                name='assignments_submission_assignment_pupil_unique',
            ),
        ]
        ordering = ('assignment_id', 'pupil_id')

    def __str__(self):
        return f'{self.pupil} — {self.assignment}'


class SubmissionRevision(models.Model):
    submission = models.ForeignKey(
        Submission,
        on_delete=models.PROTECT,
        related_name='revisions',
    )
    revision_number = models.PositiveSmallIntegerField()
    submitted_version = models.PositiveIntegerField(default=0)
    submitted_text = models.TextField()
    submitted_at = models.DateTimeField(default=timezone.now)
    is_late = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('submission', 'revision_number'),
                name='assignments_submission_revision_number_unique',
            ),
        ]
        ordering = ('revision_number',)

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('SubmissionRevision yozuvi o‘zgartirilmaydi.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('SubmissionRevision yozuvi o‘chirilmaydi.')

    def __str__(self):
        return f'{self.submission_id} — {self.revision_number}'


class SubmissionIdempotencyReceipt(models.Model):
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.PROTECT,
        related_name='submission_idempotency_receipts',
    )
    pupil = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='submission_idempotency_receipts',
    )
    key = models.CharField(max_length=255)
    input_fingerprint = models.CharField(max_length=64)
    response_payload = models.JSONField()
    response_status = models.PositiveSmallIntegerField(default=201)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('pupil', 'assignment', 'key'),
                name='assignments_submit_key_unique',
            ),
        ]


class SubmissionReturn(models.Model):
    revision = models.OneToOneField(
        SubmissionRevision,
        on_delete=models.PROTECT,
        related_name='return_record',
    )
    returned_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='returned_submission_revisions',
    )
    reason = models.TextField()
    returned_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f'{self.revision_id} — {self.returned_by_id}'


class Grade(models.Model):
    class State(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'

    revision = models.OneToOneField(
        SubmissionRevision,
        on_delete=models.PROTECT,
        related_name='grade',
    )
    grader = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='grades_given',
    )
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.DRAFT,
    )
    total = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    feedback = models.TextField(blank=True, default='')
    version = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.revision_id} — {self.state}'


class CriterionScore(models.Model):
    grade = models.ForeignKey(
        Grade,
        on_delete=models.CASCADE,
        related_name='criterion_scores',
    )
    criterion = models.ForeignKey(
        Criterion,
        on_delete=models.PROTECT,
        related_name='submission_scores',
    )
    points = models.DecimalField(max_digits=8, decimal_places=2)
    feedback = models.TextField(blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('grade', 'criterion'),
                name='assignments_grade_criterion_unique',
            ),
            models.CheckConstraint(
                condition=Q(points__gte=0),
                name='assignments_criterion_score_nonnegative',
            ),
        ]

    def __str__(self):
        return f'{self.grade_id} — {self.criterion_id}: {self.points}'


class GradeRevision(models.Model):
    grade = models.ForeignKey(
        Grade,
        on_delete=models.PROTECT,
        related_name='revisions',
    )
    changed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='grade_revisions',
    )
    old_total = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    new_total = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    old_snapshot = models.JSONField(default=dict)
    new_snapshot = models.JSONField(default=dict)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('GradeRevision yozuvi o‘zgartirilmaydi.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('GradeRevision yozuvi o‘chirilmaydi.')

    def __str__(self):
        return f'{self.grade_id} — {self.created_at}'


class SubmissionAttachment(models.Model):
    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='attachment_links',
    )
    revision = models.ForeignKey(
        SubmissionRevision,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='attachment_links',
    )
    attachment = models.OneToOneField(
        'files.Attachment',
        on_delete=models.PROTECT,
        related_name='submission_link',
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(submission__isnull=False, revision__isnull=True)
                    | Q(submission__isnull=True, revision__isnull=False)
                ),
                name='assignments_submission_attachment_one_parent',
            ),
        ]

    def __str__(self):
        return f'{self.attachment_id} — {self.submission_id or self.revision_id}'
