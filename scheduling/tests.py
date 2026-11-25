from datetime import date, time
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import IntegrityError, connections, transaction
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from accounts.models import User
from academics.models import (
    AcademicYear,
    CourseOffering,
    Enrollment,
    SchoolClass,
    Subject,
    TeachingAssignment,
)
from scheduling.models import (
    Attendance,
    AttendanceAudit,
    LessonSession,
    Room,
    ScheduleException,
    TimetableLock,
    WeeklySlot,
)
from scheduling.services import materialize_lessons


TIMETABLE_TEST_CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
    'timetable': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'p077-timetable-tests',
    },
}


@pytest.fixture
def scheduling_data(db):
    year = AcademicYear.objects.create(
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        label='2026–2027',
    )
    school_class = SchoolClass.objects.create(
        year=year,
        grade_level=7,
        section='A',
    )
    course = CourseOffering.objects.create(
        school_class=school_class,
        subject=Subject.objects.create(code='SCHED-MATH', title='Matematika'),
    )
    assigned_teacher = User.objects.create_user(
        username='schedule-teacher',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    other_teacher = User.objects.create_user(
        username='schedule-other-teacher',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    second_class = SchoolClass.objects.create(
        year=year,
        grade_level=7,
        section='B',
    )
    second_course = CourseOffering.objects.create(
        school_class=school_class,
        subject=Subject.objects.create(code='SCHED-SCI', title='Tabiiy fan'),
    )
    other_class_course = CourseOffering.objects.create(
        school_class=second_class,
        subject=Subject.objects.create(code='SCHED-HIST', title='Tarix'),
    )
    pupil = User.objects.create_user(
        username='schedule-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    TeachingAssignment.objects.create(
        course=course,
        teacher=assigned_teacher,
        start_date=date(2026, 9, 1),
    )
    TeachingAssignment.objects.create(
        course=second_course,
        teacher=other_teacher,
        start_date=date(2026, 9, 1),
    )
    TeachingAssignment.objects.create(
        course=other_class_course,
        teacher=other_teacher,
        start_date=date(2026, 9, 1),
    )
    operator = User.objects.create_user(
        username='schedule-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    permission = Permission.objects.get(
        content_type__app_label='accounts',
        codename='manage_academics',
    )
    operator.user_permissions.add(permission)
    return {
        'course': course,
        'second_course': second_course,
        'other_class_course': other_class_course,
        'assigned_teacher': assigned_teacher,
        'other_teacher': other_teacher,
        'pupil': pupil,
        'operator': operator,
        'room': Room.objects.create(name='101-xona'),
        'other_room': Room.objects.create(name='102-xona'),
    }


def weekly_slot_payload(data):
    return {
        'course': data['course'].pk,
        'teacher': data['assigned_teacher'].pk,
        'room': data['room'].pk,
        'weekday': WeeklySlot.Weekday.MONDAY,
        'start_time': '09:00:00',
        'end_time': '09:45:00',
        'valid_from': '2026-09-01',
        'valid_to': None,
    }


def create_attendance_session(data, local_date=date(2026, 11, 16)):
    slot = WeeklySlot.objects.create(
        course=data['course'],
        teacher=data['assigned_teacher'],
        room=data['room'],
        weekday=local_date.weekday(),
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    materialize_lessons(from_date=local_date, to_date=local_date)
    return LessonSession.objects.get(slot=slot, local_date=local_date)


@pytest.mark.django_db
def test_manage_academics_can_create_read_and_update_weekly_slot(scheduling_data):
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])
    payload = weekly_slot_payload(scheduling_data)

    created = client.post('/api/v1/admin/weekly-slots/', payload, format='json')
    assert created.status_code == status.HTTP_201_CREATED
    slot_id = created.data['id']

    listed = client.get('/api/v1/admin/weekly-slots/')
    assert listed.status_code == status.HTTP_200_OK
    assert listed.data[0]['teacher'] == scheduling_data['assigned_teacher'].pk

    detail = client.get(f'/api/v1/admin/weekly-slots/{slot_id}/')
    assert detail.status_code == status.HTTP_200_OK

    updated = client.patch(
        f'/api/v1/admin/weekly-slots/{slot_id}/',
        {'end_time': '10:00:00'},
        format='json',
    )
    assert updated.status_code == status.HTTP_200_OK
    assert updated.data['end_time'] == '10:00:00'


@pytest.mark.django_db
@pytest.mark.parametrize('user_key', ['assigned_teacher', 'pupil'])
def test_regular_teacher_and_pupil_cannot_change_schedule(scheduling_data, user_key):
    slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data[user_key])
    payload = weekly_slot_payload(scheduling_data)

    create_response = client.post(
        '/api/v1/admin/weekly-slots/',
        payload,
        format='json',
    )
    update_response = client.patch(
        f'/api/v1/admin/weekly-slots/{slot.pk}/',
        {'end_time': '10:00:00'},
        format='json',
    )
    read_response = client.get('/api/v1/admin/weekly-slots/')

    assert create_response.status_code == status.HTTP_403_FORBIDDEN
    assert update_response.status_code == status.HTTP_403_FORBIDDEN
    assert read_response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('end_time', '09:00:00'),
        ('end_time', '08:59:00'),
        ('teacher', 'other_teacher'),
        ('teacher', 'pupil'),
    ],
)
def test_weekly_slot_rejects_invalid_interval_or_teacher(
    scheduling_data,
    field,
    value,
):
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])
    payload = weekly_slot_payload(scheduling_data)
    payload[field] = (
        scheduling_data[value].pk
        if value in {'other_teacher', 'pupil'}
        else value
    )

    response = client.post(
        '/api/v1/admin/weekly-slots/',
        payload,
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not WeeklySlot.objects.exists()


@pytest.mark.django_db
def test_weekly_slot_database_constraint_rejects_reversed_times(scheduling_data):
    with pytest.raises(IntegrityError), transaction.atomic():
        WeeklySlot.objects.create(
            course=scheduling_data['course'],
            teacher=scheduling_data['assigned_teacher'],
            room=scheduling_data['room'],
            weekday=WeeklySlot.Weekday.MONDAY,
            start_time=time(10),
            end_time=time(9),
            valid_from=date(2026, 9, 1),
        )


@pytest.mark.django_db
def test_manage_academics_can_manage_rooms(scheduling_data):
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])

    created = client.post(
        '/api/v1/admin/rooms/',
        {'name': '202-xona'},
        format='json',
    )
    assert created.status_code == status.HTTP_201_CREATED
    read = client.get(f"/api/v1/admin/rooms/{created.data['id']}/")
    assert read.status_code == status.HTTP_200_OK
    updated = client.patch(
        f"/api/v1/admin/rooms/{created.data['id']}/",
        {'name': '203-xona'},
        format='json',
    )
    assert updated.status_code == status.HTTP_200_OK


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('conflict_field', 'candidate_changes'),
    [
        (
            'room',
            {
                'course': 'other_class_course',
                'teacher': 'other_teacher',
            },
        ),
        (
            'teacher',
            {
                'course': 'other_class_course',
                'room': 'other_room',
            },
        ),
        (
            'course',
            {
                'course': 'second_course',
                'teacher': 'other_teacher',
                'room': 'other_room',
            },
        ),
    ],
)
def test_weekly_slot_rejects_resource_conflicts(
    scheduling_data,
    conflict_field,
    candidate_changes,
):
    WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(8),
        end_time=time(8, 45),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 6, 30),
    )
    payload = weekly_slot_payload(scheduling_data)
    payload.update({
        'start_time': '08:15:00',
        'end_time': '08:30:00',
        'valid_to': '2027-06-30',
    })
    for field, fixture_key in candidate_changes.items():
        payload[field] = scheduling_data[fixture_key].pk

    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])
    response = client.post(
        '/api/v1/admin/weekly-slots/',
        payload,
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert conflict_field in response.data
    assert WeeklySlot.objects.count() == 1


@pytest.mark.django_db
def test_weekly_slot_adjacent_intervals_are_allowed(scheduling_data):
    payload = weekly_slot_payload(scheduling_data)
    payload.update({
        'start_time': '08:00:00',
        'end_time': '08:45:00',
        'valid_to': '2027-06-30',
    })
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])
    first = client.post(
        '/api/v1/admin/weekly-slots/',
        payload,
        format='json',
    )
    payload.update({
        'start_time': '08:45:00',
        'end_time': '09:30:00',
    })

    second = client.post(
        '/api/v1/admin/weekly-slots/',
        payload,
        format='json',
    )

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_weekly_slot_nonoverlapping_weekday_or_dates_are_allowed(scheduling_data):
    first = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(8),
        end_time=time(8, 45),
        valid_from=date(2026, 9, 1),
        valid_to=date(2026, 12, 31),
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])

    different_day = weekly_slot_payload(scheduling_data)
    different_day.update({
        'weekday': WeeklySlot.Weekday.TUESDAY,
        'start_time': '08:00:00',
        'end_time': '08:45:00',
    })
    second = client.post(
        '/api/v1/admin/weekly-slots/',
        different_day,
        format='json',
    )
    different_dates = {
        **weekly_slot_payload(scheduling_data),
        'start_time': '08:00:00',
        'end_time': '08:45:00',
        'valid_from': '2027-01-01',
        'valid_to': '2027-06-30',
    }
    third = client.post(
        '/api/v1/admin/weekly-slots/',
        different_dates,
        format='json',
    )
    self_update = client.patch(
        f'/api/v1/admin/weekly-slots/{first.pk}/',
        {'end_time': '08:50:00'},
        format='json',
    )

    assert second.status_code == status.HTTP_201_CREATED
    assert third.status_code == status.HTTP_201_CREATED
    assert self_update.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_weekly_slot_update_rejects_conflict_and_keeps_old_values(scheduling_data):
    existing = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(8),
        end_time=time(8, 45),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 6, 30),
    )
    target = WeeklySlot.objects.create(
        course=scheduling_data['other_class_course'],
        teacher=scheduling_data['other_teacher'],
        room=scheduling_data['other_room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 6, 30),
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])

    response = client.patch(
        f'/api/v1/admin/weekly-slots/{target.pk}/',
        {'room': scheduling_data['room'].pk, 'start_time': '08:15:00'},
        format='json',
    )
    target.refresh_from_db()

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'room' in response.data
    assert target.room_id == scheduling_data['other_room'].pk
    assert target.start_time == time(9)
    assert existing.pk != target.pk


