from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


AI_JOB_STATES = (
    'queued',
    'running',
    'review',
    'failed',
    'approved',
    'rejected',
)


class AIJob(models.Model):
    """A versioned request for draft content, never a grade-writing command."""

    class State(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        REVIEW = 'review', 'Review'
        FAILED = 'failed', 'Failed'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    actor = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='ai_jobs',
    )
    course = models.ForeignKey(
        'academics.CourseOffering',
        on_delete=models.PROTECT,
        related_name='ai_jobs',
    )
    resource = models.ForeignKey(
        'learning.Resource',
        on_delete=models.PROTECT,
        related_name='ai_jobs',
    )
    resource_version = models.PositiveSmallIntegerField()
    requested_question_count = models.PositiveSmallIntegerField(default=5)
    idempotency_key = models.CharField(max_length=255, blank=True, default='')
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.QUEUED,
    )
    prompt_version = models.PositiveSmallIntegerField(default=1)
    draft_version = models.PositiveSmallIntegerField(default=1)
    provider_model = models.CharField(max_length=200)
    tokens = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at', '-pk')
        constraints = [
            models.CheckConstraint(
                condition=Q(state__in=AI_JOB_STATES),
                name='ai_job_state_valid',
            ),
            models.CheckConstraint(
                condition=Q(resource_version__gte=1),
                name='ai_job_resource_version_positive',
            ),
            models.CheckConstraint(
                condition=Q(requested_question_count=5),
                name='ai_job_question_count_is_five',
            ),
            models.CheckConstraint(
                condition=Q(prompt_version__gte=1),
                name='ai_job_prompt_version_positive',
            ),
            models.CheckConstraint(
                condition=Q(tokens__gte=0),
                name='ai_job_tokens_nonnegative',
            ),
            models.UniqueConstraint(
                fields=('actor', 'idempotency_key'),
                condition=~Q(idempotency_key=''),
                name='ai_job_actor_idempotency_key_unique',
            ),
        ]

    def clean(self):
        super().clean()
        if self.course_id and self.resource_id:
            resource_course_id = self.resource.topic.course_id
            if resource_course_id != self.course_id:
                raise ValidationError({
                    'resource': 'Resource must belong to the job course.',
                })

    def __str__(self):
        return f'AI job {self.pk} ({self.state})'


class AIJobResultDraft(models.Model):
    """Structured, teacher-reviewable question draft; not an assessment grade."""

    job = models.ForeignKey(
        AIJob,
        on_delete=models.CASCADE,
        related_name='result_drafts',
    )
    position = models.PositiveSmallIntegerField()
    question_text = models.TextField()
    choices = models.JSONField(default=list)
    correct_choice_index = models.PositiveSmallIntegerField()
    explanation = models.TextField(blank=True, default='')
    citation = models.JSONField(default=dict)
    approved_question = models.OneToOneField('assessments.Question', null=True, blank=True, on_delete=models.PROTECT, related_name='ai_origin_draft')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('position', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('job', 'position'),
                name='ai_result_draft_job_position_unique',
            ),
        ]

    def __str__(self):
        return f'AI job {self.job_id} draft {self.position}'


class AIDailyQuota(models.Model):
    """One locked reservation counter per actor and local business day."""

    actor = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='ai_daily_quotas',
    )
    day = models.DateField()
    jobs_reserved = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('actor', 'day'),
                name='ai_daily_quota_actor_day_unique',
            ),
            models.CheckConstraint(
                condition=Q(jobs_reserved__gte=0),
                name='ai_daily_quota_reserved_nonnegative',
            ),
        ]
