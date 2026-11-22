from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Kind:
        ANNOUNCEMENT_PUBLISHED = 'announcement.published'
        ASSIGNMENT_DUE_SOON = 'assignment.due_soon'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='notifications',
    )
    announcement = models.ForeignKey(
        'learning.Announcement',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='notifications',
    )
    assignment = models.ForeignKey(
        'assignments.Assignment',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='notifications',
    )
    event_key = models.CharField(max_length=255)
    kind = models.CharField(max_length=100)
    deadline_version = models.PositiveIntegerField(blank=True, null=True)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ('-created_at', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('recipient', 'event_key'),
                name='notifications_recipient_event_unique',
            ),
            models.UniqueConstraint(
                fields=('recipient', 'assignment', 'deadline_version', 'kind'),
                condition=models.Q(assignment__isnull=False),
                name='notifications_deadline_reminder_unique',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        announcement__isnull=False,
                        assignment__isnull=True,
                        deadline_version__isnull=True,
                    )
                    | models.Q(
                        announcement__isnull=True,
                        assignment__isnull=False,
                        deadline_version__isnull=False,
                    )
                ),
                name='notifications_single_source_valid',
            ),
        ]

    @property
    def is_read(self):
        return self.read_at is not None
