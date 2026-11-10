from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


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

    class Meta:
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