@pytest.mark.django_db(transaction=True)
def test_parallel_conflicting_creates_cannot_both_succeed(scheduling_data):
    TimetableLock.objects.filter(pk='school').delete()
    barrier = Barrier(2)
    payload = weekly_slot_payload(scheduling_data)

    def create_slot():
        connections.close_all()
        client = APIClient()
        client.force_authenticate(user=scheduling_data['operator'])
        barrier.wait(timeout=10)
        try:
            response = client.post(
                '/api/v1/admin/weekly-slots/',
                payload,
                format='json',
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: create_slot(), range(2)))

    assert responses.count(status.HTTP_201_CREATED) == 1
    assert responses.count(status.HTTP_400_BAD_REQUEST) == 1
    assert WeeklySlot.objects.count() == 1
    assert TimetableLock.objects.filter(pk='school').exists()


@pytest.mark.django_db
def test_materialize_lessons_is_idempotent_and_converts_local_time(scheduling_data):
    slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 6, 30),
    )
    local_date = date(2026, 11, 16)

    first_run = materialize_lessons(from_date=local_date, to_date=local_date)
    second_run = materialize_lessons(from_date=local_date, to_date=local_date)

    assert len(first_run) == 1
    assert second_run == []
    session = LessonSession.objects.get(slot=slot, local_date=local_date)
    assert session.starts_at.isoformat() == '2026-11-16T04:00:00+00:00'
    assert session.ends_at.isoformat() == '2026-11-16T04:45:00+00:00'


