from django.core.exceptions import ValidationError
from django.db import models


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
