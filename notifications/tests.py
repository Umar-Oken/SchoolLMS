from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from academics.models import (
    AcademicYear,
    CourseOffering,
    Enrollment,
    MembershipStatus,
    SchoolClass,
    Subject,
    TeachingAssignment,
    Term,
)
from assignments.models import Assignment
from assignments.services import change_assignment_deadlines
from learning.models import Announcement, Topic
from notifications.models import Notification
from notifications.services import (
    create_deadline_reminders,
    fan_out_announcement,
)
from outbox.models import OutboxEffectReceipt, OutboxEvent
from outbox.tasks import consume_outbox_event, publish_outbox_event
from notifications.tasks import create_due_soon_reminders


@pytest.fixture
def announcement_event_data(db):
    today = timezone.localdate()
    year = AcademicYear.objects.create(
        start_date=date(today.year, 1, 1),
        end_date=date(today.year, 12, 31),
        label=f'{today.year}',
    )
    school_class = SchoolClass.objects.create(
        year=year,
        grade_level=7,
        section='A',
    )
    subject = Subject.objects.create(code='NOTICE-MATH', title='Matematika')
    course = CourseOffering.objects.create(
        school_class=school_class,
        subject=subject,
    )
    other_class = SchoolClass.objects.create(
        year=year,
        grade_level=7,
        section='B',
    )
    other_course = CourseOffering.objects.create(
        school_class=other_class,
        subject=subject,
    )
    teacher = User.objects.create_user(
        username='notice-teacher',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    pupil_a = User.objects.create_user(
        username='notice-pupil-a',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    pupil_b = User.objects.create_user(
        username='notice-pupil-b',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    foreign_pupil = User.objects.create_user(
        username='notice-foreign-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    TeachingAssignment.objects.create(
        course=course,
        teacher=teacher,
        start_date=today - timedelta(days=10),
    )
    for pupil in (pupil_a, pupil_b):
        Enrollment.objects.create(
            course=course,
            pupil=pupil,
            start_date=today - timedelta(days=10),
        )
    Enrollment.objects.create(
        course=other_course,
        pupil=foreign_pupil,
        start_date=today - timedelta(days=10),
    )
    announcement = Announcement.objects.create(
        course=course,
        title='Ertangi dars',
        body='Dars 9:00 da boshlanadi.',
        state=Announcement.State.PUBLISHED,
        published_at=timezone.now(),
    )
    event = OutboxEvent.objects.create(
        kind='announcement.published',
        payload={
            'announcement_id': announcement.pk,
            'course_id': course.pk,
            'title': announcement.title,
        },
        dedup_key=f'announcement:{announcement.pk}:published',
        next_run_at=timezone.now(),
    )
    return {
        'announcement': announcement,
        'event': event,
        'teacher': teacher,
        'pupil_a': pupil_a,
        'pupil_b': pupil_b,
        'foreign_pupil': foreign_pupil,
    }


@pytest.fixture
def deadline_reminder_data(db):
    now = timezone.now()
    today = timezone.localdate(now)
    year = AcademicYear.objects.create(
        start_date=date(today.year, 1, 1),
        end_date=date(today.year, 12, 31),
        label=f'Deadline {today.year}',
    )
    term = Term.objects.create(
        year=year,
        order=1,
        start_date=date(today.year, 1, 1),
        end_date=date(today.year, 12, 31),
    )
    school_class = SchoolClass.objects.create(
        year=year,
        grade_level=8,
        section='A',
    )
    subject = Subject.objects.create(code='REMINDER-MATH', title='Matematika')
    course = CourseOffering.objects.create(
        school_class=school_class,
        subject=subject,
    )
    teacher = User.objects.create_user(
        username='reminder-teacher',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    pupil = User.objects.create_user(
        username='reminder-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    closed_pupil = User.objects.create_user(
        username='reminder-closed-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    future_pupil = User.objects.create_user(
        username='reminder-future-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    TeachingAssignment.objects.create(
        course=course,
        teacher=teacher,
        start_date=today - timedelta(days=10),
    )
    Enrollment.objects.create(
        course=course,
        pupil=pupil,
        start_date=today - timedelta(days=10),
    )
    Enrollment.objects.create(
        course=course,
        pupil=closed_pupil,
        start_date=today - timedelta(days=10),
        end_date=today - timedelta(days=1),
        status=MembershipStatus.CLOSED,
    )
    Enrollment.objects.create(
        course=course,
        pupil=future_pupil,
        start_date=today + timedelta(days=1),
    )
    topic = Topic.objects.create(
        course=course,
        term=term,
        title='Reminder topic',
        objective='Published topic for deadline tests.',
        order=1,
        state=Topic.State.PUBLISHED,
        published_at=now,
    )
    assignment = Assignment.objects.create(
        topic=topic,
        title='Deadline test',
        instructions='Finish the task.',
        available_at=now - timedelta(days=1),
        due_at=now + timedelta(hours=12),
        closes_at=now + timedelta(days=2),
        max_score='10.00',
        max_attempts=1,
        state=Assignment.State.PUBLISHED,
        published_at=now,
    )
    return {
        'assignment': assignment,
        'pupil': pupil,
        'closed_pupil': closed_pupil,
        'future_pupil': future_pupil,
        'now': now,
    }


@pytest.mark.django_db(transaction=True)
def test_announcement_outbox_dispatch_fans_out_once_to_course_recipients(
    announcement_event_data,
    monkeypatch,
):
    from outbox import tasks

    def deliver(*, args, queue):
        assert queue == 'default'
        return consume_outbox_event.run(*args)

    monkeypatch.setattr(tasks.consume_outbox_event, 'apply_async', deliver)
    event = announcement_event_data['event']

    assert publish_outbox_event(event.pk) == OutboxEvent.Status.PROCESSED
    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.PROCESSED
    assert OutboxEffectReceipt.objects.filter(
        dedup_key=event.dedup_key,
    ).exists()

    expected_recipients = {
        announcement_event_data['teacher'].pk,
        announcement_event_data['pupil_a'].pk,
        announcement_event_data['pupil_b'].pk,
    }
    assert set(
        Notification.objects.filter(
            event_key=event.dedup_key,
        ).values_list('recipient_id', flat=True)
    ) == expected_recipients

    fan_out_announcement(event)
    assert Notification.objects.filter(event_key=event.dedup_key).count() == 3
    assert consume_outbox_event.run(event.pk) == OutboxEvent.Status.PROCESSED
    assert Notification.objects.filter(event_key=event.dedup_key).count() == 3
    assert not Notification.objects.filter(
        recipient=announcement_event_data['foreign_pupil'],
    ).exists()


@pytest.mark.django_db
def test_notification_list_is_paginated_and_scoped_to_current_user(
    announcement_event_data,
):
    event = announcement_event_data['event']
    fan_out_announcement(event)
    client = APIClient()
    client.force_authenticate(user=announcement_event_data['pupil_a'])

    response = client.get('/api/v1/notifications/')

    assert response.status_code == 200
    assert response.data['count'] == 1
    assert response.data['next'] is None
    assert response.data['results'][0]['title'] == 'Ertangi dars'
    assert response.data['results'][0]['is_read'] is False
    assert response.data['results'][0]['read_at'] is None


@pytest.mark.django_db
def test_pupil_cannot_mark_another_pupils_notification_read(
    announcement_event_data,
):
    fan_out_announcement(announcement_event_data['event'])
    notification_b = Notification.objects.get(
        recipient=announcement_event_data['pupil_b'],
    )
    client_a = APIClient()
    client_a.force_authenticate(user=announcement_event_data['pupil_a'])

    response = client_a.patch(
        f'/api/v1/notifications/{notification_b.pk}/read/',
        {},
        format='json',
    )

    assert response.status_code == 404
    notification_b.refresh_from_db()
    assert notification_b.read_at is None


@pytest.mark.django_db
def test_notification_owner_can_mark_read_idempotently(announcement_event_data):
    fan_out_announcement(announcement_event_data['event'])
    notification = Notification.objects.get(
        recipient=announcement_event_data['pupil_a'],
    )
    client = APIClient()
    client.force_authenticate(user=announcement_event_data['pupil_a'])

    first = client.patch(
        f'/api/v1/notifications/{notification.pk}/read/',
        {},
        format='json',
    )
    read_at = first.data['read_at']
    second = client.patch(
        f'/api/v1/notifications/{notification.pk}/read/',
        {},
        format='json',
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data['is_read'] is True
    assert second.data['read_at'] == read_at


@pytest.mark.django_db
def test_two_beat_ticks_create_one_deadline_reminder_for_each_current_pupil(
    deadline_reminder_data,
    monkeypatch,
):
    now = deadline_reminder_data['now']
    monkeypatch.setattr(
        'notifications.tasks.timezone.now',
        lambda: now,
    )

    first_tick = create_due_soon_reminders.run()
    second_tick = create_due_soon_reminders.run()

    assert first_tick == 1
    assert second_tick == 0
    reminders = Notification.objects.filter(
        assignment=deadline_reminder_data['assignment'],
        kind=Notification.Kind.ASSIGNMENT_DUE_SOON,
    )
    assert reminders.count() == 1
    reminder = reminders.get()
    assert reminder.recipient == deadline_reminder_data['pupil']
    assert reminder.deadline_version == 1
    assert reminder.announcement is None
    assert not reminders.filter(
        recipient__in=(
            deadline_reminder_data['closed_pupil'],
            deadline_reminder_data['future_pupil'],
        ),
    ).exists()


@pytest.mark.django_db
def test_reminder_window_includes_exactly_24_hours_and_excludes_expired_deadlines(
    deadline_reminder_data,
):
    assignment = deadline_reminder_data['assignment']
    now = deadline_reminder_data['now']

    Assignment.objects.filter(pk=assignment.pk).update(
        due_at=now + timedelta(hours=24),
    )
    assert create_deadline_reminders(
        assignment.pk,
        expected_version=1,
        now=now,
    ) == 1

    Assignment.objects.filter(pk=assignment.pk).update(due_at=now)
    assert create_deadline_reminders(
        assignment.pk,
        expected_version=1,
        now=now,
    ) == 0
    assert Notification.objects.filter(assignment=assignment).count() == 1


@pytest.mark.django_db
def test_deadline_change_bumps_version_cancels_stale_reminder_and_rechecks(
    deadline_reminder_data,
    monkeypatch,
):
    now = deadline_reminder_data['now']
    assignment = deadline_reminder_data['assignment']
    monkeypatch.setattr(
        'notifications.tasks.timezone.now',
        lambda: now,
    )
    assert create_due_soon_reminders.run() == 1
    old_reminder = Notification.objects.get(assignment=assignment)

    changed = change_assignment_deadlines(
        assignment.pk,
        available_at=now - timedelta(days=1),
        due_at=now + timedelta(hours=36),
        closes_at=now + timedelta(days=2),
    )
    old_reminder.refresh_from_db()

    assert changed.deadline_version == 2
    assert old_reminder.cancelled_at is not None
    assert create_deadline_reminders(
        assignment.pk,
        expected_version=1,
        now=now,
    ) == 0
    assert create_due_soon_reminders.run() == 0
    assert Notification.objects.filter(assignment=assignment).count() == 1
    assert not Notification.objects.filter(
        assignment=assignment,
        cancelled_at__isnull=True,
    ).exists()
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=deadline_reminder_data['pupil'])
    response = pupil_client.get('/api/v1/notifications/')
    assert response.status_code == 200
    assert response.data['count'] == 0


@pytest.mark.django_db
def test_deadline_service_only_bumps_version_when_deadline_changes(
    deadline_reminder_data,
):
    assignment = deadline_reminder_data['assignment']
    now = deadline_reminder_data['now']

    unchanged = change_assignment_deadlines(
        assignment.pk,
        available_at=assignment.available_at,
        due_at=assignment.due_at,
        closes_at=assignment.closes_at,
    )
    changed = change_assignment_deadlines(
        assignment.pk,
        available_at=assignment.available_at,
        due_at=assignment.due_at + timedelta(hours=1),
        closes_at=assignment.closes_at + timedelta(hours=1),
    )

    assert unchanged.deadline_version == 1
    assert changed.deadline_version == 2
    assert changed.due_at > now


@pytest.mark.django_db
def test_deadline_command_changes_deadlines_with_timezone_aware_inputs(
    deadline_reminder_data,
):
    assignment = deadline_reminder_data['assignment']
    now = deadline_reminder_data['now']
    output = StringIO()

    call_command(
        'change_assignment_deadlines',
        '--assignment-id',
        str(assignment.pk),
        '--available-at',
        (now - timedelta(days=1)).isoformat(),
        '--due-at',
        (now + timedelta(hours=13)).isoformat(),
        '--closes-at',
        (now + timedelta(days=2)).isoformat(),
        stdout=output,
    )
    assignment.refresh_from_db()

    assert assignment.deadline_version == 2
    assert 'deadline_version=2' in output.getvalue()