@pytest.mark.django_db
def test_materialize_lessons_only_creates_inside_slot_validity(scheduling_data):
    slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 11, 16),
        valid_to=date(2026, 11, 16),
    )

    created = materialize_lessons(
        from_date=date(2026, 11, 9),
        to_date=date(2026, 11, 23),
    )

    assert [(session.slot_id, session.local_date) for session in created] == [
        (slot.pk, date(2026, 11, 16)),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('from_date', 'to_date'),
    [
        (date(2026, 11, 17), date(2026, 11, 16)),
        (date(2026, 11, 1), date(2026, 12, 2)),
    ],
)
def test_materialization_rejects_invalid_or_over_31_day_window(
    from_date,
    to_date,
):
    with pytest.raises(DjangoValidationError):
        materialize_lessons(from_date=from_date, to_date=to_date)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('from_date', 'to_date', 'expected_status'),
    [
        ('2026-11-16', '2026-12-16', status.HTTP_200_OK),
        ('2026-11-16', '2026-12-17', status.HTTP_400_BAD_REQUEST),
        ('invalid', '2026-11-16', status.HTTP_400_BAD_REQUEST),
    ],
)
def test_schedule_api_validates_query_window(
    scheduling_data,
    from_date,
    to_date,
    expected_status,
):
    client = APIClient()
    client.force_authenticate(user=scheduling_data['assigned_teacher'])

    response = client.get(
        '/api/v1/schedule/',
        {'from': from_date, 'to': to_date},
    )

    assert response.status_code == expected_status


@pytest.mark.django_db
def test_schedule_api_scopes_sessions_to_teacher_assignments_and_pupil_enrollment(
    scheduling_data,
):
    local_date = date(2026, 11, 16)
    own_slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 6, 30),
    )
    other_slot = WeeklySlot.objects.create(
        course=scheduling_data['other_class_course'],
        teacher=scheduling_data['other_teacher'],
        room=scheduling_data['other_room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(10),
        end_time=time(10, 45),
        valid_from=date(2026, 9, 1),
        valid_to=date(2027, 6, 30),
    )
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    materialize_lessons(from_date=local_date, to_date=local_date)
    query = {'from': local_date.isoformat(), 'to': local_date.isoformat()}

    teacher_client = APIClient()
    teacher_client.force_authenticate(user=scheduling_data['assigned_teacher'])
    teacher_response = teacher_client.get('/api/v1/schedule/', query)
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=scheduling_data['pupil'])
    pupil_response = pupil_client.get('/api/v1/schedule/', query)

    assert teacher_response.status_code == status.HTTP_200_OK
    assert [row['slot'] for row in teacher_response.data] == [own_slot.pk]
    assert pupil_response.status_code == status.HTTP_200_OK
    assert [row['slot'] for row in pupil_response.data] == [own_slot.pk]
    assert LessonSession.objects.filter(slot=other_slot).exists()


