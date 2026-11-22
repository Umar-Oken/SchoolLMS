from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from assignments.models import Assignment
from notifications.services import create_deadline_reminders


DEADLINE_REMINDER_WINDOW = timedelta(hours=24)


@shared_task(name='notifications.tasks.create_deadline_reminders')
def create_due_soon_reminders():
    now = timezone.now()
    assignment_versions = list(
        Assignment.objects.filter(
            state=Assignment.State.PUBLISHED,
            topic__state='published',
            topic__archived_at__isnull=True,
            topic__course__archived_at__isnull=True,
            due_at__gt=now,
            due_at__lte=now + DEADLINE_REMINDER_WINDOW,
        )
        .order_by('due_at', 'pk')
        .values_list('pk', 'deadline_version')
    )
    return sum(
        create_deadline_reminders(
            assignment_id,
            expected_version=deadline_version,
            now=now,
        )
        for assignment_id, deadline_version in assignment_versions
    )
