from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    connection,
    connections,
    transaction,
)
from django.test.utils import CaptureQueriesContext
from django.db.models.deletion import ProtectedError
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from assignments.models import (
    Assignment,
    Criterion,
    CriterionScore,
    Grade,
    GradeRevision,
    Level,
    Submission,
    SubmissionAttachment,
    SubmissionIdempotencyReceipt,
    SubmissionReturn,
    SubmissionRevision,
)
from academics.models import Enrollment, MembershipStatus, TeachingAssignment
from files.models import Attachment
from learning.models import Topic


def run_parallel_api_request(
    *,
    barrier,
    user_id,
    method,
    path,
    data,
    headers=None,
):
    connections.close_all()
    try:
        user = User.objects.get(pk=user_id)
        client = APIClient()
        client.force_authenticate(user=user)
        barrier.wait(timeout=10)
        response = getattr(client, method)(
            path,
            data,
            format='json',
            **(headers or {}),
        )
        return response.status_code, response.data
    finally:
        connections.close_all()


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


def valid_rubric_payload():
    return {
        'criteria': [
            {
                'label': 'Dalillar',
                'max_points': '6.00',
                'position': 0,
                'levels': [
                    {
                        'description': 'To‘liq',
                        'points': '6.00',
                        'position': 0,
                    },
                ],
            },
        ],
    }


def create_rubric_with_points(assignment, points):
    for position, max_points in enumerate(points):
        criterion = Criterion.objects.create(
            assignment=assignment,
            label=f'Criterion {position}',
            max_points=Decimal(max_points),
            position=position,
        )
        Level.objects.create(
            criterion=criterion,
            description='Not demonstrated',
            points=Decimal('0.00'),
            position=0,
        )
        Level.objects.create(
            criterion=criterion,
            description='Demonstrated',
            points=Decimal(max_points),
            position=1,
        )


def create_published_assignment_for_pupil(assignment_model_data, pupil=None):
    pupil = pupil or assignment_model_data['pupil']
    topic = assignment_model_data['topic']
    topic.state = Topic.State.PUBLISHED
    topic.published_at = timezone.now()
    topic.save(update_fields=('state', 'published_at'))
    Enrollment.objects.create(
        pupil=pupil,
        course=topic.course,
        start_date=timezone.localdate() - timedelta(days=1),
    )
    assignment = Assignment.objects.create(
        topic=topic,
        state=Assignment.State.PUBLISHED,
        published_at=timezone.now(),
        **valid_assignment_fields(),
    )
    return assignment


def create_queue_submissions(assignment_model_data, count):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupils = [assignment_model_data['pupil']]
    pupils.extend(
        User.objects.create_user(
            username=f'queue-pupil-{number}',
            password='safe-test-password',
            role=User.Role.PUPIL,
            first_name=f'Queue {number}',
        )
        for number in range(1, count)
    )
    for pupil in pupils[1:]:
        Enrollment.objects.create(
            pupil=pupil,
            course=assignment.topic.course,
            start_date=timezone.localdate() - timedelta(days=1),
        )
    for pupil in pupils:
        submission = Submission.objects.create(
            assignment=assignment,
            pupil=pupil,
        )
        SubmissionRevision.objects.create(
            submission=submission,
            revision_number=1,
            submitted_version=1,
            submitted_text='Synthetic queue answer',
        )
    return assignment


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
    ).status_code == status.HTTP_404_NOT_FOUND

    other_assignment.state = Assignment.State.PUBLISHED
    other_assignment.save(update_fields=['state'])
    other_client = APIClient()
    other_client.force_authenticate(
        user=assignment_model_data['other_teacher'],
    )
    assert other_client.get(
        f'/api/v1/assignments/{other_assignment.pk}/',
    ).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_teacher_can_replace_draft_rubric_and_omitted_children_are_deleted(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        **valid_assignment_fields(),
    )
    first = Criterion.objects.create(
        assignment=assignment,
        label='Old first',
        max_points=Decimal('6.00'),
        position=0,
    )
    first_level = Level.objects.create(
        criterion=first,
        description='Old level',
        points=Decimal('2.00'),
        position=0,
    )
    omitted = Criterion.objects.create(
        assignment=assignment,
        label='Omitted',
        max_points=Decimal('4.00'),
        position=2,
    )
    Level.objects.create(
        criterion=omitted,
        description='Cascade delete',
        points=Decimal('1.00'),
        position=0,
    )
    second = Criterion.objects.create(
        assignment=assignment,
        label='Old second',
        max_points=Decimal('3.00'),
        position=1,
    )
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])

    response = client.put(
        f'/api/v1/assignments/{assignment.pk}/rubric/',
        {
            'criteria': [
                {
                    'id': first.pk,
                    'label': 'Updated first',
                    'max_points': '6.00',
                    'position': 1,
                    'levels': [
                        {
                            'id': first_level.pk,
                            'description': 'Updated level',
                            'points': '6.00',
                            'position': 0,
                        },
                        {
                            'description': 'New level',
                            'points': '3.00',
                            'position': 1,
                        },
                    ],
                },
                {
                    'label': 'New criterion',
                    'max_points': '2.00',
                    'position': 2,
                    'levels': [],
                },
                {
                    'id': second.pk,
                    'label': 'Updated second',
                    'max_points': '3.00',
                    'position': 0,
                    'levels': [],
                },
            ],
        },
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data['criteria']) == 3
    first.refresh_from_db()
    assert first.label == 'Updated first'
    assert first.position == 1
    assert first.levels.count() == 2
    second.refresh_from_db()
    assert second.position == 0
    assert not Criterion.objects.filter(pk=omitted.pk).exists()
    assert not Level.objects.filter(criterion=omitted).exists()
    assert response.data['criteria'][0]['id'] == second.pk


@pytest.mark.django_db
def test_rubric_rejects_foreign_child_ids_and_level_points_over_criterion_limit(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        **valid_assignment_fields(),
    )
    other_assignment = Assignment.objects.create(
        topic=assignment_model_data['other_topic'],
        **valid_assignment_fields(),
    )
    foreign_criterion = Criterion.objects.create(
        assignment=other_assignment,
        label='Foreign',
        max_points=Decimal('5.00'),
        position=0,
    )
    own_criterion = Criterion.objects.create(
        assignment=assignment,
        label='Own',
        max_points=Decimal('5.00'),
        position=0,
    )
    foreign_level = Level.objects.create(
        criterion=foreign_criterion,
        description='Foreign level',
        points=Decimal('1.00'),
        position=0,
    )
    own_level = Level.objects.create(
        criterion=own_criterion,
        description='Own level',
        points=Decimal('1.00'),
        position=0,
    )
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])
    url = f'/api/v1/assignments/{assignment.pk}/rubric/'

    foreign_id_response = client.put(
        url,
        {
            'criteria': [
                {
                    'id': foreign_criterion.pk,
                    'label': 'Injected',
                    'max_points': '5.00',
                    'position': 0,
                    'levels': [],
                },
            ],
        },
        format='json',
    )
    assert foreign_id_response.status_code == status.HTTP_400_BAD_REQUEST

    foreign_level_response = client.put(
        url,
        {
            'criteria': [
                {
                    'id': own_criterion.pk,
                    'label': 'Own',
                    'max_points': '5.00',
                    'position': 0,
                    'levels': [
                        {
                            'id': foreign_level.pk,
                            'description': 'Injected',
                            'points': '1.00',
                            'position': 0,
                        },
                    ],
                },
            ],
        },
        format='json',
    )
    assert foreign_level_response.status_code == status.HTTP_400_BAD_REQUEST

    over_limit_response = client.put(
        url,
        {
            'criteria': [
                {
                    'id': own_criterion.pk,
                    'label': 'Own',
                    'max_points': '5.00',
                    'position': 0,
                    'levels': [
                        {
                            'id': own_level.pk,
                            'description': 'Over limit',
                            'points': '5.01',
                            'position': 0,
                        },
                    ],
                },
            ],
        },
        format='json',
    )
    assert over_limit_response.status_code == status.HTTP_400_BAD_REQUEST
    own_criterion.refresh_from_db()
    own_level.refresh_from_db()
    assert own_criterion.label == 'Own'
    assert own_level.description == 'Own level'
    assert Criterion.objects.filter(assignment=assignment).count() == 1


@pytest.mark.django_db
def test_rubric_write_rolls_back_all_changes_on_child_database_error(
    assignment_model_data,
    monkeypatch,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        **valid_assignment_fields(),
    )
    criterion = Criterion.objects.create(
        assignment=assignment,
        label='Original',
        max_points=Decimal('5.00'),
        position=0,
    )
    level = Level.objects.create(
        criterion=criterion,
        description='Original level',
        points=Decimal('2.00'),
        position=0,
    )

    def fail_create(**kwargs):
        raise IntegrityError('synthetic child write failure')

    monkeypatch.setattr(Level.objects, 'create', fail_create)
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])
    with pytest.raises(IntegrityError, match='synthetic child write failure'):
        client.put(
            f'/api/v1/assignments/{assignment.pk}/rubric/',
            {
                'criteria': [
                    {
                        'id': criterion.pk,
                        'label': 'Changed',
                        'max_points': '5.00',
                        'position': 0,
                        'levels': [],
                    },
                    {
                        'label': 'New criterion',
                        'max_points': '2.00',
                        'position': 1,
                        'levels': [
                            {
                                'description': 'New level',
                                'points': '1.00',
                                'position': 0,
                            },
                        ],
                    },
                ],
            },
            format='json',
        )

    criterion.refresh_from_db()
    level.refresh_from_db()
    assert criterion.label == 'Original'
    assert level.description == 'Original level'
    assert Criterion.objects.filter(assignment=assignment).count() == 1
    assert Level.objects.filter(criterion=criterion).count() == 1