@pytest.mark.django_db
@override_settings(CACHES=TIMETABLE_TEST_CACHES, TIMETABLE_CACHE_TTL=60)
def test_timetable_cache_is_scoped_to_each_pupil(scheduling_data):
    local_date = date(2026, 11, 16)
    own_slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=local_date.weekday(),
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    other_slot = WeeklySlot.objects.create(
        course=scheduling_data['other_class_course'],
        teacher=scheduling_data['other_teacher'],
        room=scheduling_data['other_room'],
        weekday=local_date.weekday(),
        start_time=time(10),
        end_time=time(10, 45),
        valid_from=date(2026, 9, 1),
    )
    other_pupil = User.objects.create_user(
        username='other-schedule-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    Enrollment.objects.create(
        pupil=other_pupil,
        course=scheduling_data['other_class_course'],
        start_date=date(2026, 9, 1),
    )
    materialize_lessons(from_date=local_date, to_date=local_date)
    query = {'from': local_date.isoformat(), 'to': local_date.isoformat()}

    first_client = APIClient()
    first_client.force_authenticate(user=scheduling_data['pupil'])
    second_client = APIClient()
    second_client.force_authenticate(user=other_pupil)

    first = first_client.get('/api/v1/schedule/', query)
    second = second_client.get('/api/v1/schedule/', query)

    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert [row['slot'] for row in first.data] == [own_slot.pk]
    assert [row['slot'] for row in second.data] == [other_slot.pk]


@pytest.mark.django_db
@override_settings(CACHES=TIMETABLE_TEST_CACHES, TIMETABLE_CACHE_TTL=60)
def test_schedule_exception_bumps_cache_version(scheduling_data):
    session = create_attendance_session(scheduling_data)
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    query = {'from': '2026-11-16', 'to': '2026-11-16'}
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=scheduling_data['pupil'])

    cached = pupil_client.get('/api/v1/schedule/', query)
    assert [row['id'] for row in cached.data] == [session.pk]

    operator_client = APIClient()
    operator_client.force_authenticate(user=scheduling_data['operator'])
    changed = operator_client.post(
        f'/api/v1/admin/lesson-sessions/{session.pk}/exception/',
        {'action': 'cancelled', 'reason': 'Synthetic closure'},
        format='json',
    )
    fresh = pupil_client.get('/api/v1/schedule/', query)

    assert changed.status_code == status.HTTP_200_OK
    assert fresh.data[0]['id'] == session.pk
    assert fresh.data[0]['status'] == LessonSession.Status.CANCELLED


@pytest.mark.django_db
@override_settings(CACHES=TIMETABLE_TEST_CACHES, TIMETABLE_CACHE_TTL=60)
def test_timetable_cache_outage_falls_back_to_database(scheduling_data):
    session = create_attendance_session(scheduling_data)
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )

    class FailingCache:
        def get(self, *args, **kwargs):
            raise OSError('Redis unavailable')

        def set(self, *args, **kwargs):
            raise OSError('Redis unavailable')

    class FailingCaches:
        def __getitem__(self, alias):
            assert alias == 'timetable'
            return FailingCache()

    client = APIClient()
    client.force_authenticate(user=scheduling_data['pupil'])
    with patch('scheduling.cache.caches', FailingCaches()):
        response = client.get(
            '/api/v1/schedule/',
            {'from': '2026-11-16', 'to': '2026-11-16'},
        )

    assert response.status_code == status.HTTP_200_OK
    assert [row['id'] for row in response.data] == [session.pk]


@pytest.mark.django_db
def test_materialize_lessons_management_command_is_idempotent(scheduling_data):
    WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 11, 16),
    )
    output = []

    class CaptureOutput:
        def write(self, message):
            output.append(message)

    call_command(
        'materialize_lessons',
        '--from',
        '2026-11-16',
        '--to',
        '2026-11-16',
        stdout=CaptureOutput(),
    )
    call_command(
        'materialize_lessons',
        '--from',
        '2026-11-16',
        '--to',
        '2026-11-16',
        stdout=CaptureOutput(),
    )

    assert LessonSession.objects.count() == 1
    assert output == [
        '1 ta yangi dars yaratildi.\n',
        '0 ta yangi dars yaratildi.\n',
    ]


@pytest.mark.django_db
def test_cancelled_occurrence_keeps_session_and_audit(scheduling_data):
    session_date = date(2026, 11, 16)
    slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    materialize_lessons(from_date=session_date, to_date=session_date)
    session = LessonSession.objects.get(slot=slot, local_date=session_date)
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])

    response = client.post(
        f'/api/v1/admin/lesson-sessions/{session.pk}/exception/',
        {'action': 'cancelled', 'reason': 'Maktab yopiq'},
        format='json',
    )

    session.refresh_from_db()
    slot.refresh_from_db()
    exception = ScheduleException.objects.get(session=session)
    assert response.status_code == status.HTTP_200_OK
    assert session.status == LessonSession.Status.CANCELLED
    assert LessonSession.objects.filter(pk=session.pk).exists()
    assert slot.weekday == WeeklySlot.Weekday.MONDAY
    assert slot.start_time == time(9)
    assert exception.action == ScheduleException.Action.CANCELLED
    assert exception.reason == 'Maktab yopiq'
    assert exception.actor_id == scheduling_data['operator'].pk
    with pytest.raises(DjangoValidationError):
        exception.save()
    with pytest.raises(DjangoValidationError):
        exception.delete()

    repeated_cancel = client.post(
        f'/api/v1/admin/lesson-sessions/{session.pk}/exception/',
        {'action': 'cancelled', 'reason': 'Takroriy bekor qilish'},
        format='json',
    )
    assert repeated_cancel.status_code == status.HTTP_400_BAD_REQUEST
    assert ScheduleException.objects.filter(session=session).count() == 1


