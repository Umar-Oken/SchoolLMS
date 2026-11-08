from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Topic(models.Model):
    class State(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'

    course = models.ForeignKey(
        'academics.CourseOffering',
        on_delete=models.PROTECT,
        related_name='topics',
    )
    term = models.ForeignKey(
        'academics.Term',
        on_delete=models.PROTECT,
        related_name='topics',
    )
    title = models.CharField(max_length=200)
    objective = models.TextField()
    order = models.PositiveSmallIntegerField()
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.DRAFT,
    )
    published_at = models.DateTimeField(blank=True, null=True)
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'term', 'order'],
                name='learning_topic_course_term_order_unique',
            ),
        ]
        ordering = ['term__order', 'order', 'pk']

    def clean(self):
        super().clean()
        if self.course_id and self.term_id:
            course_year_id = self.course.school_class.year_id
            if self.term.year_id != course_year_id:
                raise ValidationError({
                    'term': 'Term and course must belong to the same academic year.'
                })

    def __str__(self):
        return f'{self.course} — {self.title}'


class Resource(models.Model):
    class Kind(models.TextChoices):
        PDF = 'pdf', 'PDF'
        VIDEO = 'video', 'Video'
        LINK = 'link', 'Link'

    class State(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'

    topic = models.ForeignKey(
        Topic,
        on_delete=models.PROTECT,
        related_name='resources',
    )
    kind = models.CharField(
        max_length=10,
        choices=Kind.choices,
        default=Kind.PDF,
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    attachment = models.ForeignKey(
        'files.Attachment',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='resources',
    )
    url = models.URLField(blank=True, null=True, max_length=2048)
    transcript_text = models.TextField(blank=True, default='')
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.DRAFT,
    )
    version = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ('pk',)
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        kind='pdf',
                        attachment__isnull=False,
                        url__isnull=True,
                    )
                    | Q(
                        kind__in=['video', 'link'],
                        attachment__isnull=True,
                        url__isnull=False,
                    )
                ),
                name='learning_resource_kind_target_valid',
            ),
        ]

    def __str__(self):
        return f'{self.topic}: {self.title}'


class Completion(models.Model):
    class Kind(models.TextChoices):
        READ = 'read', 'Read'

    pupil = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='topic_completions',
    )
    topic = models.ForeignKey(
        Topic,
        on_delete=models.PROTECT,
        related_name='completions',
    )
    kind = models.CharField(
        max_length=10,
        choices=Kind.choices,
        default=Kind.READ,
    )
    completed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('pupil', 'topic', 'kind'),
                name='learning_completion_pupil_topic_kind_unique',
            ),
            models.CheckConstraint(
                condition=Q(kind='read'),
                name='learning_completion_kind_valid',
            ),
        ]
        ordering = ('completed_at', 'pk')
