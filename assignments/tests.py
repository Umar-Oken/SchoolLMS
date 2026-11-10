from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from assignments.models import Assignment, Criterion, Level
from academics.models import TeachingAssignment
from learning.models import Topic


@pytest.fixture
def assignment_model_data(db):
    from datetime import date

    from accounts.models import User
    from academics.models import (
        AcademicYear,
        CourseOffering,
        SchoolClass,
        Subject,
        Term,
    )

    year = AcademicYear.objects.create(
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        label='Assignment test year',
    )
    term = Term.objects.create(
        year=year,
        order=1,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 12, 31),
    )
    school_class = SchoolClass.objects.create(
        year=year,
        grade_level=7,
        section='A',
    )
    subject = Subject.objects.create(code='ASSIGNMENT-TEST', title='Test')
    course = CourseOffering.objects.create(
        school_class=school_class,
        subject=subject,
    )
    other_school_class = SchoolClass.objects.create(
        year=year,
        grade_level=7,
        section='B',
    )
    other_course = CourseOffering.objects.create(
        school_class=other_school_class,
        subject=subject,
    )
    teacher = User.objects.create_user(
        username='assignment-teacher',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    other_teacher = User.objects.create_user(
        username='assignment-other-teacher',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    pupil = User.objects.create_user(
        username='assignment-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    for assigned_course, assigned_teacher in (
        (course, teacher),
        (other_course, other_teacher),
    ):
        TeachingAssignment.objects.create(
            course=assigned_course,
            teacher=assigned_teacher,
            start_date=timezone.localdate() - timedelta(days=1),
        )
    topic = Topic.objects.create(
        course=course,
        term=term,
        title='Test topic',
        objective='Test objective',
        order=1,
    )
    other_topic = Topic.objects.create(
        course=other_course,
        term=term,
        title='Other test topic',
        objective='Other test objective',
        order=1,
    )
    return {
        'topic': topic,
        'other_topic': other_topic,
        'teacher': teacher,
        'other_teacher': other_teacher,
        'pupil': pupil,
    }


def valid_assignment_fields():
    available_at = timezone.now()
    return {
        'title': 'Synthetic assignment',
        'instructions': 'Complete the exercise.',
        'available_at': available_at,
        'due_at': available_at + timedelta(days=1),
        'closes_at': available_at + timedelta(days=2),
        'max_score': Decimal('12.50'),
        'max_attempts': 2,
    }


def valid_assignment_payload(topic_id):
    values = valid_assignment_fields()
    return {
        'topic': topic_id,
        'title': values['title'],
        'instructions': values['instructions'],
        'available_at': values['available_at'].isoformat(),
        'due_at': values['due_at'].isoformat(),
        'closes_at': values['closes_at'].isoformat(),
        'max_score': str(values['max_score']),
        'max_attempts': values['max_attempts'],
    }


@pytest.mark.django_db
def test_assignment_valid_defaults_to_draft_without_publishing_topic(
    assignment_model_data,
):
    topic = assignment_model_data['topic']
    values = valid_assignment_fields()
    assignment = Assignment.objects.create(topic=topic, **values)

    assert assignment.state == Assignment.State.DRAFT
    topic.refresh_from_db()
    assert topic.state == Topic.State.DRAFT
    assert assignment.max_score == Decimal('12.50')
    assert all(
        timezone.is_aware(getattr(assignment, field))
        for field in ('available_at', 'due_at', 'closes_at')
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    'invalid_field',
    ('available_at', 'due_at'),
)
def test_assignment_rejects_reversed_deadlines(
    assignment_model_data,
    invalid_field,
):
    values = valid_assignment_fields()
    if invalid_field == 'available_at':
        values['available_at'] = values['due_at'] + timedelta(seconds=1)
    else:
        values['due_at'] = values['closes_at'] + timedelta(seconds=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        Assignment.objects.create(
            topic=assignment_model_data['topic'],
            **values,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('max_score', Decimal('0')),
        ('max_score', Decimal('-1.25')),
        ('max_attempts', 0),
    ],
)
def test_assignment_rejects_nonpositive_score_or_attempts(
    assignment_model_data,
    field,
    value,
):
    values = valid_assignment_fields()
    values[field] = value

    with pytest.raises(IntegrityError), transaction.atomic():
        Assignment.objects.create(
            topic=assignment_model_data['topic'],
            **values,
        )


@pytest.mark.django_db
def test_criterion_requires_positive_max_points_and_unique_assignment_position(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        **valid_assignment_fields(),
    )
    Criterion.objects.create(
        assignment=assignment,
        label='Evidence',
        max_points=Decimal('6.00'),
        position=1,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Criterion.objects.create(
            assignment=assignment,
            label='Reasoning',
            max_points=Decimal('3.00'),
            position=1,
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        Criterion.objects.create(
            assignment=assignment,
            label='Invalid',
            max_points=Decimal('0.00'),
            position=2,
        )


@pytest.mark.django_db
def test_level_requires_nonnegative_points_and_unique_criterion_position(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        **valid_assignment_fields(),
    )
    criterion = Criterion.objects.create(
        assignment=assignment,
        label='Evidence',
        max_points=Decimal('6.00'),
        position=1,
    )
    Level.objects.create(
        criterion=criterion,
        description='Meets expectations',
        points=Decimal('3.00'),
        position=1,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Level.objects.create(
            criterion=criterion,
            description='Another level at same position',
            points=Decimal('1.00'),
            position=1,
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        Level.objects.create(
            criterion=criterion,
            description='Negative points',
            points=Decimal('-0.01'),
            position=2,
        )


@pytest.mark.django_db
def test_level_validation_uses_its_own_criterion_max_points(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        **valid_assignment_fields(),
    )
    criterion_with_lower_max = Criterion.objects.create(
        assignment=assignment,
        label='Evidence',
        max_points=Decimal('6.00'),
        position=1,
    )
    Criterion.objects.create(
        assignment=assignment,
        label='Reasoning',
        max_points=Decimal('10.00'),
        position=2,
    )
    level = Level(
        criterion=criterion_with_lower_max,
        description='Exceeds its criterion maximum',
        points=Decimal('7.00'),
        position=1,
    )

    with pytest.raises(ValidationError, match='criterion max_points'):
        level.full_clean()


@pytest.mark.django_db
def test_assigned_teacher_can_create_and_read_own_draft(assignment_model_data):
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])
    payload = valid_assignment_payload(assignment_model_data['topic'].pk)

    created = client.post('/api/v1/assignments/', payload, format='json')

    assert created.status_code == status.HTTP_201_CREATED
    assert created.data['topic'] == assignment_model_data['topic'].pk
    assert created.data['state'] == Assignment.State.DRAFT
    assert not Assignment.objects.get(pk=created.data['id']).criteria.exists()

    detail = client.get(f"/api/v1/assignments/{created.data['id']}/")

    assert detail.status_code == status.HTTP_200_OK
    assert detail.data == created.data


@pytest.mark.django_db
def test_assignment_create_rejects_pupil_and_foreign_topic(
    assignment_model_data,
):
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=assignment_model_data['pupil'])
    pupil_response = pupil_client.post(
        '/api/v1/assignments/',
        valid_assignment_payload(assignment_model_data['topic'].pk),
        format='json',
    )
    assert pupil_response.status_code == status.HTTP_403_FORBIDDEN

    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    foreign_response = teacher_client.post(
        '/api/v1/assignments/',
        valid_assignment_payload(assignment_model_data['other_topic'].pk),
        format='json',
    )
    assert foreign_response.status_code == status.HTTP_404_NOT_FOUND
    assert Assignment.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    'injected_field',
    ('state', 'teacher', 'published_at'),
)
def test_assignment_create_rejects_server_owned_fields(
    assignment_model_data,
    injected_field,
):
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])
    payload = valid_assignment_payload(assignment_model_data['topic'].pk)
    payload[injected_field] = (
        Assignment.State.PUBLISHED
        if injected_field == 'state'
        else 123
    )

    response = client.post(
        '/api/v1/assignments/',
        payload,
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert injected_field in response.data
    assert Assignment.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('invalid_field', 'expected_error_field'),
    (
        ('available_after_due', 'due_at'),
        ('due_after_close', 'closes_at'),
    ),
)
def test_assignment_create_returns_clear_errors_for_invalid_date_order(
    assignment_model_data,
    invalid_field,
    expected_error_field,
):
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])
    payload = valid_assignment_payload(assignment_model_data['topic'].pk)
    if invalid_field == 'available_after_due':
        payload['available_at'] = (
            timezone.now() + timedelta(days=3)
        ).isoformat()
    else:
        payload['due_at'] = (
            timezone.now() + timedelta(days=3)
        ).isoformat()

    response = client.post(
        '/api/v1/assignments/',
        payload,
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert expected_error_field in response.data
    assert Assignment.objects.count() == 0


@pytest.mark.django_db
def test_assignment_detail_is_limited_to_teacher_owned_drafts(
    assignment_model_data,
):
    own_assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        **valid_assignment_fields(),
    )
    other_assignment = Assignment.objects.create(
        topic=assignment_model_data['other_topic'],
        **valid_assignment_fields(),
    )

    owner_client = APIClient()
    owner_client.force_authenticate(user=assignment_model_data['teacher'])
    assert owner_client.get(
        f'/api/v1/assignments/{own_assignment.pk}/',
    ).status_code == status.HTTP_200_OK
    assert owner_client.get(
        f'/api/v1/assignments/{other_assignment.pk}/',
    ).status_code == status.HTTP_404_NOT_FOUND

    pupil_client = APIClient()
    pupil_client.force_authenticate(user=assignment_model_data['pupil'])
    assert pupil_client.get(
        f'/api/v1/assignments/{own_assignment.pk}/',
    ).status_code == status.HTTP_403_FORBIDDEN

    other_assignment.state = Assignment.State.PUBLISHED
    other_assignment.save(update_fields=['state'])
    other_client = APIClient()
    other_client.force_authenticate(
        user=assignment_model_data['other_teacher'],
    )
    assert other_client.get(
        f'/api/v1/assignments/{other_assignment.pk}/',
    ).status_code == status.HTTP_404_NOT_FOUND