@pytest.mark.django_db
def test_move_changes_only_one_session_and_allows_course_assigned_substitute(
    scheduling_data,
):
    second_teacher_assignment = TeachingAssignment.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['other_teacher'],
        start_date=date(2026, 9, 1),
    )
    session_date = date(2026, 11, 16)
    next_session_date = date(2026, 11, 23)
    slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    materialize_lessons(
        from_date=session_date,
        to_date=next_session_date,
    )
    session = LessonSession.objects.get(slot=slot, local_date=session_date)
    unaffected_session = LessonSession.objects.get(
        slot=slot,
        local_date=next_session_date,
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])

    response = client.post(
        f'/api/v1/admin/lesson-sessions/{session.pk}/exception/',
        {
            'action': 'moved',
            'reason': 'Xona band',
            'scheduled_date': '2026-11-17',
            'start_time': '11:00:00',
            'end_time': '11:45:00',
            'teacher': scheduling_data['other_teacher'].pk,
            'room': scheduling_data['other_room'].pk,
        },
        format='json',
    )

    session.refresh_from_db()
    unaffected_session.refresh_from_db()
    slot.refresh_from_db()
    exception = ScheduleException.objects.get(session=session)
    assert response.status_code == status.HTTP_200_OK
    assert session.status == LessonSession.Status.MOVED
    assert session.local_date == session_date
    assert session.scheduled_date == date(2026, 11, 17)
    assert session.teacher_override_id == second_teacher_assignment.teacher_id
    assert session.room_override_id == scheduling_data['other_room'].pk
    assert unaffected_session.scheduled_date == next_session_date
    assert unaffected_session.status == LessonSession.Status.SCHEDULED
    assert slot.weekday == WeeklySlot.Weekday.MONDAY
    assert exception.action == ScheduleException.Action.MOVED

    schedule_client = APIClient()
    schedule_client.force_authenticate(user=scheduling_data['other_teacher'])
    schedule_response = schedule_client.get(
        '/api/v1/schedule/',
        {'from': '2026-11-17', 'to': '2026-11-17'},
    )
    assert schedule_response.status_code == status.HTTP_200_OK
    assert [row['id'] for row in schedule_response.data] == [session.pk]
    assert schedule_response.data[0]['teacher'] == scheduling_data['other_teacher'].pk
    assert schedule_response.data[0]['scheduled_date'] == '2026-11-17'

    cancel_response = client.post(
        f'/api/v1/admin/lesson-sessions/{session.pk}/exception/',
        {'action': 'cancelled', 'reason': 'Yakuniy bekor qilish'},
        format='json',
    )
    session.refresh_from_db()
    assert cancel_response.status_code == status.HTTP_200_OK
    assert session.status == LessonSession.Status.CANCELLED
    assert session.scheduled_date == date(2026, 11, 17)
    assert session.teacher_override_id == scheduling_data['other_teacher'].pk
    assert session.room_override_id == scheduling_data['other_room'].pk
    assert ScheduleException.objects.filter(session=session).count() == 2


@pytest.mark.django_db
def test_conflicting_occurrence_move_rolls_back(scheduling_data):
    TeachingAssignment.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['other_teacher'],
        start_date=date(2026, 9, 1),
    )
    source_date = date(2026, 11, 16)
    target_slot = WeeklySlot.objects.create(
        course=scheduling_data['other_class_course'],
        teacher=scheduling_data['other_teacher'],
        room=scheduling_data['other_room'],
        weekday=WeeklySlot.Weekday.TUESDAY,
        start_time=time(11),
        end_time=time(11, 45),
        valid_from=date(2026, 9, 1),
    )
    source_slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    materialize_lessons(from_date=source_date, to_date=source_date)
    session = LessonSession.objects.get(slot=source_slot, local_date=source_date)
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])

    response = client.post(
        f'/api/v1/admin/lesson-sessions/{session.pk}/exception/',
        {
            'action': 'moved',
            'reason': 'Xona almashtirish',
            'scheduled_date': '2026-11-17',
            'start_time': '11:15:00',
            'end_time': '12:00:00',
            'room': scheduling_data['other_room'].pk,
        },
        format='json',
    )

    session.refresh_from_db()
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'room' in response.data
    assert session.status == LessonSession.Status.SCHEDULED
    assert session.scheduled_date == source_date
    assert not ScheduleException.objects.filter(session=session).exists()
    assert target_slot.weekday == WeeklySlot.Weekday.TUESDAY