@pytest.mark.django_db
def test_rubric_write_requires_teacher_scope_and_draft(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        **valid_assignment_fields(),
    )
    url = f'/api/v1/assignments/{assignment.pk}/rubric/'
    payload = valid_rubric_payload()

    pupil_client = APIClient()
    pupil_client.force_authenticate(user=assignment_model_data['pupil'])
    assert pupil_client.put(
        url,
        payload,
        format='json',
    ).status_code == status.HTTP_403_FORBIDDEN

    other_teacher_client = APIClient()
    other_teacher_client.force_authenticate(
        user=assignment_model_data['other_teacher'],
    )
    assert other_teacher_client.put(
        url,
        payload,
        format='json',
    ).status_code == status.HTTP_404_NOT_FOUND

    assignment.state = Assignment.State.PUBLISHED
    assignment.save(update_fields=['state'])
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    assert teacher_client.put(
        url,
        payload,
        format='json',
    ).status_code == status.HTTP_404_NOT_FOUND
    assert not Criterion.objects.filter(assignment=assignment).exists()


@pytest.mark.django_db
def test_assignment_publish_rejects_rubric_sum_that_differs_from_max_score(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        max_score=Decimal('10.00'),
        **{
            key: value
            for key, value in valid_assignment_fields().items()
            if key != 'max_score'
        },
    )
    create_rubric_with_points(assignment, ('6.00', '3.00', '2.00'))
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])

    response = client.post(
        f'/api/v1/assignments/{assignment.pk}/publish/',
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'criteria' in response.data
    assignment.refresh_from_db()
    assert assignment.state == Assignment.State.DRAFT
    assert assignment.published_at is None
    assert assignment.rubric_version == 0


@pytest.mark.django_db
def test_assignment_publish_accepts_exact_rubric_and_is_idempotent(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        max_score=Decimal('10.00'),
        **{
            key: value
            for key, value in valid_assignment_fields().items()
            if key != 'max_score'
        },
    )
    create_rubric_with_points(assignment, ('6.00', '3.00', '1.00'))
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])
    publish_url = f'/api/v1/assignments/{assignment.pk}/publish/'

    first_response = client.post(publish_url, {}, format='json')

    assert first_response.status_code == status.HTTP_200_OK
    assert first_response.data['state'] == Assignment.State.PUBLISHED
    assert first_response.data['published_at'] is not None
    assert first_response.data['rubric_version'] == 1
    published_at = first_response.data['published_at']
    criterion_count = Criterion.objects.filter(
        assignment=assignment,
    ).count()
    level_count = Level.objects.filter(
        criterion__assignment=assignment,
    ).count()

    second_response = client.post(publish_url, {}, format='json')

    assert second_response.status_code == status.HTTP_200_OK
    assert second_response.data['published_at'] == published_at
    assert second_response.data['rubric_version'] == 1
    assert Criterion.objects.filter(assignment=assignment).count() == criterion_count
    assert Level.objects.filter(
        criterion__assignment=assignment,
    ).count() == level_count
    assert client.put(
        f'/api/v1/assignments/{assignment.pk}/rubric/',
        valid_rubric_payload(),
        format='json',
    ).status_code == status.HTTP_404_NOT_FOUND
    assert client.patch(
        f'/api/v1/assignments/{assignment.pk}/',
        {'title': 'Changed after publish'},
        format='json',
    ).status_code == status.HTTP_405_METHOD_NOT_ALLOWED


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('field', 'value', 'error_key'),
    (
        ('title', '  ', 'title'),
        ('instructions', '  ', 'instructions'),
    ),
)
def test_assignment_publish_requires_title_and_instructions(
    assignment_model_data,
    field,
    value,
    error_key,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        max_score=Decimal('10.00'),
        **{
            **{
                key: value
                for key, value in valid_assignment_fields().items()
                if key != 'max_score'
            },
            field: value,
        },
    )
    create_rubric_with_points(assignment, ('10.00',))
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])

    response = client.post(
        f'/api/v1/assignments/{assignment.pk}/publish/',
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert error_key in response.data
    assignment.refresh_from_db()
    assert assignment.state == Assignment.State.DRAFT


@pytest.mark.django_db
def test_assignment_publish_requires_a_zero_point_level_per_criterion(
    assignment_model_data,
):
    assignment = Assignment.objects.create(
        topic=assignment_model_data['topic'],
        max_score=Decimal('10.00'),
        **{
            key: value
            for key, value in valid_assignment_fields().items()
            if key != 'max_score'
        },
    )
    criterion = Criterion.objects.create(
        assignment=assignment,
        label='Criterion',
        max_points=Decimal('10.00'),
        position=0,
    )
    Level.objects.create(
        criterion=criterion,
        description='Positive level only',
        points=Decimal('10.00'),
        position=0,
    )
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])

    response = client.post(
        f'/api/v1/assignments/{assignment.pk}/publish/',
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert any(key.endswith('_levels') for key in response.data)
    assignment.refresh_from_db()
    assert assignment.state == Assignment.State.DRAFT


@pytest.mark.django_db
def test_pupil_assignment_list_and_detail_are_limited_to_enrolled_published_scope(
    assignment_model_data,
):
    pupil = assignment_model_data['pupil']
    topic = assignment_model_data['topic']
    topic.state = Topic.State.PUBLISHED
    topic.published_at = timezone.now()
    topic.save(update_fields=('state', 'published_at'))
    Enrollment.objects.create(
        pupil=pupil,
        course=topic.course,
        start_date=timezone.localdate() - timedelta(days=1),
    )
    assignment = Assignment.objects.create(
        topic=topic,
        state=Assignment.State.PUBLISHED,
        published_at=timezone.now(),
        **valid_assignment_fields(),
    )
    create_rubric_with_points(assignment, ('12.50',))

    other_pupil = User.objects.create_user(
        username='assignment-unenrolled-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    other_topic = assignment_model_data['other_topic']
    other_topic.state = Topic.State.PUBLISHED
    other_topic.published_at = timezone.now()
    other_topic.save(update_fields=('state', 'published_at'))
    other_assignment = Assignment.objects.create(
        topic=other_topic,
        state=Assignment.State.PUBLISHED,
        published_at=timezone.now(),
        **valid_assignment_fields(),
    )
    draft_topic = Topic.objects.create(
        course=topic.course,
        term=topic.term,
        title='Draft topic',
        objective='Not visible',
        order=2,
    )
    draft_assignment = Assignment.objects.create(
        topic=draft_topic,
        **valid_assignment_fields(),
    )

    client = APIClient()
    client.force_authenticate(user=pupil)

    response = client.get(
        f'/api/v1/assignments/?course={topic.course_id}',
    )

    assert response.status_code == status.HTTP_200_OK
    assert [item['id'] for item in response.data['results']] == [assignment.pk]
    assert response.data['results'][0]['course'] == topic.course_id
    assert response.data['results'][0]['instructions'] == assignment.instructions
    assert len(response.data['results'][0]['rubric']) == 1
    assert client.get(
        f'/api/v1/assignments/{assignment.pk}/',
    ).status_code == status.HTTP_200_OK
    assert client.get(
        f'/api/v1/assignments/{other_assignment.pk}/',
    ).status_code == status.HTTP_404_NOT_FOUND
    assert client.get(
        f'/api/v1/assignments/{draft_assignment.pk}/',
    ).status_code == status.HTTP_404_NOT_FOUND

    other_client = APIClient()
    other_client.force_authenticate(user=other_pupil)
    assert other_client.get(
        f'/api/v1/assignments/{assignment.pk}/',
    ).status_code == status.HTTP_404_NOT_FOUND

    wrong_course_response = client.get(
        f'/api/v1/assignments/?course={other_topic.course_id}',
    )
    assert wrong_course_response.status_code == status.HTTP_200_OK
    assert wrong_course_response.data['results'] == []
    assert client.get(
        '/api/v1/assignments/?course=not-an-id',
    ).status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('time_offset', 'expected_status', 'can_submit'),
    (
        (-1, 'upcoming', False),
        (0, 'on_time', True),
        (3600, 'on_time', True),
        (3601, 'late', True),
        (7200, 'late', True),
        (7201, 'closed', False),
    ),
)
def test_pupil_assignment_time_boundaries(
    assignment_model_data,
    monkeypatch,
    time_offset,
    expected_status,
    can_submit,
):
    pupil = assignment_model_data['pupil']
    topic = assignment_model_data['topic']
    topic.state = Topic.State.PUBLISHED
    topic.published_at = timezone.now()
    topic.save(update_fields=('state', 'published_at'))
    Enrollment.objects.create(
        pupil=pupil,
        course=topic.course,
        start_date=timezone.localdate() - timedelta(days=1),
    )
    now = timezone.now()
    assignment = Assignment(
        topic=topic,
        title='Boundary test',
        instructions='Check the time boundary.',
        available_at=now,
        due_at=now + timedelta(seconds=3600),
        closes_at=now + timedelta(seconds=7200),
        max_score=Decimal('10.00'),
        max_attempts=1,
        state=Assignment.State.PUBLISHED,
        published_at=now,
    )
    assignment.save()
    clock = now + timedelta(seconds=time_offset)
    monkeypatch.setattr('assignments.time.timezone.now', lambda: clock)
    client = APIClient()
    client.force_authenticate(user=pupil)

    response = client.get(f'/api/v1/assignments/{assignment.pk}/')

    assert response.status_code == status.HTTP_200_OK
    assert response.data['availability_status'] == expected_status
    assert response.data['can_submit'] is can_submit


@pytest.mark.django_db
def test_my_draft_get_is_empty_without_creating_submission(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['pupil'])

    response = client.get(
        f'/api/v1/assignments/{assignment.pk}/my-draft/',
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        'submission_id': None,
        'assignment': assignment.pk,
        'draft_text': '',
        'draft_version': 0,
        'attachment_ids': [],
    }
    assert not assignment.submissions.exists()


@pytest.mark.django_db
def test_my_draft_repeated_put_updates_one_container_and_never_creates_revision(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupil = assignment_model_data['pupil']
    client = APIClient()
    client.force_authenticate(user=pupil)
    url = f'/api/v1/assignments/{assignment.pk}/my-draft/'

    first_response = client.put(
        url,
        {'draft_text': 'Birinchi qoralama', 'draft_version': 0},
        format='json',
    )

    assert first_response.status_code == status.HTTP_200_OK
    submission_id = first_response.data['submission_id']
    assert first_response.data['draft_version'] == 1
    assert first_response.data['draft_text'] == 'Birinchi qoralama'

    second_response = client.put(
        url,
        {'draft_text': 'Yangilangan qoralama', 'draft_version': 1},
        format='json',
    )

    assert second_response.status_code == status.HTTP_200_OK
    assert second_response.data['submission_id'] == submission_id
    assert second_response.data['draft_version'] == 2
    assert second_response.data['draft_text'] == 'Yangilangan qoralama'
    assert assignment.submissions.count() == 1
    assert not assignment.submissions.get().revisions.exists()


@pytest.mark.django_db
def test_my_draft_rejects_stale_version_without_overwriting_newer_text(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['pupil'])
    url = f'/api/v1/assignments/{assignment.pk}/my-draft/'
    first = client.put(
        url,
        {'draft_text': 'Yangi matn', 'draft_version': 0},
        format='json',
    )
    assert first.status_code == status.HTTP_200_OK

    stale = client.put(
        url,
        {'draft_text': 'Eskirgan matn', 'draft_version': 0},
        format='json',
    )

    assert stale.status_code == status.HTTP_409_CONFLICT
    submission = assignment.submissions.get()
    assert submission.draft_text == 'Yangi matn'
    assert submission.draft_version == 1


@pytest.mark.django_db
def test_my_draft_never_accepts_client_pupil_identity(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    other_pupil = User.objects.create_user(
        username='assignment-draft-target',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['pupil'])

    response = client.put(
        f'/api/v1/assignments/{assignment.pk}/my-draft/',
        {
            'pupil': other_pupil.pk,
            'draft_text': 'Boshqa o‘quvchi nomidan',
            'draft_version': 0,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'pupil' in response.data
    assert not Submission.objects.filter(
        assignment=assignment,
        pupil=other_pupil,
    ).exists()


@pytest.mark.django_db
def test_my_draft_requires_current_active_enrollment_and_published_assignment(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupil = assignment_model_data['pupil']
    Enrollment.objects.filter(
        pupil=pupil,
        course=assignment.topic.course,
    ).update(
        status=MembershipStatus.CLOSED,
        end_date=timezone.localdate(),
    )
    client = APIClient()
    client.force_authenticate(user=pupil)

    response = client.put(
        f'/api/v1/assignments/{assignment.pk}/my-draft/',
        {'draft_text': 'No active enrollment', 'draft_version': 0},
        format='json',
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert not Submission.objects.filter(assignment=assignment).exists()


@pytest.mark.django_db
def test_submission_unique_per_assignment_and_pupil(assignment_model_data):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupil = assignment_model_data['pupil']
    Submission.objects.create(assignment=assignment, pupil=pupil)

    with pytest.raises(IntegrityError), transaction.atomic():
        Submission.objects.create(assignment=assignment, pupil=pupil)


@pytest.mark.django_db
def test_submission_revision_is_immutable_and_not_created_for_draft_updates(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupil = assignment_model_data['pupil']
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Editable draft',
    )
    revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Immutable snapshot',
        submitted_at=timezone.now(),
        is_late=False,
    )

    revision.submitted_text = 'Changed snapshot'
    with pytest.raises(ValidationError, match='o‘zgartirilmaydi'):
        revision.save()
    with pytest.raises(ValidationError, match='o‘chirilmaydi'):
        revision.delete()


def create_clean_attachment(owner, *, scan_status=Attachment.ScanStatus.CLEAN):
    return Attachment.objects.create(
        owner=owner,
        original_name='answer.pdf',
        size=12,
        detected_mime='application/pdf',
        scan_status=scan_status,
    )


@pytest.mark.django_db
def test_my_draft_replaces_attachment_ids_atomically_and_rejects_bad_files(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupil = assignment_model_data['pupil']
    first = create_clean_attachment(pupil)
    replacement = create_clean_attachment(pupil)
    pending = create_clean_attachment(
        pupil,
        scan_status=Attachment.ScanStatus.PENDING_SCAN,
    )
    foreign = create_clean_attachment(assignment_model_data['teacher'])
    client = APIClient()
    client.force_authenticate(user=pupil)
    url = f'/api/v1/assignments/{assignment.pk}/my-draft/'

    created = client.put(
        url,
        {
            'draft_text': 'Birinchi matn',
            'draft_version': 0,
            'attachment_ids': [first.pk],
        },
        format='json',
    )

    assert created.status_code == status.HTTP_200_OK
    assert created.data['attachment_ids'] == [first.pk]
    invalid_payloads = (
        [foreign.pk],
        [pending.pk],
        [first.pk, first.pk],
        [first.pk, replacement.pk, pending.pk, foreign.pk],
    )
    for attachment_ids in invalid_payloads:
        rejected = client.put(
            url,
            {
                'draft_text': 'Yaroqsiz update',
                'draft_version': 1,
                'attachment_ids': attachment_ids,
            },
            format='json',
        )
        assert rejected.status_code == status.HTTP_400_BAD_REQUEST
        submission = assignment.submissions.get(pupil=pupil)
        assert submission.draft_text == 'Birinchi matn'
        assert submission.draft_version == 1
        assert list(submission.attachments.values_list('pk', flat=True)) == [
            first.pk,
        ]

    replaced = client.put(
        url,
        {
            'draft_text': 'Yangilangan matn',
            'draft_version': 1,
            'attachment_ids': [replacement.pk],
        },
        format='json',
    )
    assert replaced.status_code == status.HTTP_200_OK
    assert replaced.data['attachment_ids'] == [replacement.pk]
    assert not SubmissionAttachment.objects.filter(
        attachment=first,
    ).exists()
    another_assignment = Assignment.objects.create(
        topic=assignment.topic,
        state=Assignment.State.PUBLISHED,
        published_at=timezone.now(),
        **valid_assignment_fields(),
    )
    reused = client.put(
        f'/api/v1/assignments/{another_assignment.pk}/my-draft/',
        {
            'draft_text': '',
            'draft_version': 0,
            'attachment_ids': [first.pk],
        },
        format='json',
    )
    assert reused.status_code == status.HTTP_200_OK
    assert reused.data['attachment_ids'] == [first.pk]


@pytest.mark.django_db
def test_my_draft_stale_version_does_not_replace_attachment_links(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupil = assignment_model_data['pupil']
    current = create_clean_attachment(pupil)
    replacement = create_clean_attachment(pupil)
    client = APIClient()
    client.force_authenticate(user=pupil)
    url = f'/api/v1/assignments/{assignment.pk}/my-draft/'

    first = client.put(
        url,
        {
            'draft_text': 'Current',
            'draft_version': 0,
            'attachment_ids': [current.pk],
        },
        format='json',
    )
    stale = client.put(
        url,
        {
            'draft_text': 'Stale',
            'draft_version': 0,
            'attachment_ids': [replacement.pk],
        },
        format='json',
    )

    assert first.status_code == status.HTTP_200_OK
    assert stale.status_code == status.HTTP_409_CONFLICT
    submission = assignment.submissions.get(pupil=pupil)
    assert submission.draft_text == 'Current'
    assert list(submission.attachments.values_list('pk', flat=True)) == [
        current.pk,
    ]


@pytest.mark.django_db
def test_attachment_download_allows_pupil_draft_and_teacher_revision_only(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupil = assignment_model_data['pupil']
    teacher = assignment_model_data['teacher']
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Draft',
    )
    draft_attachment = create_clean_attachment(pupil)
    revision_attachment = create_clean_attachment(pupil)
    SubmissionAttachment.objects.create(
        submission=submission,
        attachment=draft_attachment,
    )
    revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Submitted',
        submitted_at=timezone.now(),
        is_late=False,
    )
    SubmissionAttachment.objects.create(
        revision=revision,
        attachment=revision_attachment,
    )

    storage = storages['private']
    keys = [str(draft_attachment.key), str(revision_attachment.key)]
    for key in keys:
        storage.save(key, ContentFile(b'%PDF-1.7\nprivate content'))

    try:
        pupil_client = APIClient()
        pupil_client.force_authenticate(user=pupil)
        teacher_client = APIClient()
        teacher_client.force_authenticate(user=teacher)
        draft_url = f'/api/v1/attachments/{draft_attachment.pk}/download/'
        revision_url = (
            f'/api/v1/attachments/{revision_attachment.pk}/download/'
        )

        assert pupil_client.get(draft_url).status_code == status.HTTP_200_OK
        assert pupil_client.get(revision_url).status_code == status.HTTP_200_OK
        assert teacher_client.get(draft_url).status_code == status.HTTP_404_NOT_FOUND
        assert teacher_client.get(revision_url).status_code == status.HTTP_200_OK
    finally:
        for key in keys:
            storage.delete(key)


@pytest.mark.django_db
def test_attachment_cannot_be_reused_by_another_draft_or_revision(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    pupil = assignment_model_data['pupil']
    other_pupil = User.objects.create_user(
        username='assignment-attachment-other-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    Enrollment.objects.create(
        pupil=other_pupil,
        course=assignment.topic.course,
        start_date=timezone.localdate() - timedelta(days=1),
    )
    attachment = create_clean_attachment(pupil)
    SubmissionAttachment.objects.create(
        submission=Submission.objects.create(
            assignment=assignment,
            pupil=other_pupil,
        ),
        attachment=attachment,
    )
    client = APIClient()
    client.force_authenticate(user=pupil)

    response = client.put(
        f'/api/v1/assignments/{assignment.pk}/my-draft/',
        {
            'draft_text': 'No reuse',
            'draft_version': 0,
            'attachment_ids': [attachment.pk],
        },
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Submission.objects.filter(
        assignment=assignment,
        pupil=pupil,
    ).exists()

    other_submission = Submission.objects.get(
        assignment=assignment,
        pupil=other_pupil,
    )
    revision_attachment = create_clean_attachment(pupil)
    revision = SubmissionRevision.objects.create(
        submission=other_submission,
        revision_number=1,
        submitted_version=0,
        submitted_text='Revision',
        submitted_at=timezone.now(),
        is_late=False,
    )
    SubmissionAttachment.objects.create(
        revision=revision,
        attachment=revision_attachment,
    )
    revision_reuse = client.put(
        f'/api/v1/assignments/{assignment.pk}/my-draft/',
        {
            'draft_text': 'No revision reuse',
            'draft_version': 0,
            'attachment_ids': [revision_attachment.pk],
        },
        format='json',
    )
    assert revision_reuse.status_code == status.HTTP_400_BAD_REQUEST
    assert not Submission.objects.filter(
        assignment=assignment,
        pupil=pupil,
    ).exists()


@pytest.mark.django_db
def test_submit_work_at_due_and_closes_boundaries_sets_late_from_server_clock(
    assignment_model_data,
    monkeypatch,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    due = timezone.now()
    assignment.available_at = due - timedelta(hours=1)
    assignment.due_at = due
    assignment.closes_at = due + timedelta(hours=1)
    assignment.save()
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Boundary answer',
        draft_version=1,
    )
    client = APIClient()
    client.force_authenticate(user=pupil)
    monkeypatch.setattr('assignments.services.timezone.now', lambda: due)

    due_response = client.post(
        f'/api/v1/assignments/{assignment.pk}/submit/',
        {'draft_version': 1},
        format='json',
        HTTP_IDEMPOTENCY_KEY='due-boundary',
    )

    assert due_response.status_code == status.HTTP_201_CREATED
    assert due_response.data['is_late'] is False
    assert parse_datetime(due_response.data['submitted_at']) == due
    assert submission.revisions.get().submitted_at == due

    another_pupil = User.objects.create_user(
        username='assignment-close-boundary-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    Enrollment.objects.create(
        pupil=another_pupil,
        course=assignment.topic.course,
        start_date=timezone.localdate() - timedelta(days=1),
    )
    another_submission = Submission.objects.create(
        assignment=assignment,
        pupil=another_pupil,
        draft_text='At close',
        draft_version=3,
    )
    monkeypatch.setattr(
        'assignments.services.timezone.now',
        lambda: assignment.closes_at,
    )
    close_client = APIClient()
    close_client.force_authenticate(user=another_pupil)

    close_response = close_client.post(
        f'/api/v1/assignments/{assignment.pk}/submit/',
        {'draft_version': 3},
        format='json',
        HTTP_IDEMPOTENCY_KEY='close-boundary',
    )

    assert close_response.status_code == status.HTTP_201_CREATED
    assert close_response.data['is_late'] is True
    assert another_submission.revisions.get().submitted_at == assignment.closes_at


@pytest.mark.django_db
def test_submit_rejects_empty_early_closed_and_stale_work(
    assignment_model_data,
    monkeypatch,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    client = APIClient()
    client.force_authenticate(user=pupil)
    url = f'/api/v1/assignments/{assignment.pk}/submit/'

    empty = client.post(
        url,
        {'draft_version': 0},
        format='json',
        HTTP_IDEMPOTENCY_KEY='empty',
    )
    assert empty.status_code == status.HTTP_400_BAD_REQUEST

    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Has answer',
        draft_version=2,
    )

    stale = client.post(
        url,
        {'draft_version': 1},
        format='json',
        HTTP_IDEMPOTENCY_KEY='stale',
    )
    assert stale.status_code == status.HTTP_409_CONFLICT
    assert not submission.revisions.exists()

    future = assignment.available_at - timedelta(seconds=1)
    monkeypatch.setattr('assignments.services.timezone.now', lambda: future)
    early = client.post(
        url,
        {'draft_version': 2},
        format='json',
        HTTP_IDEMPOTENCY_KEY='early',
    )
    assert early.status_code == status.HTTP_400_BAD_REQUEST
    assert not submission.revisions.exists()

    closed = assignment.closes_at + timedelta(microseconds=1)
    monkeypatch.setattr('assignments.services.timezone.now', lambda: closed)
    late_closed = client.post(
        url,
        {'draft_version': 2},
        format='json',
        HTTP_IDEMPOTENCY_KEY='late-closed',
    )
    assert late_closed.status_code == status.HTTP_400_BAD_REQUEST
    assert not submission.revisions.exists()


@pytest.mark.django_db
def test_submit_freezes_text_and_moves_files_to_revision_and_blocks_duplicate(
    assignment_model_data,
    monkeypatch,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    assignment.max_attempts = 2
    assignment.save(update_fields=('max_attempts',))
    attachment = create_clean_attachment(pupil)
    client = APIClient()
    client.force_authenticate(user=pupil)
    draft_url = f'/api/v1/assignments/{assignment.pk}/my-draft/'
    submit_url = f'/api/v1/assignments/{assignment.pk}/submit/'

    draft = client.put(
        draft_url,
        {
            'draft_text': 'Frozen answer',
            'draft_version': 0,
            'attachment_ids': [attachment.pk],
        },
        format='json',
    )
    submitted = client.post(
        submit_url,
        {'draft_version': draft.data['draft_version']},
        format='json',
        HTTP_IDEMPOTENCY_KEY='submit-first',
    )
    duplicate = client.post(
        submit_url,
        {'draft_version': draft.data['draft_version']},
        format='json',
        HTTP_IDEMPOTENCY_KEY='submit-second',
    )

    assert submitted.status_code == status.HTTP_201_CREATED
    assert submitted.data['revision_number'] == 1
    assert submitted.data['attachment_ids'] == [attachment.pk]
    assert duplicate.status_code == status.HTTP_409_CONFLICT
    revision = SubmissionRevision.objects.get()
    link = SubmissionAttachment.objects.get(attachment=attachment)
    assert revision.submitted_text == 'Frozen answer'
    assert link.revision_id == revision.pk
    assert link.submission_id is None

    returned = APIClient()
    returned.force_authenticate(user=assignment_model_data['teacher'])
    return_response = returned.post(
        f'/api/v1/submissions/{draft.data["submission_id"]}/return/',
        {'reason': 'Open for editing.'},
        format='json',
    )
    assert return_response.status_code == status.HTTP_201_CREATED
    edit = client.put(
        draft_url,
        {
            'draft_text': 'Later draft edit',
            'draft_version': return_response.data['draft_version'],
        },
        format='json',
    )
    assert edit.status_code == status.HTTP_200_OK
    assert edit.data['draft_text'] == 'Later draft edit'
    assert edit.data['attachment_ids'] == []
    revision.refresh_from_db()
    link.refresh_from_db()
    assert revision.submitted_text == 'Frozen answer'
    assert link.revision_id == revision.pk
    assert link.submission_id is None

    reassigned = client.put(
        draft_url,
        {
            'draft_text': 'Try reusing submitted file',
            'draft_version': edit.data['draft_version'],
            'attachment_ids': [attachment.pk],
        },
        format='json',
    )
    assert reassigned.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_submit_respects_max_attempts_but_allows_new_draft_version(
    assignment_model_data,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    assignment.max_attempts = 1
    assignment.save(update_fields=('max_attempts',))
    client = APIClient()
    client.force_authenticate(user=pupil)
    draft_url = f'/api/v1/assignments/{assignment.pk}/my-draft/'
    submit_url = f'/api/v1/assignments/{assignment.pk}/submit/'

    draft = client.put(
        draft_url,
        {'draft_text': 'Attempt one', 'draft_version': 0},
        format='json',
    )
    first = client.post(
        submit_url,
        {'draft_version': draft.data['draft_version']},
        format='json',
        HTTP_IDEMPOTENCY_KEY='attempt-one',
    )
    assert first.status_code == status.HTTP_201_CREATED

    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    returned = teacher_client.post(
        f'/api/v1/submissions/{draft.data["submission_id"]}/return/',
        {'reason': 'Attempt limit test.'},
        format='json',
    )
    assert returned.status_code == status.HTTP_201_CREATED
    updated = client.put(
        draft_url,
        {
            'draft_text': 'Attempt two',
            'draft_version': returned.data['draft_version'],
        },
        format='json',
    )
    blocked = client.post(
        submit_url,
        {'draft_version': updated.data['draft_version']},
        format='json',
        HTTP_IDEMPOTENCY_KEY='attempt-two',
    )

    assert updated.status_code == status.HTTP_200_OK
    assert blocked.status_code == status.HTTP_400_BAD_REQUEST
    assert SubmissionRevision.objects.filter(
        submission__assignment=assignment,
    ).count() == 1


@pytest.mark.django_db
def test_submit_requires_key_and_replays_same_receipt_after_deadline(
    assignment_model_data,
    monkeypatch,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Retry-safe answer',
        draft_version=4,
    )
    client = APIClient()
    client.force_authenticate(user=pupil)
    url = f'/api/v1/assignments/{assignment.pk}/submit/'

    missing_key = client.post(
        url,
        {'draft_version': 4},
        format='json',
    )
    assert missing_key.status_code == status.HTTP_400_BAD_REQUEST
    assert not submission.revisions.exists()

    key = 'client-submit-unique-001'
    first = client.post(
        url,
        {'draft_version': 4},
        format='json',
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert first.status_code == status.HTTP_201_CREATED
    assert submission.revisions.count() == 1
    assert SubmissionIdempotencyReceipt.objects.filter(
        assignment=assignment,
        pupil=pupil,
        key=key,
    ).count() == 1

    monkeypatch.setattr(
        'assignments.services.timezone.now',
        lambda: assignment.closes_at + timedelta(seconds=1),
    )
    replay = client.post(
        url,
        {'draft_version': 4},
        format='json',
        HTTP_IDEMPOTENCY_KEY=key,
    )

    assert replay.status_code == status.HTTP_201_CREATED
    assert replay.data == first.data
    assert submission.revisions.count() == 1


@pytest.mark.django_db
def test_submit_idempotency_key_rejects_different_input_and_scopes_actor(
    assignment_model_data,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Fingerprint answer',
        draft_version=5,
    )
    client = APIClient()
    client.force_authenticate(user=pupil)
    url = f'/api/v1/assignments/{assignment.pk}/submit/'
    key = 'fingerprint-key'

    original = client.post(
        url,
        {'draft_version': 5},
        format='json',
        HTTP_IDEMPOTENCY_KEY=key,
    )
    mismatch = client.post(
        url,
        {'draft_version': 4},
        format='json',
        HTTP_IDEMPOTENCY_KEY=key,
    )

    assert original.status_code == status.HTTP_201_CREATED
    assert mismatch.status_code == status.HTTP_409_CONFLICT
    assert submission.revisions.count() == 1
    receipt = SubmissionIdempotencyReceipt.objects.get()
    assert receipt.response_payload == original.data

    other_assignment = Assignment.objects.create(
        topic=assignment.topic,
        state=Assignment.State.PUBLISHED,
        published_at=timezone.now(),
        **valid_assignment_fields(),
    )
    Submission.objects.create(
        assignment=other_assignment,
        pupil=pupil,
        draft_text='Same key, separate assignment',
        draft_version=5,
    )
    separate_assignment = client.post(
        f'/api/v1/assignments/{other_assignment.pk}/submit/',
        {'draft_version': 5},
        format='json',
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert separate_assignment.status_code == status.HTTP_201_CREATED
    assert SubmissionIdempotencyReceipt.objects.filter(
        pupil=pupil,
        key=key,
    ).count() == 2


@pytest.mark.django_db
def test_assigned_teacher_returns_work_and_pupil_resubmits_new_revision(
    assignment_model_data,
):
    pupil = assignment_model_data['pupil']
    teacher = assignment_model_data['teacher']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    assignment.max_attempts = 2
    assignment.save(update_fields=('max_attempts',))
    attachment = create_clean_attachment(pupil)
    SubmissionAttachment.objects.create(
        submission=Submission.objects.create(
            assignment=assignment,
            pupil=pupil,
            draft_text='First submitted answer',
            draft_version=1,
        ),
        attachment=attachment,
    )
    submission = assignment.submissions.get(pupil=pupil)
    first_revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='First submitted answer',
        submitted_at=timezone.now(),
        is_late=False,
    )
    SubmissionAttachment.objects.filter(
        submission=submission,
    ).update(submission=None, revision=first_revision)
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=teacher)
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=pupil)
    return_url = f'/api/v1/submissions/{submission.pk}/return/'

    returned = teacher_client.post(
        return_url,
        {'reason': 'Iltimos, ikkinchi dalilni ham qo‘shing.'},
        format='json',
    )

    assert returned.status_code == status.HTTP_201_CREATED
    assert returned.data['revision_id'] == first_revision.pk
    assert returned.data['revision_number'] == 1
    assert returned.data['draft_version'] == 2
    assert returned.data['reason'] == 'Iltimos, ikkinchi dalilni ham qo‘shing.'
    first_revision.refresh_from_db()
    assert first_revision.submitted_text == 'First submitted answer'
    assert first_revision.return_record.returned_by == teacher
    assert first_revision.return_record.reason == returned.data['reason']

    edit = pupil_client.put(
        f'/api/v1/assignments/{assignment.pk}/my-draft/',
        {
            'draft_text': 'Second answer with more evidence',
            'draft_version': returned.data['draft_version'],
        },
        format='json',
    )
    resubmitted = pupil_client.post(
        f'/api/v1/assignments/{assignment.pk}/submit/',
        {'draft_version': edit.data['draft_version']},
        format='json',
        HTTP_IDEMPOTENCY_KEY='resubmit-after-return',
    )

    assert edit.status_code == status.HTTP_200_OK
    assert resubmitted.status_code == status.HTTP_201_CREATED
    assert resubmitted.data['revision_number'] == 2
    assert submission.revisions.count() == 2
    first_revision.refresh_from_db()
    assert first_revision.submitted_text == 'First submitted answer'
    assert first_revision.attachment_links.get().attachment_id == attachment.pk


@pytest.mark.django_db
def test_only_assigned_teacher_can_return_and_return_does_not_extend_deadline(
    assignment_model_data,
    monkeypatch,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Submitted text',
        draft_version=1,
    )
    revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Submitted text',
        submitted_at=timezone.now(),
        is_late=False,
    )
    other_teacher_client = APIClient()
    other_teacher_client.force_authenticate(
        user=assignment_model_data['other_teacher'],
    )

    forbidden = other_teacher_client.post(
        f'/api/v1/submissions/{submission.pk}/return/',
        {'reason': 'Wrong course'},
        format='json',
    )
    assert forbidden.status_code == status.HTTP_404_NOT_FOUND
    assert not SubmissionReturn.objects.filter(revision=revision).exists()

    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    returned = teacher_client.post(
        f'/api/v1/submissions/{submission.pk}/return/',
        {'reason': 'Revise after deadline'},
        format='json',
    )
    assert returned.status_code == status.HTTP_201_CREATED
    assert returned.data['draft_version'] == 2

    monkeypatch.setattr(
        'assignments.services.timezone.now',
        lambda: assignment.closes_at + timedelta(seconds=1),
    )
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=pupil)
    rejected = pupil_client.post(
        f'/api/v1/assignments/{assignment.pk}/submit/',
        {'draft_version': 2},
        format='json',
        HTTP_IDEMPOTENCY_KEY='after-deadline-resubmit',
    )

    assert rejected.status_code == status.HTTP_400_BAD_REQUEST
    assert submission.revisions.count() == 1


@pytest.mark.django_db
def test_draft_edit_is_locked_until_teacher_returns_latest_revision(
    assignment_model_data,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Submitted',
        draft_version=1,
    )
    SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Submitted',
        submitted_at=timezone.now(),
        is_late=False,
    )
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=pupil)

    locked = pupil_client.put(
        f'/api/v1/assignments/{assignment.pk}/my-draft/',
        {'draft_text': 'Premature edit', 'draft_version': 1},
        format='json',
    )

    assert locked.status_code == status.HTTP_409_CONFLICT
    submission.refresh_from_db()
    assert submission.draft_text == 'Submitted'
    assert submission.draft_version == 1


@pytest.mark.django_db
def test_grade_is_unique_per_revision_and_protects_submitted_history(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=assignment_model_data['pupil'],
        draft_text='Submitted answer',
    )
    revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Submitted answer',
        submitted_at=timezone.now(),
    )
    grade = Grade.objects.create(
        revision=revision,
        grader=assignment_model_data['teacher'],
        total=Decimal('0.00'),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Grade.objects.create(
            revision=revision,
            grader=assignment_model_data['teacher'],
        )

    with pytest.raises(ProtectedError), transaction.atomic():
        SubmissionRevision.objects.filter(pk=revision.pk).delete()
    assert Grade.objects.get(pk=grade.pk).revision_id == revision.pk


@pytest.mark.django_db
def test_criterion_score_rejects_negative_points_and_duplicate_criterion(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    criterion = Criterion.objects.create(
        assignment=assignment,
        label='Evidence',
        max_points=Decimal('6.00'),
        position=0,
    )
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=assignment_model_data['pupil'],
        draft_text='Submitted answer',
    )
    revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Submitted answer',
        submitted_at=timezone.now(),
    )
    grade = Grade.objects.create(
        revision=revision,
        grader=assignment_model_data['teacher'],
    )
    CriterionScore.objects.create(
        grade=grade,
        criterion=criterion,
        points=Decimal('0.00'),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        CriterionScore.objects.create(
            grade=grade,
            criterion=criterion,
            points=Decimal('1.00'),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        CriterionScore.objects.create(
            grade=grade,
            criterion=Criterion.objects.create(
                assignment=assignment,
                label='Negative test',
                max_points=Decimal('1.00'),
                position=1,
            ),
            points=Decimal('-0.01'),
        )


@pytest.mark.django_db
def test_grade_revision_protects_grade_history_and_records_old_new_reason(
    assignment_model_data,
):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=assignment_model_data['pupil'],
        draft_text='Submitted answer',
    )
    revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Submitted answer',
        submitted_at=timezone.now(),
    )
    grade = Grade.objects.create(
        revision=revision,
        grader=assignment_model_data['teacher'],
        total=Decimal('8.00'),
    )
    grade_revision = GradeRevision.objects.create(
        grade=grade,
        changed_by=assignment_model_data['teacher'],
        old_total=None,
        new_total=Decimal('8.00'),
        reason='Initial draft grade',
    )

    with pytest.raises(ProtectedError), transaction.atomic():
        Grade.objects.filter(pk=grade.pk).delete()
    grade_revision.refresh_from_db()
    assert grade_revision.old_total is None
    assert grade_revision.new_total == Decimal('8.00')
    assert grade_revision.reason == 'Initial draft grade'


def create_gradeable_revision(assignment_model_data):
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    assignment.max_score = Decimal('10.00')
    assignment.save(update_fields=('max_score',))
    TeachingAssignment.objects.filter(
        course=assignment.topic.course,
        teacher=assignment_model_data['teacher'],
    ).update(can_grade=True)
    criteria = []
    for position, max_points in enumerate(('6.00', '3.00', '1.00')):
        criteria.append(Criterion.objects.create(
            assignment=assignment,
            label=f'Criterion {position + 1}',
            max_points=Decimal(max_points),
            position=position,
        ))
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=assignment_model_data['pupil'],
        draft_text='Answer for grading',
        draft_version=1,
    )
    revision = SubmissionRevision.objects.create(
        submission=submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Answer for grading',
        submitted_at=timezone.now(),
    )
    return assignment, revision, criteria


def grade_payload(criteria, *, version=0, points=None):
    points = points or ('5.00', '2.00', '1.00')
    return {
        'version': version,
        'feedback': 'Yaxshi asoslangan javob.',
        'criterion_scores': [
            {
                'criterion_id': criterion.pk,
                'points': points[index],
                'feedback': f'Izoh {index + 1}',
            }
            for index, criterion in enumerate(criteria)
        ],
    }


@pytest.mark.django_db
def test_teacher_writes_rubric_grade_and_server_calculates_total(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])

    response = client.put(
        f'/api/v1/revisions/{revision.pk}/grade/',
        grade_payload(criteria),
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data['revision_id'] == revision.pk
    assert response.data['state'] == Grade.State.DRAFT
    assert response.data['total'] == '8.00'
    assert response.data['version'] == 1
    assert response.data['feedback'] == 'Yaxshi asoslangan javob.'
    assert len(response.data['criterion_scores']) == 3
    grade = Grade.objects.get(revision=revision)
    assert grade.grader == assignment_model_data['teacher']
    assert grade.total == Decimal('8.00')
    assert assignment.max_score == Decimal('10.00')


@pytest.mark.django_db
def test_grade_requires_all_owned_criteria_and_points_within_limits(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])
    url = f'/api/v1/revisions/{revision.pk}/grade/'
    valid = grade_payload(criteria)
    missing = {**valid, 'criterion_scores': valid['criterion_scores'][:-1]}
    other_assignment = Assignment.objects.create(
        topic=assignment_model_data['other_topic'],
        state=Assignment.State.PUBLISHED,
        published_at=timezone.now(),
        **valid_assignment_fields(),
    )
    foreign_criterion = Criterion.objects.create(
        assignment=other_assignment,
        label='Foreign criterion',
        max_points=Decimal('2.00'),
        position=0,
    )
    foreign = {
        **valid,
        'criterion_scores': [
            *valid['criterion_scores'][:-1],
            {
                'criterion_id': foreign_criterion.pk,
                'points': '1.00',
            },
        ],
    }
    over_max = grade_payload(criteria, points=('6.01', '2.00', '1.00'))

    for payload in (missing, foreign, over_max):
        response = client.put(url, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Grade.objects.filter(revision=revision).exists()
        assert not CriterionScore.objects.filter(
            grade__revision=revision,
        ).exists()


@pytest.mark.django_db
def test_grade_draft_version_and_pupil_write_are_enforced(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    url = f'/api/v1/revisions/{revision.pk}/grade/'
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    first = teacher_client.put(
        url,
        grade_payload(criteria),
        format='json',
    )
    stale_payload = grade_payload(criteria, version=0, points=('4.00', '2.00', '1.00'))
    stale = teacher_client.put(url, stale_payload, format='json')

    assert first.status_code == status.HTTP_200_OK
    assert stale.status_code == status.HTTP_409_CONFLICT
    grade = Grade.objects.get(revision=revision)
    assert grade.total == Decimal('8.00')
    assert grade.version == 1

    pupil_client = APIClient()
    pupil_client.force_authenticate(user=assignment_model_data['pupil'])
    pupil_write = pupil_client.put(
        url,
        grade_payload(criteria),
        format='json',
    )
    assert pupil_write.status_code == status.HTTP_403_FORBIDDEN
    pupil_read = pupil_client.get(url)
    assert pupil_read.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_grade_publish_is_atomic_idempotent_and_visible_only_to_owner(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    draft_response = teacher_client.put(
        f'/api/v1/revisions/{revision.pk}/grade/',
        grade_payload(criteria),
        format='json',
    )
    assert draft_response.status_code == status.HTTP_200_OK
    assert draft_response.data['version'] == 1

    publish_url = f'/api/v1/revisions/{revision.pk}/grade/publish/'
    payload = {'grade_version': 1, 'revision_version': 1}
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=assignment_model_data['pupil'])
    assert pupil_client.get(
        f'/api/v1/revisions/{revision.pk}/grade/',
    ).status_code == status.HTTP_404_NOT_FOUND

    published = teacher_client.post(publish_url, payload, format='json')
    assert published.status_code == status.HTTP_200_OK
    assert published.data['state'] == Grade.State.PUBLISHED
    assert published.data['version'] == 2
    assert published.data['total'] == '8.00'
    assert published.data['published_at'] is not None
    assert len(published.data['criterion_scores']) == 3
    assert GradeRevision.objects.filter(
        grade__revision=revision,
        reason='Bahoni pupilga e’lon qilish.',
    ).count() == 1

    duplicate = teacher_client.post(publish_url, payload, format='json')
    assert duplicate.status_code == status.HTTP_200_OK
    assert duplicate.data == published.data
    assert GradeRevision.objects.filter(
        grade__revision=revision,
        reason='Bahoni pupilga e’lon qilish.',
    ).count() == 1

    pupil_read = pupil_client.get(
        f'/api/v1/revisions/{revision.pk}/grade/',
    )
    assert pupil_read.status_code == status.HTTP_200_OK
    assert pupil_read.data == published.data

    other_pupil = User.objects.create_user(
        username='grade-other-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    other_pupil_client = APIClient()
    other_pupil_client.force_authenticate(user=other_pupil)
    assert other_pupil_client.get(
        f'/api/v1/revisions/{revision.pk}/grade/',
    ).status_code == status.HTTP_404_NOT_FOUND

    other_teacher_client = APIClient()
    other_teacher_client.force_authenticate(
        user=assignment_model_data['other_teacher'],
    )
    assert other_teacher_client.post(
        publish_url,
        payload,
        format='json',
    ).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_grade_publish_rejects_stale_versions_and_incomplete_scores(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    draft = teacher_client.put(
        f'/api/v1/revisions/{revision.pk}/grade/',
        grade_payload(criteria),
        format='json',
    )
    assert draft.status_code == status.HTTP_200_OK
    publish_url = f'/api/v1/revisions/{revision.pk}/grade/publish/'

    stale_grade = teacher_client.post(
        publish_url,
        {'grade_version': 0, 'revision_version': 1},
        format='json',
    )
    stale_revision = teacher_client.post(
        publish_url,
        {'grade_version': 1, 'revision_version': 2},
        format='json',
    )
    assert stale_grade.status_code == status.HTTP_409_CONFLICT
    assert stale_revision.status_code == status.HTTP_409_CONFLICT

    assignment.max_score = Decimal('11.00')
    assignment.save(update_fields=('max_score',))
    invalid_rubric = teacher_client.post(
        publish_url,
        {'grade_version': 1, 'revision_version': 1},
        format='json',
    )
    assert invalid_rubric.status_code == status.HTTP_400_BAD_REQUEST
    assignment.max_score = Decimal('10.00')
    assignment.save(update_fields=('max_score',))

    grade = Grade.objects.get(revision=revision)
    CriterionScore.objects.filter(
        grade=grade,
        criterion=criteria[0],
    ).delete()
    incomplete = teacher_client.post(
        publish_url,
        {'grade_version': 1, 'revision_version': 1},
        format='json',
    )
    assert incomplete.status_code == status.HTTP_400_BAD_REQUEST
    grade.refresh_from_db()
    assert grade.state == Grade.State.DRAFT
    assert grade.published_at is None
    assert not GradeRevision.objects.filter(
        grade=grade,
        reason='Bahoni pupilga e’lon qilish.',
    ).exists()


@pytest.mark.django_db
def test_published_grade_regrade_keeps_full_immutable_audit(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    draft = teacher_client.put(
        f'/api/v1/revisions/{revision.pk}/grade/',
        grade_payload(criteria),
        format='json',
    )
    assert draft.status_code == status.HTTP_200_OK
    publish_url = f'/api/v1/revisions/{revision.pk}/grade/publish/'
    published = teacher_client.post(
        publish_url,
        {'grade_version': 1, 'revision_version': 1},
        format='json',
    )
    assert published.status_code == status.HTTP_200_OK
    regrade_url = f'/api/v1/revisions/{revision.pk}/grade/regrade/'
    reason = 'Dalil mezoni qayta ko‘rib chiqildi.'
    changed_scores = [
        {
            'criterion_id': criteria[0].pk,
            'points': '6.00',
            'feedback': 'To‘liq dalil.',
        },
        {
            'criterion_id': criteria[1].pk,
            'points': '1.00',
            'feedback': 'Qisman izoh.',
        },
        {
            'criterion_id': criteria[2].pk,
            'points': '0.00',
            'feedback': 'Ushbu mezon bajarilmadi.',
        },
    ]
    response = teacher_client.post(
        regrade_url,
        {
            'expected_version': published.data['version'],
            'criterion_scores': changed_scores,
            'reason': reason,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data['state'] == Grade.State.PUBLISHED
    assert response.data['total'] == '7.00'
    assert response.data['version'] == published.data['version'] + 1
    assert response.data['published_at'] != published.data['published_at']
    grade = Grade.objects.get(revision=revision)
    audit = GradeRevision.objects.get(
        grade=grade,
        reason=reason,
    )
    assert audit.changed_by == assignment_model_data['teacher']
    assert audit.old_total == Decimal('8.00')
    assert audit.new_total == Decimal('7.00')
    assert audit.created_at is not None
    assert audit.old_snapshot['grader_id'] == assignment_model_data['teacher'].pk
    assert audit.old_snapshot['total'] == '8.00'
    assert audit.old_snapshot['version'] == 2
    assert audit.old_snapshot['criterion_scores'][0]['points'] == '5.00'
    assert audit.new_snapshot['grader_id'] == assignment_model_data['teacher'].pk
    assert audit.new_snapshot['total'] == '7.00'
    assert audit.new_snapshot['version'] == 3
    assert audit.new_snapshot['criterion_scores'][0]['points'] == '6.00'

    pupil_client = APIClient()
    pupil_client.force_authenticate(user=assignment_model_data['pupil'])
    pupil_grade = pupil_client.get(
        f'/api/v1/revisions/{revision.pk}/grade/',
    )
    assert pupil_grade.status_code == status.HTTP_200_OK
    assert pupil_grade.data['total'] == '7.00'

    audit.reason = 'Mutated'
    with pytest.raises(ValidationError):
        audit.save()
    with pytest.raises(ValidationError):
        audit.delete()


@pytest.mark.django_db
def test_grade_regrade_rejects_missing_reason_stale_version_and_other_teacher(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    teacher_client.put(
        f'/api/v1/revisions/{revision.pk}/grade/',
        grade_payload(criteria),
        format='json',
    )
    teacher_client.post(
        f'/api/v1/revisions/{revision.pk}/grade/publish/',
        {'grade_version': 1, 'revision_version': 1},
        format='json',
    )
    url = f'/api/v1/revisions/{revision.pk}/grade/regrade/'
    payload = {
        'expected_version': 2,
        'criterion_scores': grade_payload(criteria)['criterion_scores'],
        'reason': 'Tekshirilgan sabab.',
    }

    stale = teacher_client.post(
        url,
        {**payload, 'expected_version': 1},
        format='json',
    )
    no_reason = teacher_client.post(
        url,
        {**payload, 'reason': '   '},
        format='json',
    )
    client_total = teacher_client.post(
        url,
        {**payload, 'total': '999.00'},
        format='json',
    )
    assert stale.status_code == status.HTTP_409_CONFLICT
    assert no_reason.status_code == status.HTTP_400_BAD_REQUEST
    assert client_total.status_code == status.HTTP_400_BAD_REQUEST
    assert GradeRevision.objects.filter(
        grade__revision=revision,
        reason='Tekshirilgan sabab.',
    ).count() == 0

    other_teacher_client = APIClient()
    other_teacher_client.force_authenticate(
        user=assignment_model_data['other_teacher'],
    )
    assert other_teacher_client.post(
        url,
        payload,
        format='json',
    ).status_code == status.HTTP_404_NOT_FOUND
    grade = Grade.objects.get(revision=revision)
    assert grade.total == Decimal('8.00')
    assert grade.version == 2
    assert GradeRevision.objects.filter(grade=grade).count() == 1


def create_gradebook_assignment(topic, title):
    assignment = Assignment.objects.create(
        topic=topic,
        state=Assignment.State.PUBLISHED,
        published_at=timezone.now(),
        **{
            **valid_assignment_fields(),
            'title': title,
            'max_score': Decimal('10.00'),
        },
    )
    criterion = Criterion.objects.create(
        assignment=assignment,
        label='Criterion',
        max_points=Decimal('10.00'),
        position=0,
    )
    return assignment, criterion


def create_published_test_grade(revision, teacher, criterion, points):
    grade = Grade.objects.create(
        revision=revision,
        grader=teacher,
        state=Grade.State.PUBLISHED,
        total=Decimal(points),
        feedback=f'Grade {points}',
        version=2,
        published_at=timezone.now(),
    )
    CriterionScore.objects.create(
        grade=grade,
        criterion=criterion,
        points=Decimal(points),
        feedback=f'Criterion {points}',
    )
    return grade


@pytest.mark.django_db
def test_pupil_gradebook_distinguishes_statuses_and_selects_latest_published(
    assignment_model_data,
):
    graded_assignment, graded_revision, graded_criteria = create_gradeable_revision(
        assignment_model_data,
    )
    topic = graded_assignment.topic
    create_gradebook_assignment(topic, 'Missing')
    ungraded, _ = create_gradebook_assignment(topic, 'Ungraded')
    returned, _ = create_gradebook_assignment(topic, 'Returned')

    ungraded_submission = Submission.objects.create(
        assignment=ungraded,
        pupil=assignment_model_data['pupil'],
    )
    SubmissionRevision.objects.create(
        submission=ungraded_submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Not graded yet',
    )
    returned_submission = Submission.objects.create(
        assignment=returned,
        pupil=assignment_model_data['pupil'],
    )
    returned_revision = SubmissionRevision.objects.create(
        submission=returned_submission,
        revision_number=1,
        submitted_version=1,
        submitted_text='Returned answer',
    )
    SubmissionReturn.objects.create(
        revision=returned_revision,
        returned_by=assignment_model_data['teacher'],
        reason='Please add evidence.',
    )

    create_published_test_grade(
        graded_revision,
        assignment_model_data['teacher'],
        graded_criteria[0],
        '5.00',
    )
    newer_revision = SubmissionRevision.objects.create(
        submission=graded_revision.submission,
        revision_number=2,
        submitted_version=2,
        submitted_text='Resubmitted answer',
    )
    create_published_test_grade(
        newer_revision,
        assignment_model_data['teacher'],
        graded_criteria[0],
        '0.00',
    )

    client = APIClient()
    client.force_authenticate(user=assignment_model_data['pupil'])
    response = client.get('/api/v1/me/gradebook/')

    assert response.status_code == status.HTTP_200_OK
    entries = {item['title']: item for item in response.data['results']}
    assert entries['Missing']['status'] == 'missing'
    assert entries['Missing']['current_grade'] is None
    assert entries['Ungraded']['status'] == 'ungraded'
    assert entries['Ungraded']['current_grade'] is None
    assert entries['Returned']['status'] == 'returned'
    history_entry = entries['Synthetic assignment']
    assert history_entry['status'] == 'graded'
    assert history_entry['current_grade']['revision_number'] == 2
    assert history_entry['current_grade']['total'] == '0.00'
    assert len(history_entry['history']) == 1
    assert history_entry['history'][0]['revision_number'] == 1
    assert history_entry['history'][0]['total'] == '5.00'

    filtered = client.get(
        f'/api/v1/me/gradebook/?course={topic.course_id}&term={topic.term_id}',
    )
    assert filtered.status_code == status.HTTP_200_OK
    assert filtered.data['count'] == response.data['count']
    assert client.get('/api/v1/me/gradebook/?course=nope').status_code == 400
    assert client.get(
        f'/api/v1/me/gradebook/?course={assignment_model_data["other_topic"].course_id}',
    ).data['results'] == []
    assert all(
        item['course_id'] == topic.course_id
        for item in filtered.data['results']
    )


@pytest.mark.django_db
def test_pupil_gradebook_is_scoped_to_authenticated_pupil(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    create_published_test_grade(
        revision,
        assignment_model_data['teacher'],
        criteria[0],
        '0.00',
    )
    other_pupil = User.objects.create_user(
        username='gradebook-other-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    Enrollment.objects.create(
        pupil=other_pupil,
        course=assignment.topic.course,
        start_date=timezone.localdate() - timedelta(days=1),
    )
    other_client = APIClient()
    other_client.force_authenticate(user=other_pupil)

    response = other_client.get('/api/v1/me/gradebook/')

    assert response.status_code == status.HTTP_200_OK
    assert response.data['count'] == 1
    assert response.data['results'][0]['assignment_id'] == assignment.pk
    assert response.data['results'][0]['status'] == 'missing'
    assert response.data['results'][0]['current_grade'] is None

    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    assert teacher_client.get(
        '/api/v1/me/gradebook/',
    ).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_teacher_course_gradebook_and_submission_queue_are_scoped_and_minimal(
    assignment_model_data,
):
    assignment, revision, criteria = create_gradeable_revision(
        assignment_model_data,
    )
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=assignment_model_data['teacher'])
    course_url = (
        f'/api/v1/courses/{assignment_model_data["topic"].course_id}/gradebook/'
    )
    assignment_url = (
        f'/api/v1/assignments/{assignment.pk}/submissions/'
    )

    pending = teacher_client.get(course_url)
    assert pending.status_code == status.HTTP_200_OK
    assert pending.data['count'] == 1
    row = pending.data['results'][0]
    assert row['status'] == 'submitted'
    assert row['pupil'] == {
        'id': assignment_model_data['pupil'].pk,
        'first_name': '',
        'last_name': '',
    }
    assert row['latest_revision']['id'] == revision.pk
    assert row['latest_revision']['grade'] is None
    assert 'draft_text' not in row
    assert 'email' not in row['pupil']
    assert teacher_client.get(assignment_url).data == pending.data

    draft = teacher_client.put(
        f'/api/v1/revisions/{revision.pk}/grade/',
        grade_payload(criteria),
        format='json',
    )
    assert draft.status_code == status.HTTP_200_OK
    draft_row = teacher_client.get(assignment_url).data['results'][0]
    assert draft_row['status'] == 'draft'
    assert draft_row['latest_revision']['grade']['state'] == Grade.State.DRAFT

    published = teacher_client.post(
        f'/api/v1/revisions/{revision.pk}/grade/publish/',
        {'grade_version': 1, 'revision_version': 1},
        format='json',
    )
    assert published.status_code == status.HTTP_200_OK
    graded = teacher_client.get(course_url).data['results'][0]
    assert graded['status'] == 'graded'
    assert graded['latest_revision']['grade']['state'] == Grade.State.PUBLISHED
    assert graded['latest_revision']['grade']['total'] == '8.00'

    other_teacher_client = APIClient()
    other_teacher_client.force_authenticate(
        user=assignment_model_data['other_teacher'],
    )
    assert other_teacher_client.get(course_url).status_code == 404
    assert other_teacher_client.get(assignment_url).status_code == 404

    pupil_client = APIClient()
    pupil_client.force_authenticate(user=assignment_model_data['pupil'])
    assert pupil_client.get(course_url).status_code == 403
    assert pupil_client.get(assignment_url).status_code == 403


@pytest.mark.django_db
def test_teacher_gradebook_query_count_is_constant_from_five_to_fifty_rows(
    assignment_model_data,
):
    assignment = create_queue_submissions(assignment_model_data, 5)
    client = APIClient()
    client.force_authenticate(user=assignment_model_data['teacher'])
    url = f'/api/v1/courses/{assignment.topic.course_id}/gradebook/?page_size=100'

    with CaptureQueriesContext(connection) as five_queries:
        five_response = client.get(url)

    assert five_response.status_code == status.HTTP_200_OK
    assert five_response.data['count'] == 5
    original_rows = {
        row['pupil']['id']: row
        for row in five_response.data['results']
    }

    for number in range(5, 50):
        pupil = User.objects.create_user(
            username=f'queue-pupil-{number}',
            password='safe-test-password',
            role=User.Role.PUPIL,
            first_name=f'Queue {number}',
        )
        Enrollment.objects.create(
            pupil=pupil,
            course=assignment.topic.course,
            start_date=timezone.localdate() - timedelta(days=1),
        )
        submission = Submission.objects.create(
            assignment=assignment,
            pupil=pupil,
        )
        SubmissionRevision.objects.create(
            submission=submission,
            revision_number=1,
            submitted_version=1,
            submitted_text='Synthetic queue answer',
        )

    with CaptureQueriesContext(connection) as fifty_queries:
        fifty_response = client.get(url)

    assert fifty_response.status_code == status.HTTP_200_OK
    assert fifty_response.data['count'] == 50
    assert len(five_queries) == len(fifty_queries) == 4
    assert {
        row['pupil']['id']: row
        for row in fifty_response.data['results']
        if row['pupil']['id'] in original_rows
    } == original_rows


@pytest.mark.django_db
@pytest.mark.skipif(
    connection.vendor != 'postgresql',
    reason='P076 index plan requires PostgreSQL EXPLAIN.',
)
def test_assignment_topic_state_index_is_used_for_published_gradebook_scope(
    assignment_model_data,
):
    topic = assignment_model_data['topic']
    Assignment.objects.bulk_create([
        Assignment(
            topic=topic,
            state=Assignment.State.DRAFT,
            **{
                **valid_assignment_fields(),
                'title': f'Draft assignment {number}',
                'instructions': 'Synthetic draft',
            },
        )
        for number in range(500)
    ])
    published = Assignment.objects.create(
        topic=topic,
        state=Assignment.State.PUBLISHED,
        published_at=timezone.now(),
        **{
            **valid_assignment_fields(),
            'title': 'Published assignment',
            'instructions': 'Synthetic published assignment',
        },
    )
    with connection.cursor() as cursor:
        cursor.execute('ANALYZE assignments_assignment')

    plan = Assignment.objects.filter(
        topic_id=topic.pk,
        state=Assignment.State.PUBLISHED,
    ).explain()

    assert published.pk
    assert 'assignments_topic_state_idx' in plan


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != 'postgresql',
    reason='Parallel lock tests require PostgreSQL.',
)
def test_parallel_submit_with_same_key_creates_one_revision(
    assignment_model_data,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    submission = Submission.objects.create(
        assignment=assignment,
        pupil=pupil,
        draft_text='Concurrent answer',
        draft_version=1,
    )
    barrier = Barrier(2)
    request_args = {
        'barrier': barrier,
        'user_id': pupil.pk,
        'method': 'post',
        'path': f'/api/v1/assignments/{assignment.pk}/submit/',
        'data': {'draft_version': 1},
        'headers': {'HTTP_IDEMPOTENCY_KEY': 'parallel-submit-key'},
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run_parallel_api_request, **request_args)
            for _ in range(2)
        ]
        results = [future.result(timeout=30) for future in futures]

    assert [result[0] for result in results] == [
        status.HTTP_201_CREATED,
        status.HTTP_201_CREATED,
    ]
    assert results[0][1] == results[1][1]
    assert submission.revisions.count() == 1
    assert SubmissionIdempotencyReceipt.objects.filter(
        assignment=assignment,
        pupil=pupil,
        key='parallel-submit-key',
    ).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != 'postgresql',
    reason='Parallel lock tests require PostgreSQL.',
)
def test_parallel_first_draft_put_creates_one_submission(
    assignment_model_data,
):
    pupil = assignment_model_data['pupil']
    assignment = create_published_assignment_for_pupil(assignment_model_data)
    barrier = Barrier(2)
    request_args = {
        'barrier': barrier,
        'user_id': pupil.pk,
        'method': 'put',
        'path': f'/api/v1/assignments/{assignment.pk}/my-draft/',
        'data': {
            'draft_text': 'Parallel draft',
            'draft_version': 0,
        },
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run_parallel_api_request, **request_args)
            for _ in range(2)
        ]
        results = [future.result(timeout=30) for future in futures]

    assert sorted(result[0] for result in results) == [
        status.HTTP_200_OK,
        status.HTTP_409_CONFLICT,
    ]
    assert Submission.objects.filter(
        assignment=assignment,
        pupil=pupil,
    ).count() == 1
    submission = Submission.objects.get(
        assignment=assignment,
        pupil=pupil,
    )
    assert submission.draft_version == 1
