from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from academics.models import Enrollment, MembershipStatus, TeachingAssignment
from assignments.models import Assignment
from learning.models import Announcement
from notifications.models import Notification


@transaction.atomic
def fan_out_announcement(event):
    announcement_id = event.payload.get('announcement_id')
    announcement = (
        Announcement.objects.select_related('course')
        .filter(
            pk=announcement_id,
            state=Announcement.State.PUBLISHED,
            course__archived_at__isnull=True,
        )
        .first()
    )
    if announcement is None:
        raise ValueError(
            'Published announcement event references a missing or unpublished announcement.'
        )

    today = timezone.localdate()
    pupil_ids = Enrollment.objects.filter(
        course_id=announcement.course_id,
        status=MembershipStatus.ACTIVE,
        start_date__lte=today,
        pupil__role=User.Role.PUPIL,
        pupil__is_active=True,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today),
    ).values_list('pupil_id', flat=True)
    teacher_ids = TeachingAssignment.objects.filter(
        course_id=announcement.course_id,
        start_date__lte=today,
        teacher__role=User.Role.TEACHER,
        teacher__is_active=True,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today),
    ).values_list('teacher_id', flat=True)
    recipients = User.objects.filter(
        Q(pk__in=pupil_ids) | Q(pk__in=teacher_ids),
    ).distinct()
    for recipient in recipients.iterator():
        Notification.objects.get_or_create(
            recipient=recipient,
            event_key=event.dedup_key,
            defaults={
                'announcement': announcement,
                'kind': event.kind,
                'title': announcement.title,
                'body': announcement.body,
            },
        )


@transaction.atomic
def create_deadline_reminders(
    assignment_id,
    *,
    expected_version,
    now=None,
):
    now = timezone.now() if now is None else now
    assignment = (
        Assignment.objects.select_for_update()
        .select_related('topic', 'topic__course')
        .filter(pk=assignment_id)
        .first()
    )
    if (
        assignment is None
        or assignment.deadline_version != expected_version
        or assignment.state != Assignment.State.PUBLISHED
        or assignment.topic.state != 'published'
        or assignment.topic.archived_at is not None
        or assignment.topic.course.archived_at is not None
        or assignment.due_at <= now
        or assignment.due_at > now + timedelta(hours=24)
    ):
        return 0

    today = timezone.localdate(now)
    enrollments = (
        Enrollment.objects.select_for_update()
        .select_related('pupil')
        .filter(
            course_id=assignment.topic.course_id,
            status=MembershipStatus.ACTIVE,
            start_date__lte=today,
            pupil__role=User.Role.PUPIL,
            pupil__is_active=True,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .order_by('pk')
    )
    created_count = 0
    for enrollment in enrollments.iterator():
        pupil = enrollment.pupil
        _, created = Notification.objects.get_or_create(
            recipient=pupil,
            assignment=assignment,
            deadline_version=expected_version,
            kind=Notification.Kind.ASSIGNMENT_DUE_SOON,
            defaults={
                'announcement': None,
                'event_key': (
                    f'assignment:{assignment.pk}:deadline:'
                    f'{expected_version}:due_soon'
                ),
                'title': assignment.title,
                'body': 'Vazifa muddati 24 soat ichida tugaydi.',
            },
        )
        created_count += int(created)
    return created_count