@pytest.mark.django_db
def test_move_rejects_replacement_teacher_outside_course_scope(scheduling_data):
    session_date = date(2026, 11, 16)
    slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    materialize_lessons(from_date=session_date, to_date=session_date)
    session = LessonSession.objects.get(slot=slot, local_date=session_date)
    client = APIClient()
    client.force_authenticate(user=scheduling_data['operator'])

    response = client.post(
        f'/api/v1/admin/lesson-sessions/{session.pk}/exception/',
        {
            'action': 'moved',
            'reason': 'O‘rinbosar kerak',
            'scheduled_date': '2026-11-17',
            'start_time': '11:00:00',
            'end_time': '11:45:00',
            'teacher': scheduling_data['other_teacher'].pk,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'teacher' in response.data
    assert not ScheduleException.objects.filter(session=session).exists()


@pytest.mark.django_db
def test_regular_teacher_and_pupil_cannot_create_schedule_exceptions(
    scheduling_data,
):
    session_date = date(2026, 11, 16)
    slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    materialize_lessons(from_date=session_date, to_date=session_date)
    session = LessonSession.objects.get(slot=slot, local_date=session_date)
    client = APIClient()

    for user in (scheduling_data['assigned_teacher'], scheduling_data['pupil']):
        client.force_authenticate(user=user)
        response = client.post(
            f'/api/v1/admin/lesson-sessions/{session.pk}/exception/',
            {'action': 'cancelled', 'reason': 'test'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    assert not ScheduleException.objects.filter(session=session).exists()


@pytest.mark.django_db
def test_assigned_teacher_can_put_attendance_for_enrolled_pupils(scheduling_data):
    session = create_attendance_session(scheduling_data)
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['assigned_teacher'])

    response = client.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        [
            {
                'pupil': scheduling_data['pupil'].pk,
                'status': 'present',
            },
        ],
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        'session': session.pk,
        'attendance': [{'pupil': scheduling_data['pupil'].pk, 'status': 'present'}],
    }
    assert Attendance.objects.get(
        session=session,
        pupil=scheduling_data['pupil'],
    ).status == Attendance.Status.PRESENT
    assert AttendanceAudit.objects.count() == 0


@pytest.mark.django_db
def test_attendance_batch_rejects_duplicate_pupils_without_writes(scheduling_data):
    session = create_attendance_session(scheduling_data)
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['assigned_teacher'])
    row = {'pupil': scheduling_data['pupil'].pk, 'status': 'present'}

    response = client.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        [row, row],
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Attendance.objects.exists()


@pytest.mark.django_db
def test_attendance_batch_rejects_any_non_enrolled_pupil_atomically(
    scheduling_data,
):
    session = create_attendance_session(scheduling_data)
    outsider = User.objects.create_user(
        username='attendance-outsider',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    Enrollment.objects.create(
        pupil=outsider,
        course=scheduling_data['other_class_course'],
        start_date=date(2026, 9, 1),
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['assigned_teacher'])

    response = client.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        [
            {'pupil': scheduling_data['pupil'].pk, 'status': 'present'},
            {'pupil': outsider.pk, 'status': 'present'},
        ],
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Attendance.objects.exists()


@pytest.mark.django_db
def test_attendance_status_change_requires_reason_and_audits_atomically(
    scheduling_data,
):
    session = create_attendance_session(scheduling_data)
    second_pupil = User.objects.create_user(
        username='attendance-second-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    for pupil in (scheduling_data['pupil'], second_pupil):
        Enrollment.objects.create(
            pupil=pupil,
            course=scheduling_data['course'],
            start_date=date(2026, 9, 1),
        )
        Attendance.objects.create(
            session=session,
            pupil=pupil,
            status=Attendance.Status.ABSENT,
        )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['assigned_teacher'])

    rejected = client.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        [
            {
                'pupil': scheduling_data['pupil'].pk,
                'status': 'present',
                'reason': 'Qayta tekshirildi',
            },
            {'pupil': second_pupil.pk, 'status': 'present'},
        ],
        format='json',
    )
    assert rejected.status_code == status.HTTP_400_BAD_REQUEST
    assert Attendance.objects.filter(status=Attendance.Status.ABSENT).count() == 2
    assert not AttendanceAudit.objects.exists()

    accepted = client.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        [
            {
                'pupil': scheduling_data['pupil'].pk,
                'status': 'present',
                'reason': 'Qayta tekshirildi',
            },
            {'pupil': second_pupil.pk, 'status': 'absent'},
        ],
        format='json',
    )
    assert accepted.status_code == status.HTTP_200_OK
    audit = AttendanceAudit.objects.get()
    assert audit.attendance.pupil_id == scheduling_data['pupil'].pk
    assert audit.old_status == Attendance.Status.ABSENT
    assert audit.new_status == Attendance.Status.PRESENT
    assert audit.reason == 'Qayta tekshirildi'
    assert audit.actor_id == scheduling_data['assigned_teacher'].pk

    unchanged = client.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        [
            {'pupil': scheduling_data['pupil'].pk, 'status': 'present'},
            {'pupil': second_pupil.pk, 'status': 'absent'},
        ],
        format='json',
    )
    assert unchanged.status_code == status.HTTP_200_OK
    assert AttendanceAudit.objects.count() == 1


@pytest.mark.django_db
def test_attendance_write_requires_assigned_teacher_and_active_session(
    scheduling_data,
):
    session = create_attendance_session(scheduling_data)
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    payload = [{'pupil': scheduling_data['pupil'].pk, 'status': 'late'}]
    client = APIClient()

    for user in (
        scheduling_data['other_teacher'],
        scheduling_data['pupil'],
    ):
        client.force_authenticate(user=user)
        unauthorized = client.put(
            f'/api/v1/sessions/{session.pk}/attendance/',
            payload,
            format='json',
        )
        assert unauthorized.status_code == status.HTTP_403_FORBIDDEN

    client.force_authenticate(user=scheduling_data['assigned_teacher'])
    session.status = LessonSession.Status.CANCELLED
    session.save(update_fields=('status',))
    cancelled = client.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        payload,
        format='json',
    )
    assert cancelled.status_code == status.HTTP_400_BAD_REQUEST
    assert not Attendance.objects.exists()


@pytest.mark.django_db
def test_attendance_uses_moved_session_date_for_enrollment(scheduling_data):
    session = create_attendance_session(scheduling_data)
    session.scheduled_date = date(2026, 11, 17)
    session.save(update_fields=('scheduled_date',))
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 11, 17),
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['assigned_teacher'])

    response = client.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        [{'pupil': scheduling_data['pupil'].pk, 'status': 'unmarked'}],
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert Attendance.objects.get(session=session).status == Attendance.Status.UNMARKED


@pytest.mark.django_db
def test_attendance_audit_cannot_be_updated_or_deleted(scheduling_data):
    session = create_attendance_session(scheduling_data)
    record = Attendance.objects.create(
        session=session,
        pupil=scheduling_data['pupil'],
        status=Attendance.Status.PRESENT,
    )
    audit = AttendanceAudit.objects.create(
        attendance=record,
        old_status=Attendance.Status.ABSENT,
        new_status=Attendance.Status.PRESENT,
        reason='Davomat tuzatildi',
        actor=scheduling_data['assigned_teacher'],
    )

    with pytest.raises(DjangoValidationError):
        audit.save()
    with pytest.raises(DjangoValidationError):
        audit.delete()


@pytest.mark.django_db
def test_pupil_attendance_summary_excludes_cancelled_and_keeps_unmarked(
    scheduling_data,
):
    pupil = scheduling_data['pupil']
    slot = WeeklySlot.objects.create(
        course=scheduling_data['course'],
        teacher=scheduling_data['assigned_teacher'],
        room=scheduling_data['room'],
        weekday=WeeklySlot.Weekday.MONDAY,
        start_time=time(9),
        end_time=time(9, 45),
        valid_from=date(2026, 9, 1),
    )
    Enrollment.objects.create(
        pupil=pupil,
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    from_date = date(2026, 11, 16)
    to_date = date(2026, 11, 30)
    materialize_lessons(from_date=from_date, to_date=to_date)
    sessions = list(
        LessonSession.objects.filter(slot=slot).order_by('scheduled_date')
    )
    Attendance.objects.create(
        session=sessions[0],
        pupil=pupil,
        status=Attendance.Status.PRESENT,
    )
    Attendance.objects.create(
        session=sessions[1],
        pupil=pupil,
        status=Attendance.Status.UNMARKED,
    )
    sessions[2].status = LessonSession.Status.CANCELLED
    sessions[2].save(update_fields=('status',))

    client = APIClient()
    client.force_authenticate(user=pupil)
    response = client.get(
        '/api/v1/me/attendance/',
        {'from': from_date.isoformat(), 'to': to_date.isoformat()},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data['summary'] == {
        'eligible_lessons': 2,
        'marked_lessons': 1,
        'present': 1,
        'absent': 0,
        'late': 0,
        'excused': 0,
        'unmarked': 1,
    }
    assert [row['status'] for row in response.data['results']] == [
        Attendance.Status.PRESENT,
        Attendance.Status.UNMARKED,
    ]
    assert sessions[2].pk not in {
        row['session'] for row in response.data['results']
    }


@pytest.mark.django_db
def test_pupil_attendance_api_only_returns_request_users_records(
    scheduling_data,
):
    session = create_attendance_session(scheduling_data)
    another_pupil = User.objects.create_user(
        username='attendance-private-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    for pupil in (scheduling_data['pupil'], another_pupil):
        Enrollment.objects.create(
            pupil=pupil,
            course=scheduling_data['course'],
            start_date=date(2026, 9, 1),
        )
    own_record = Attendance.objects.create(
        session=session,
        pupil=scheduling_data['pupil'],
        status=Attendance.Status.PRESENT,
    )
    other_record = Attendance.objects.create(
        session=session,
        pupil=another_pupil,
        status=Attendance.Status.ABSENT,
    )
    AttendanceAudit.objects.create(
        attendance=other_record,
        old_status=Attendance.Status.PRESENT,
        new_status=Attendance.Status.ABSENT,
        reason='Boshqa pupil uchun ichki tuzatish',
        actor=scheduling_data['assigned_teacher'],
    )
    client = APIClient()
    client.force_authenticate(user=scheduling_data['pupil'])

    response = client.get(
        '/api/v1/me/attendance/',
        {'from': '2026-11-16', 'to': '2026-11-16'},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data['summary']['present'] == 1
    assert response.data['summary']['absent'] == 0
    assert len(response.data['results']) == 1
    assert response.data['results'][0]['session'] == session.pk
    assert response.data['results'][0]['history'] == []
    assert own_record.status == Attendance.Status.PRESENT


@pytest.mark.django_db
def test_pupil_attendance_api_validates_date_window(scheduling_data):
    client = APIClient()
    client.force_authenticate(user=scheduling_data['pupil'])

    response = client.get(
        '/api/v1/me/attendance/',
        {'from': '2026-11-01', 'to': '2026-12-02'},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_teacher_attendance_read_includes_roster_and_correction_history(
    scheduling_data,
):
    session = create_attendance_session(scheduling_data)
    second_pupil = User.objects.create_user(
        username='attendance-roster-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    for pupil in (scheduling_data['pupil'], second_pupil):
        Enrollment.objects.create(
            pupil=pupil,
            course=scheduling_data['course'],
            start_date=date(2026, 9, 1),
        )
    Attendance.objects.create(
        session=session,
        pupil=scheduling_data['pupil'],
        status=Attendance.Status.ABSENT,
    )
    writer = APIClient()
    writer.force_authenticate(user=scheduling_data['assigned_teacher'])
    correction = writer.put(
        f'/api/v1/sessions/{session.pk}/attendance/',
        [{
            'pupil': scheduling_data['pupil'].pk,
            'status': Attendance.Status.PRESENT,
            'reason': 'Teacher davomatni qayta tekshirdi',
        }],
        format='json',
    )

    reader = APIClient()
    reader.force_authenticate(user=scheduling_data['assigned_teacher'])
    response = reader.get(
        f'/api/v1/sessions/{session.pk}/attendance/',
    )

    assert correction.status_code == status.HTTP_200_OK
    assert response.status_code == status.HTTP_200_OK
    assert response.data['summary'] == {
        'eligible_pupils': 2,
        'marked_pupils': 1,
        'present': 1,
        'absent': 0,
        'late': 0,
        'excused': 0,
        'unmarked': 1,
    }
    corrected = next(
        row for row in response.data['attendance']
        if row['pupil'] == scheduling_data['pupil'].pk
    )
    assert corrected['status'] == Attendance.Status.PRESENT
    assert len(corrected['history']) == 1
    assert corrected['history'][0]['old_status'] == Attendance.Status.ABSENT
    assert corrected['history'][0]['new_status'] == Attendance.Status.PRESENT
    assert corrected['history'][0]['reason'] == 'Teacher davomatni qayta tekshirdi'

    session.status = LessonSession.Status.CANCELLED
    session.save(update_fields=('status',))
    cancelled_response = reader.get(
        f'/api/v1/sessions/{session.pk}/attendance/',
    )
    assert cancelled_response.status_code == status.HTTP_200_OK
    assert cancelled_response.data['summary'] == {
        'eligible_pupils': 0,
        'marked_pupils': 0,
        'present': 0,
        'absent': 0,
        'late': 0,
        'excused': 0,
        'unmarked': 0,
    }
    assert cancelled_response.data['attendance'][0]['history'] == corrected['history']


@pytest.mark.django_db
def test_attendance_read_permissions_are_limited_to_own_pupil_or_assigned_teacher(
    scheduling_data,
):
    session = create_attendance_session(scheduling_data)
    Enrollment.objects.create(
        pupil=scheduling_data['pupil'],
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    client = APIClient()

    client.force_authenticate(user=scheduling_data['other_teacher'])
    unassigned_teacher = client.get(
        f'/api/v1/sessions/{session.pk}/attendance/',
    )
    assert unassigned_teacher.status_code == status.HTTP_403_FORBIDDEN

    client.force_authenticate(user=scheduling_data['pupil'])
    pupil_session_read = client.get(
        f'/api/v1/sessions/{session.pk}/attendance/',
    )
    pupil_other_contract = client.get(
        '/api/v1/me/attendance/',
        {'from': '2026-11-16', 'to': '2026-11-16'},
    )
    assert pupil_session_read.status_code == status.HTTP_403_FORBIDDEN
    assert pupil_other_contract.status_code == status.HTTP_200_OK


@pytest.mark.django_db(transaction=True)
def test_parallel_attendance_corrections_keep_a_serial_audit_history(
    scheduling_data,
):
    session = create_attendance_session(scheduling_data)
    pupil = scheduling_data['pupil']
    Enrollment.objects.create(
        pupil=pupil,
        course=scheduling_data['course'],
        start_date=date(2026, 9, 1),
    )
    Attendance.objects.create(
        session=session,
        pupil=pupil,
        status=Attendance.Status.ABSENT,
    )
    barrier = Barrier(2)
    corrections = [
        (Attendance.Status.PRESENT, 'Birinchi tekshiruv'),
        (Attendance.Status.LATE, 'Ikkinchi tekshiruv'),
    ]

    def correct_attendance(correction):
        connections.close_all()
        client = APIClient()
        client.force_authenticate(user=scheduling_data['assigned_teacher'])
        barrier.wait(timeout=10)
        try:
            response = client.put(
                f'/api/v1/sessions/{session.pk}/attendance/',
                [{
                    'pupil': pupil.pk,
                    'status': correction[0],
                    'reason': correction[1],
                }],
                format='json',
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(correct_attendance, corrections))

    record = Attendance.objects.get(session=session, pupil=pupil)
    history = list(
        AttendanceAudit.objects.filter(attendance=record).order_by(
            'created_at',
            'pk',
        )
    )
    assert responses == [status.HTTP_200_OK, status.HTTP_200_OK]
    assert len(history) == 2
    assert history[0].old_status == Attendance.Status.ABSENT
    assert history[1].old_status == history[0].new_status
    assert record.status == history[1].new_status
