from datetime import date, timedelta
import uuid

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.utils import timezone
from django.test import override_settings
from rest_framework import status
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
from academics.services import close_teaching_assignment
from files.models import Attachment
from learning.models import Completion, Resource, Topic


@pytest.fixture
def topic_api_data(db):
    year = AcademicYear.objects.create(
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        label='2026–2027',
    )
    other_year = AcademicYear.objects.create(
        start_date=date(2027, 9, 1),
        end_date=date(2028, 6, 30),
        label='2027–2028',
    )
    term = Term.objects.create(
        year=year,
        order=1,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 11, 30),
    )
    other_year_term = Term.objects.create(
        year=other_year,
        order=1,
        start_date=date(2027, 9, 1),
        end_date=date(2027, 11, 30),
    )
    class_a = SchoolClass.objects.create(
        year=year,
        grade_level=7,
        section='A',
    )
    class_b = SchoolClass.objects.create(
        year=year,
        grade_level=7,
        section='B',
    )
    subject = Subject.objects.create(code='TOPIC-MATH', title='Matematika')
    course_a = CourseOffering.objects.create(
        school_class=class_a,
        subject=subject,
    )
    course_b = CourseOffering.objects.create(
        school_class=class_b,
        subject=subject,
    )
    teacher_a = User.objects.create_user(
        username='topic-teacher-a',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    teacher_b = User.objects.create_user(
        username='topic-teacher-b',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    pupil_a = User.objects.create_user(
        username='topic-pupil-a',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    pupil_b = User.objects.create_user(
        username='topic-pupil-b',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    for course, teacher in ((course_a, teacher_a), (course_b, teacher_b)):
        TeachingAssignment.objects.create(
            course=course,
            teacher=teacher,
            start_date=date(2026, 9, 1),
        )
    for course, pupil in ((course_a, pupil_a), (course_b, pupil_b)):
        Enrollment.objects.create(
            course=course,
            pupil=pupil,
            start_date=date(2026, 9, 1),
        )

    draft = Topic.objects.create(
        course=course_a,
        term=term,
        title='Draft topic',
        objective='Draft objective',
        order=1,
    )
    published = Topic.objects.create(
        course=course_a,
        term=term,
        title='Published topic',
        objective='Published objective',
        order=2,
        state=Topic.State.PUBLISHED,
        published_at=timezone.now(),
    )
    other_course_topic = Topic.objects.create(
        course=course_b,
        term=term,
        title='Other course topic',
        objective='Other course objective',
        order=1,
    )
    return {
        'term': term,
        'other_year_term': other_year_term,
        'course_a': course_a,
        'course_b': course_b,
        'teacher_a': teacher_a,
        'teacher_b': teacher_b,
        'pupil_a': pupil_a,
        'pupil_b': pupil_b,
        'draft': draft,
        'published': published,
        'other_course_topic': other_course_topic,
    }


@pytest.mark.django_db
def test_topic_rejects_term_from_another_academic_year(topic_api_data):
    topic = Topic(
        course=topic_api_data['course_a'],
        term=topic_api_data['other_year_term'],
        title='Wrong year',
        objective='Synthetic objective',
        order=3,
    )

    with pytest.raises(ValidationError) as error:
        topic.full_clean()

    assert 'term' in error.value.message_dict


@pytest.mark.django_db
def test_topic_course_term_order_is_unique(topic_api_data):
    with pytest.raises(IntegrityError), transaction.atomic():
        Topic.objects.create(
            course=topic_api_data['course_a'],
            term=topic_api_data['term'],
            title='Duplicate order',
            objective='Synthetic objective',
            order=1,
        )


@pytest.mark.django_db
def test_teacher_lists_own_course_topics_including_drafts(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.get(
        f"/api/v1/courses/{topic_api_data['course_a'].pk}/topics/"
    )

    assert response.status_code == status.HTTP_200_OK
    assert {item['id'] for item in response.data} == {
        topic_api_data['draft'].pk,
        topic_api_data['published'].pk,
    }
    assert set(response.data[0]) == {
        'id',
        'course',
        'term',
        'title',
        'objective',
        'order',
        'state',
        'published_at',
        'archived_at',
    }


@pytest.mark.django_db
def test_pupil_lists_only_published_topics_and_cannot_retrieve_draft(
    topic_api_data,
):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['pupil_a'])

    list_response = client.get(
        f"/api/v1/courses/{topic_api_data['course_a'].pk}/topics/"
    )
    draft_response = client.get(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/"
    )
    published_response = client.get(
        f"/api/v1/topics/{topic_api_data['published'].pk}/"
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert [item['id'] for item in list_response.data] == [
        topic_api_data['published'].pk
    ]
    assert draft_response.status_code == status.HTTP_404_NOT_FOUND
    assert published_response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_topics_reject_unassigned_course_and_foreign_topic_ids(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    foreign_course_list = client.get(
        f"/api/v1/courses/{topic_api_data['course_b'].pk}/topics/"
    )
    foreign_topic_detail = client.get(
        f"/api/v1/topics/{topic_api_data['other_course_topic'].pk}/"
    )

    assert foreign_course_list.status_code == status.HTTP_404_NOT_FOUND
    assert foreign_topic_detail.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_teacher_can_create_draft_topic_for_assigned_course(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.post(
        '/api/v1/topics/',
        {
            'course': topic_api_data['course_a'].pk,
            'term': topic_api_data['term'].pk,
            'title': 'New draft',
            'objective': 'A synthetic learning objective.',
            'order': 3,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data['course'] == topic_api_data['course_a'].pk
    assert response.data['term'] == topic_api_data['term'].pk
    assert response.data['title'] == 'New draft'
    assert response.data['state'] == Topic.State.DRAFT
    assert response.data['published_at'] is None
    created = Topic.objects.get(pk=response.data['id'])
    assert created.state == Topic.State.DRAFT
    assert created.published_at is None


@pytest.mark.django_db
def test_closed_term_blocks_topic_writes_without_removing_published_history(
    topic_api_data,
):
    term = topic_api_data['term']
    term.status = 'closed'
    term.save(update_fields=['status'])

    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])
    create_response = client.post(
        '/api/v1/topics/',
        {
            'course': topic_api_data['course_a'].pk,
            'term': term.pk,
            'title': 'Blocked new topic',
            'objective': 'Closed terms reject writes.',
            'order': 3,
        },
        format='json',
    )
    update_response = client.patch(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/",
        {'title': 'Blocked edit'},
        format='json',
    )
    publish_response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/publish/",
        {},
        format='json',
    )

    assert create_response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'term' in create_response.data
    assert update_response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'term' in update_response.data
    assert publish_response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'term' in publish_response.data
    assert Topic.objects.filter(pk=topic_api_data['published'].pk).exists()
    topic_api_data['draft'].refresh_from_db()
    assert topic_api_data['draft'].state == Topic.State.DRAFT
    assert topic_api_data['draft'].title == 'Draft topic'
    assert not Topic.objects.filter(title='Blocked new topic').exists()


@pytest.mark.django_db
def test_topic_create_uses_jwt_without_requiring_csrf(topic_api_data):
    client = APIClient(enforce_csrf_checks=True)
    payload = {
        'course': topic_api_data['course_a'].pk,
        'term': topic_api_data['term'].pk,
        'title': 'JWT draft without CSRF',
        'objective': 'Synthetic objective.',
        'order': 3,
    }
    anonymous_response = client.post('/api/v1/topics/', payload, format='json')
    assert anonymous_response.status_code == status.HTTP_401_UNAUTHORIZED

    login = client.post(
        '/api/v1/auth/login/',
        {
            'username': topic_api_data['teacher_a'].username,
            'password': 'safe-test-password',
        },
        format='json',
    )
    assert login.status_code == status.HTTP_200_OK
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    created_response = client.post('/api/v1/topics/', payload, format='json')

    assert created_response.status_code == status.HTTP_201_CREATED
    assert created_response.data['state'] == Topic.State.DRAFT


@pytest.mark.django_db
def test_pupil_cannot_create_topic(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['pupil_a'])

    response = client.post(
        '/api/v1/topics/',
        {
            'course': topic_api_data['course_a'].pk,
            'term': topic_api_data['term'].pk,
            'title': 'Pupil attempt',
            'objective': 'Synthetic objective.',
            'order': 3,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert Topic.objects.count() == 3


@pytest.mark.django_db
def test_teacher_cannot_create_topic_for_unassigned_course(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.post(
        '/api/v1/topics/',
        {
            'course': topic_api_data['course_b'].pk,
            'term': topic_api_data['term'].pk,
            'title': 'Foreign course',
            'objective': 'Synthetic objective.',
            'order': 2,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Topic.objects.count() == 3


@pytest.mark.django_db
def test_topic_create_rejects_publish_or_teacher_fields(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])
    payload = {
        'course': topic_api_data['course_a'].pk,
        'term': topic_api_data['term'].pk,
        'title': 'Attempted publish',
        'objective': 'Synthetic objective.',
        'order': 3,
        'state': Topic.State.PUBLISHED,
        'published_at': timezone.now().isoformat(),
        'teacher': topic_api_data['teacher_a'].pk,
    }

    response = client.post('/api/v1/topics/', payload, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert {'state', 'published_at', 'teacher'} <= set(response.data)
    assert Topic.objects.count() == 3


@pytest.mark.django_db
def test_topic_create_rejects_term_from_another_academic_year(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.post(
        '/api/v1/topics/',
        {
            'course': topic_api_data['course_a'].pk,
            'term': topic_api_data['other_year_term'].pk,
            'title': 'Wrong year',
            'objective': 'Synthetic objective.',
            'order': 3,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'term' in response.data
    assert Topic.objects.count() == 3


@pytest.mark.django_db
def test_teacher_can_patch_own_draft_without_changing_parent(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.patch(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/",
        {
            'title': 'Updated draft',
            'objective': 'Updated synthetic objective.',
            'order': 4,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    topic_api_data['draft'].refresh_from_db()
    assert topic_api_data['draft'].title == 'Updated draft'
    assert topic_api_data['draft'].objective == 'Updated synthetic objective.'
    assert topic_api_data['draft'].order == 4
    assert topic_api_data['draft'].course_id == topic_api_data['course_a'].pk


@pytest.mark.django_db
def test_topic_patch_rejects_parent_and_server_managed_fields(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])
    topic = topic_api_data['draft']

    response = client.patch(
        f'/api/v1/topics/{topic.pk}/',
        {
            'course': topic_api_data['course_b'].pk,
            'state': Topic.State.PUBLISHED,
            'published_at': timezone.now().isoformat(),
            'archived_at': timezone.now().isoformat(),
        },
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert {'course', 'state', 'published_at', 'archived_at'} <= set(response.data)
    topic.refresh_from_db()
    assert topic.course_id == topic_api_data['course_a'].pk
    assert topic.state == Topic.State.DRAFT
    assert topic.published_at is None


@pytest.mark.django_db
def test_foreign_teacher_cannot_patch_topic(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_b'])

    response = client.patch(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/",
        {'title': 'Unauthorized change'},
        format='json',
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    topic_api_data['draft'].refresh_from_db()
    assert topic_api_data['draft'].title == 'Draft topic'


@pytest.mark.django_db
def test_published_topic_cannot_be_patched(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.patch(
        f"/api/v1/topics/{topic_api_data['published'].pk}/",
        {'objective': 'Attempt to overwrite published objective.'},
        format='json',
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    topic_api_data['published'].refresh_from_db()
    assert topic_api_data['published'].objective == 'Published objective'


@pytest.mark.django_db
def test_archive_keeps_topic_row_and_removes_it_from_pupil_active_list(
    topic_api_data,
):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.post(
        f"/api/v1/topics/{topic_api_data['published'].pk}/archive/",
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data['archived_at'] is not None
    topic_api_data['published'].refresh_from_db()
    assert topic_api_data['published'].archived_at is not None
    assert Topic.objects.filter(pk=topic_api_data['published'].pk).exists()

    pupil_client = APIClient()
    pupil_client.force_authenticate(user=topic_api_data['pupil_a'])
    list_response = pupil_client.get(
        f"/api/v1/courses/{topic_api_data['course_a'].pk}/topics/"
    )
    detail_response = pupil_client.get(
        f"/api/v1/topics/{topic_api_data['published'].pk}/"
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert list_response.data == []
    assert detail_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_teacher_can_publish_topic_and_pupil_can_see_it(topic_api_data):
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=topic_api_data['teacher_a'])

    response = teacher_client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/publish/",
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data['state'] == Topic.State.PUBLISHED
    assert response.data['published_at'] is not None
    topic_api_data['draft'].refresh_from_db()
    assert topic_api_data['draft'].state == Topic.State.PUBLISHED
    assert topic_api_data['draft'].published_at is not None

    pupil_client = APIClient()
    pupil_client.force_authenticate(user=topic_api_data['pupil_a'])
    list_response = pupil_client.get(
        f"/api/v1/courses/{topic_api_data['course_a'].pk}/topics/"
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert {item['id'] for item in list_response.data} == {
        topic_api_data['draft'].pk,
        topic_api_data['published'].pk,
    }


@pytest.mark.django_db
def test_publish_is_idempotent_and_preserves_original_published_at(
    topic_api_data,
):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])
    publish_url = f"/api/v1/topics/{topic_api_data['draft'].pk}/publish/"

    first_response = client.post(publish_url, {}, format='json')
    first_published_at = first_response.data['published_at']
    second_response = client.post(publish_url, {}, format='json')

    assert first_response.status_code == status.HTTP_200_OK
    assert second_response.status_code == status.HTTP_200_OK
    assert second_response.data['published_at'] == first_published_at
    assert Topic.objects.filter(
        course=topic_api_data['course_a'],
        term=topic_api_data['term'],
        order=topic_api_data['draft'].order,
    ).count() == 1


@pytest.mark.django_db
def test_teacher_create_patch_publish_and_pupil_read_contract(topic_api_data):
    teacher_client = APIClient()
    teacher_client.force_authenticate(user=topic_api_data['teacher_a'])
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=topic_api_data['pupil_a'])

    created = teacher_client.post(
        '/api/v1/topics/',
        {
            'course': topic_api_data['course_a'].pk,
            'term': topic_api_data['term'].pk,
            'title': 'Workflow draft',
            'objective': 'A complete synthetic workflow.',
            'order': 3,
        },
        format='json',
    )
    assert created.status_code == status.HTTP_201_CREATED
    expected_fields = {
        'id',
        'course',
        'term',
        'title',
        'objective',
        'order',
        'state',
        'published_at',
        'archived_at',
    }
    assert set(created.data) == expected_fields
    assert created.data['state'] == Topic.State.DRAFT
    assert created.data['published_at'] is None

    topic_url = f"/api/v1/topics/{created.data['id']}/"
    publish_url = f'{topic_url}publish/'
    hidden_draft = pupil_client.get(topic_url)
    pupil_list_before_publish = pupil_client.get(
        f"/api/v1/courses/{topic_api_data['course_a'].pk}/topics/"
    )
    assert hidden_draft.status_code == status.HTTP_404_NOT_FOUND
    assert created.data['id'] not in {
        item['id'] for item in pupil_list_before_publish.data
    }

    patched = teacher_client.patch(
        topic_url,
        {
            'title': 'Workflow updated',
            'objective': 'Patched before publication.',
        },
        format='json',
    )
    assert patched.status_code == status.HTTP_200_OK
    assert set(patched.data) == expected_fields
    assert patched.data['title'] == 'Workflow updated'
    assert patched.data['state'] == Topic.State.DRAFT
    assert patched.data['published_at'] is None

    published = teacher_client.post(publish_url, {}, format='json')
    assert published.status_code == status.HTTP_200_OK
    assert set(published.data) == expected_fields
    assert published.data['state'] == Topic.State.PUBLISHED
    assert published.data['title'] == 'Workflow updated'
    assert published.data['published_at'] is not None
    original_timestamp = published.data['published_at']
    repeated_publish = teacher_client.post(publish_url, {}, format='json')
    assert repeated_publish.status_code == status.HTTP_200_OK
    assert repeated_publish.data['published_at'] == original_timestamp

    pupil_detail = pupil_client.get(topic_url)
    pupil_list = pupil_client.get(
        f"/api/v1/courses/{topic_api_data['course_a'].pk}/topics/"
    )
    assert pupil_detail.status_code == status.HTTP_200_OK
    assert {
        key: value
        for key, value in pupil_detail.data.items()
        if key != 'completion'
    } == published.data
    assert pupil_detail.data['completion'] is None
    assert created.data['id'] in {item['id'] for item in pupil_list.data}


@pytest.mark.django_db
def test_academic_operator_does_not_bypass_teacher_course_scope(topic_api_data):
    operator = User.objects.create_user(
        username='topic-academic-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    permission = Permission.objects.get(
        content_type__app_label='accounts',
        codename='manage_academics',
    )
    operator.user_permissions.add(permission)
    client = APIClient()
    client.force_authenticate(user=operator)

    create_response = client.post(
        '/api/v1/topics/',
        {
            'course': topic_api_data['course_a'].pk,
            'term': topic_api_data['term'].pk,
            'title': 'Operator without assignment',
            'objective': 'Capability is not course assignment.',
            'order': 3,
        },
        format='json',
    )
    publish_response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/publish/",
        {},
        format='json',
    )

    assert create_response.status_code == status.HTTP_404_NOT_FOUND
    assert publish_response.status_code == status.HTTP_404_NOT_FOUND
    assert not Topic.objects.filter(title='Operator without assignment').exists()
    topic_api_data['draft'].refresh_from_db()
    assert topic_api_data['draft'].state == Topic.State.DRAFT


@pytest.mark.django_db
def test_publishing_already_published_topic_is_idempotent(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])
    existing_timestamp = topic_api_data['published'].published_at

    response = client.post(
        f"/api/v1/topics/{topic_api_data['published'].pk}/publish/",
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    topic_api_data['published'].refresh_from_db()
    assert topic_api_data['published'].published_at == existing_timestamp


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('field', 'value'),
    [('title', '  '), ('objective', '\n \t')],
)
def test_publish_rejects_blank_title_or_objective(topic_api_data, field, value):
    topic = topic_api_data['draft']
    Topic.objects.filter(pk=topic.pk).update(**{field: value})
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.post(
        f'/api/v1/topics/{topic.pk}/publish/',
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field in response.data
    topic.refresh_from_db()
    assert topic.state == Topic.State.DRAFT
    assert topic.published_at is None


@pytest.mark.django_db
def test_publish_rejects_topic_with_term_from_another_year(topic_api_data):
    topic = topic_api_data['draft']
    Topic.objects.filter(pk=topic.pk).update(
        term=topic_api_data['other_year_term'],
    )
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.post(
        f'/api/v1/topics/{topic.pk}/publish/',
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'term' in response.data
    topic.refresh_from_db()
    assert topic.state == Topic.State.DRAFT
    assert topic.published_at is None


@pytest.mark.django_db
def test_publish_rejects_pupil_and_foreign_teacher(topic_api_data):
    url = f"/api/v1/topics/{topic_api_data['draft'].pk}/publish/"

    pupil_client = APIClient()
    pupil_client.force_authenticate(user=topic_api_data['pupil_a'])
    pupil_response = pupil_client.post(url, {}, format='json')

    foreign_teacher_client = APIClient()
    foreign_teacher_client.force_authenticate(user=topic_api_data['teacher_b'])
    teacher_response = foreign_teacher_client.post(url, {}, format='json')

    assert pupil_response.status_code == status.HTTP_403_FORBIDDEN
    assert teacher_response.status_code == status.HTTP_404_NOT_FOUND
    topic_api_data['draft'].refresh_from_db()
    assert topic_api_data['draft'].state == Topic.State.DRAFT


@pytest.mark.django_db
def test_archived_topic_cannot_be_published(topic_api_data):
    topic = topic_api_data['draft']
    Topic.objects.filter(pk=topic.pk).update(archived_at=timezone.now())
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    response = client.post(
        f'/api/v1/topics/{topic.pk}/publish/',
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    topic.refresh_from_db()
    assert topic.state == Topic.State.DRAFT
    assert topic.published_at is None


@pytest.mark.django_db
def test_destroy_and_put_remain_unavailable(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])
    topic_url = f"/api/v1/topics/{topic_api_data['draft'].pk}/"

    delete_response = client.delete(topic_url)
    put_response = client.put(
        topic_url,
        {'title': 'Replacement'},
        format='json',
    )

    assert delete_response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert put_response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert Topic.objects.filter(pk=topic_api_data['draft'].pk).exists()


@pytest.mark.django_db
def test_teacher_cannot_write_after_assignment_interval_ends(topic_api_data):
    assignment = TeachingAssignment.objects.get(
        course=topic_api_data['course_a'],
        teacher=topic_api_data['teacher_a'],
    )
    close_teaching_assignment(
        assignment,
        timezone.localdate() - timedelta(days=1),
    )
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    create_response = client.post(
        "/api/v1/topics/",
        {
            "course": topic_api_data['course_a'].pk,
            "term": topic_api_data['term'].pk,
            "title": "New topic",
            "objective": "New objective",
            "order": 2,
        },
        format="json",
    )
    update_response = client.patch(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/",
        {"title": "Changed draft"},
        format="json",
    )
    publish_response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/publish/",
        {},
        format='json',
    )

    assert create_response.status_code == status.HTTP_404_NOT_FOUND
    assert update_response.status_code == status.HTTP_404_NOT_FOUND
    assert publish_response.status_code == status.HTTP_404_NOT_FOUND
    topic_api_data['draft'].refresh_from_db()
    assert topic_api_data['draft'].state == Topic.State.DRAFT
    assert topic_api_data['draft'].published_at is None


def create_resource_attachment(owner, *, scan_status, detected_mime='application/pdf'):
    return Attachment.objects.create(
        owner=owner,
        original_name='lesson.pdf',
        size=128,
        detected_mime=detected_mime,
        scan_status=scan_status,
    )


@pytest.mark.django_db
def test_teacher_can_create_draft_pdf_resource_for_own_topic(topic_api_data):
    attachment = create_resource_attachment(
        topic_api_data['teacher_a'],
        scan_status=Attachment.ScanStatus.CLEAN,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/resources/",
        {
            'kind': Resource.Kind.PDF,
            'title': 'PDF material',
            'description': 'Synthetic resource description.',
            'attachment': attachment.pk,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_201_CREATED
    resource = Resource.objects.get()
    assert resource.topic == topic_api_data['draft']
    assert resource.attachment == attachment
    assert resource.state == Resource.State.DRAFT
    assert resource.version == 1
    assert set(response.data) == {
        'id',
        'topic',
        'kind',
        'title',
        'description',
        'attachment',
        'url',
        'transcript_text',
        'state',
        'version',
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('owner_key', 'scan_status', 'detected_mime'),
    [
        ('teacher_a', Attachment.ScanStatus.PENDING_SCAN, 'application/pdf'),
        ('teacher_b', Attachment.ScanStatus.CLEAN, 'application/pdf'),
        ('teacher_a', Attachment.ScanStatus.CLEAN, 'image/png'),
    ],
)
def test_resource_rejects_pending_foreign_or_non_pdf_attachment(
    topic_api_data,
    owner_key,
    scan_status,
    detected_mime,
):
    attachment = create_resource_attachment(
        topic_api_data[owner_key],
        scan_status=scan_status,
        detected_mime=detected_mime,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/resources/",
        {'title': 'Rejected material', 'attachment': attachment.pk},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Resource.objects.exists()


@pytest.mark.django_db
def test_resource_create_rejects_state_version_and_parent_injection(
    topic_api_data,
):
    attachment = create_resource_attachment(
        topic_api_data['teacher_a'],
        scan_status=Attachment.ScanStatus.CLEAN,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/resources/",
        {
            'title': 'Injected state',
            'attachment': attachment.pk,
            'state': Resource.State.PUBLISHED,
            'version': 99,
            'topic': topic_api_data['published'].pk,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert {'state', 'version', 'topic'} <= set(response.data)
    assert not Resource.objects.exists()


@pytest.mark.django_db
def test_resource_create_rejects_pupil_and_foreign_topic(topic_api_data):
    attachment = create_resource_attachment(
        topic_api_data['teacher_a'],
        scan_status=Attachment.ScanStatus.CLEAN,
    )
    pupil_client = APIClient()
    pupil_client.force_authenticate(topic_api_data['pupil_a'])
    pupil_response = pupil_client.post(
        f"/api/v1/topics/{topic_api_data['published'].pk}/resources/",
        {'title': 'Pupil resource', 'attachment': attachment.pk},
        format='json',
    )

    teacher_client = APIClient()
    teacher_client.force_authenticate(topic_api_data['teacher_b'])
    foreign_response = teacher_client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/resources/",
        {'title': 'Foreign resource', 'attachment': attachment.pk},
        format='json',
    )

    assert pupil_response.status_code == status.HTTP_403_FORBIDDEN
    assert foreign_response.status_code == status.HTTP_404_NOT_FOUND
    assert not Resource.objects.exists()


@pytest.mark.django_db
def test_resource_publish_requires_published_topic_and_clean_attachment(
    topic_api_data,
):
    attachment = create_resource_attachment(
        topic_api_data['teacher_a'],
        scan_status=Attachment.ScanStatus.CLEAN,
    )
    draft_topic_resource = Resource.objects.create(
        topic=topic_api_data['draft'],
        title='Draft topic resource',
        attachment=attachment,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    draft_topic_response = client.post(
        f'/api/v1/resources/{draft_topic_resource.pk}/publish/',
        {},
        format='json',
    )

    assert draft_topic_response.status_code == status.HTTP_400_BAD_REQUEST
    draft_topic_resource.refresh_from_db()
    assert draft_topic_resource.state == Resource.State.DRAFT

    published_topic_resource = Resource.objects.create(
        topic=topic_api_data['published'],
        title='Published topic resource',
        attachment=attachment,
    )
    attachment.scan_status = Attachment.ScanStatus.PENDING_SCAN
    attachment.save(update_fields=['scan_status'])
    pending_response = client.post(
        f'/api/v1/resources/{published_topic_resource.pk}/publish/',
        {'state': Resource.State.PUBLISHED},
        format='json',
    )

    assert pending_response.status_code == status.HTTP_400_BAD_REQUEST
    published_topic_resource.refresh_from_db()
    assert published_topic_resource.state == Resource.State.DRAFT

    attachment.scan_status = Attachment.ScanStatus.CLEAN
    attachment.save(update_fields=['scan_status'])
    publish_response = client.post(
        f'/api/v1/resources/{published_topic_resource.pk}/publish/',
        {'state': Resource.State.DRAFT},
        format='json',
    )

    assert publish_response.status_code == status.HTTP_200_OK
    assert publish_response.data['state'] == Resource.State.PUBLISHED
    published_topic_resource.refresh_from_db()
    assert published_topic_resource.state == Resource.State.PUBLISHED


@pytest.mark.django_db
def test_pupil_sees_only_published_resources_of_published_topics(topic_api_data):
    clean_attachment = create_resource_attachment(
        topic_api_data['teacher_a'],
        scan_status=Attachment.ScanStatus.CLEAN,
    )
    draft_resource = Resource.objects.create(
        topic=topic_api_data['published'],
        title='Hidden draft resource',
        attachment=clean_attachment,
    )
    published_resource = Resource.objects.create(
        topic=topic_api_data['published'],
        title='Visible published resource',
        attachment=clean_attachment,
        state=Resource.State.PUBLISHED,
    )
    hidden_parent_resource = Resource.objects.create(
        topic=topic_api_data['draft'],
        title='Resource under draft topic',
        attachment=clean_attachment,
        state=Resource.State.PUBLISHED,
    )
    pending_attachment = create_resource_attachment(
        topic_api_data['teacher_a'],
        scan_status=Attachment.ScanStatus.PENDING_SCAN,
    )
    pending_resource = Resource.objects.create(
        topic=topic_api_data['published'],
        title='Resource with pending attachment',
        attachment=pending_attachment,
        state=Resource.State.PUBLISHED,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['pupil_a'])

    listing = client.get(
        f"/api/v1/topics/{topic_api_data['published'].pk}/resources/"
    )
    draft_detail = client.get(f'/api/v1/resources/{draft_resource.pk}/')
    published_detail = client.get(
        f'/api/v1/resources/{published_resource.pk}/'
    )
    hidden_parent_detail = client.get(
        f'/api/v1/resources/{hidden_parent_resource.pk}/'
    )
    pending_detail = client.get(f'/api/v1/resources/{pending_resource.pk}/')
    draft_parent_list = client.get(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/resources/"
    )

    assert listing.status_code == status.HTTP_200_OK
    assert [item['id'] for item in listing.data] == [published_resource.pk]
    assert draft_detail.status_code == status.HTTP_404_NOT_FOUND
    assert published_detail.status_code == status.HTTP_200_OK
    assert hidden_parent_detail.status_code == status.HTTP_404_NOT_FOUND
    assert pending_detail.status_code == status.HTTP_404_NOT_FOUND
    assert draft_parent_list.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_pupil_cannot_read_resource_in_another_enrolled_course(topic_api_data):
    topic = topic_api_data['other_course_topic']
    topic.state = Topic.State.PUBLISHED
    topic.published_at = timezone.now()
    topic.save(update_fields=['state', 'published_at'])
    attachment = create_resource_attachment(
        topic_api_data['teacher_b'],
        scan_status=Attachment.ScanStatus.CLEAN,
    )
    resource = Resource.objects.create(
        topic=topic,
        title='Other pupil resource',
        attachment=attachment,
        state=Resource.State.PUBLISHED,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['pupil_a'])

    detail = client.get(f'/api/v1/resources/{resource.pk}/')
    listing = client.get(f'/api/v1/topics/{topic.pk}/resources/')

    assert detail.status_code == status.HTTP_404_NOT_FOUND
    assert listing.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_teacher_cannot_read_foreign_resource_detail(topic_api_data):
    attachment = create_resource_attachment(
        topic_api_data['teacher_a'],
        scan_status=Attachment.ScanStatus.CLEAN,
    )
    resource = Resource.objects.create(
        topic=topic_api_data['draft'],
        title='Private teacher resource',
        attachment=attachment,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_b'])

    response = client.get(f'/api/v1/resources/{resource.pk}/')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
@override_settings(RESOURCE_URL_ALLOWED_HOSTS=('youtube.com', 'youtu.be'))
@pytest.mark.parametrize(
    ('kind', 'url'),
    [
        (Resource.Kind.VIDEO, 'https://youtu.be/example123'),
        (Resource.Kind.LINK, 'https://youtube.com/watch?v=example123'),
    ],
)
def test_teacher_can_create_url_resource_with_allowed_https_url(
    topic_api_data,
    kind,
    url,
):
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/resources/",
        {
            'kind': kind,
            'title': 'Kasrlar video havolasi',
            'url': url,
            'transcript_text': 'Videodagi matn.',
        },
        format='json',
    )

    assert response.status_code == status.HTTP_201_CREATED
    resource = Resource.objects.get(kind=kind)
    assert resource.attachment is None
    assert resource.url == url
    assert resource.state == Resource.State.DRAFT
    assert response.data['transcript_text'] == 'Videodagi matn.'


@pytest.mark.django_db
@override_settings(RESOURCE_URL_ALLOWED_HOSTS=('youtube.com',))
@pytest.mark.parametrize(
    'url',
    [
        'http://youtube.com/watch?v=example',
        'https://not-youtube.com/watch?v=example',
        'https://youtube.com.evil.example/watch?v=example',
        'https://user@youtube.com/watch?v=example',
        'https://youtube.com:8443/watch?v=example',
    ],
)
def test_video_resource_rejects_non_https_or_unapproved_host(topic_api_data, url):
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/resources/",
        {'kind': Resource.Kind.VIDEO, 'title': 'Invalid video', 'url': url},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Resource.objects.exists()


@pytest.mark.django_db
@override_settings(RESOURCE_URL_ALLOWED_HOSTS=('youtube.com',))
@pytest.mark.parametrize(
    'data',
    [
        {
            'kind': Resource.Kind.VIDEO,
            'title': 'Mixed inputs',
            'attachment': 123,
            'url': 'https://youtube.com/watch?v=example',
        },
        {
            'kind': Resource.Kind.VIDEO,
            'title': 'Missing URL',
            'attachment': None,
        },
        {
            'kind': Resource.Kind.PDF,
            'title': 'Missing attachment',
            'attachment': None,
        },
        {
            'kind': Resource.Kind.LINK,
            'title': 'Mixed PDF inputs',
            'attachment': 123,
            'url': 'https://youtube.com/watch?v=example',
        },
        {
            'kind': Resource.Kind.PDF,
            'title': 'Mixed URL input',
            'attachment': 123,
            'url': 'https://youtube.com/watch?v=example',
        },
    ],
)
def test_resource_rejects_invalid_attachment_and_url_combinations(
    topic_api_data,
    data,
):
    data = data.copy()
    if data.get('attachment') == 123:
        attachment = create_resource_attachment(
            topic_api_data['teacher_a'],
            scan_status=Attachment.ScanStatus.CLEAN,
        )
        data['attachment'] = attachment.pk

    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    response = client.post(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/resources/",
        data,
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Resource.objects.exists()


@pytest.mark.django_db
@override_settings(RESOURCE_URL_ALLOWED_HOSTS=('youtube.com',))
def test_pupil_reads_published_video_and_transcript_in_scope(topic_api_data):
    topic = topic_api_data['published']
    resource = Resource.objects.create(
        topic=topic,
        kind=Resource.Kind.VIDEO,
        title='Kasrlar videosi',
        url='https://youtube.com/watch?v=example123',
        transcript_text='Videodagi matn.',
        state=Resource.State.PUBLISHED,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['pupil_a'])

    response = client.get(f'/api/v1/resources/{resource.pk}/')
    listing = client.get(f'/api/v1/topics/{topic.pk}/resources/')

    assert response.status_code == status.HTTP_200_OK
    assert response.data['url'] == 'https://youtube.com/watch?v=example123'
    assert response.data['transcript_text'] == 'Videodagi matn.'
    assert listing.status_code == status.HTTP_200_OK
    assert [item['id'] for item in listing.data] == [resource.pk]


@pytest.mark.django_db
@override_settings(RESOURCE_URL_ALLOWED_HOSTS=('youtube.com',))
def test_video_resource_publish_requires_published_topic(topic_api_data):
    resource = Resource.objects.create(
        topic=topic_api_data['draft'],
        kind=Resource.Kind.VIDEO,
        title='Draft topic video',
        url='https://youtube.com/watch?v=example123',
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    response = client.post(
        f'/api/v1/resources/{resource.pk}/publish/',
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    resource.refresh_from_db()
    assert resource.state == Resource.State.DRAFT


@pytest.mark.django_db
def test_pupil_completion_put_is_idempotent_and_appears_in_topic_detail(
    topic_api_data,
):
    client = APIClient()
    client.force_authenticate(topic_api_data['pupil_a'])
    completion_url = (
        f"/api/v1/topics/{topic_api_data['published'].pk}/completion/"
    )

    before = client.get(f"/api/v1/topics/{topic_api_data['published'].pk}/")
    first = client.put(completion_url, {}, format='json')
    first_timestamp = first.data['completed_at']
    repeated = client.put(completion_url, {}, format='json')
    after = client.get(f"/api/v1/topics/{topic_api_data['published'].pk}/")

    assert before.status_code == status.HTTP_200_OK
    assert before.data['completion'] is None
    assert first.status_code == status.HTTP_200_OK
    assert first.data['topic'] == topic_api_data['published'].pk
    assert first.data['kind'] == Completion.Kind.READ
    assert repeated.status_code == status.HTTP_200_OK
    assert repeated.data['completed_at'] == first_timestamp
    assert Completion.objects.filter(
        pupil=topic_api_data['pupil_a'],
        topic=topic_api_data['published'],
        kind=Completion.Kind.READ,
    ).count() == 1
    assert after.data['completion']['kind'] == Completion.Kind.READ
    assert after.data['completion']['completed_at'] == first_timestamp


@pytest.mark.django_db
def test_completion_cannot_be_set_for_foreign_or_draft_topic(topic_api_data):
    client = APIClient()
    client.force_authenticate(topic_api_data['pupil_a'])

    foreign_response = client.put(
        f"/api/v1/topics/{topic_api_data['other_course_topic'].pk}/completion/",
        {},
        format='json',
    )
    draft_response = client.put(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/completion/",
        {},
        format='json',
    )

    assert foreign_response.status_code == status.HTTP_404_NOT_FOUND
    assert draft_response.status_code == status.HTTP_404_NOT_FOUND
    assert not Completion.objects.exists()


@pytest.mark.django_db
def test_teacher_cannot_set_pupil_completion(topic_api_data):
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    response = client.put(
        f"/api/v1/topics/{topic_api_data['published'].pk}/completion/",
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not Completion.objects.exists()


@pytest.mark.django_db
def test_completion_does_not_accept_another_user_or_kind(topic_api_data):
    client = APIClient()
    client.force_authenticate(topic_api_data['pupil_a'])

    response = client.put(
        f"/api/v1/topics/{topic_api_data['published'].pk}/completion/",
        {
            'pupil': topic_api_data['pupil_b'].pk,
            'kind': Completion.Kind.READ,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert {'pupil', 'kind'} <= set(response.data)
    assert not Completion.objects.exists()


@pytest.mark.django_db
def test_closed_enrollment_cannot_create_completion(topic_api_data):
    enrollment = Enrollment.objects.get(
        pupil=topic_api_data['pupil_a'],
        course=topic_api_data['course_a'],
    )
    enrollment.status = MembershipStatus.CLOSED
    enrollment.end_date = timezone.localdate() - timedelta(days=1)
    enrollment.save(update_fields=['status', 'end_date'])
    client = APIClient()
    client.force_authenticate(topic_api_data['pupil_a'])

    response = client.put(
        f"/api/v1/topics/{topic_api_data['published'].pk}/completion/",
        {},
        format='json',
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert not Completion.objects.exists()


def create_stored_attachment(owner, *, scan_status, original_name='lesson.pdf'):
    key = uuid.uuid4()
    attachment = Attachment.objects.create(
        owner=owner,
        key=key,
        original_name=original_name,
        size=14,
        detected_mime='application/pdf',
        scan_status=scan_status,
    )
    storage = storages['private']
    storage.save(str(key), ContentFile(b'%PDF-1.7\nprivate'))
    return attachment


@pytest.mark.django_db
def test_pupil_downloads_clean_file_only_through_published_resource_scope(
    topic_api_data,
):
    attachment = create_stored_attachment(
        topic_api_data['teacher_a'],
        scan_status=Attachment.ScanStatus.CLEAN,
        original_name='lesson.pdf',
    )
    resource = Resource.objects.create(
        topic=topic_api_data['published'],
        title='Published PDF',
        attachment=attachment,
        state=Resource.State.PUBLISHED,
    )

    clients = {}
    for role, user_key in (
        ('pupil_a', 'pupil_a'),
        ('pupil_b', 'pupil_b'),
        ('foreign_teacher', 'teacher_b'),
        ('resource_teacher', 'teacher_a'),
    ):
        client = APIClient()
        client.force_authenticate(topic_api_data[user_key])
        clients[role] = client

    try:
        pupil_response = clients['pupil_a'].get(
            f'/api/v1/attachments/{attachment.pk}/download/'
        )
        foreign_pupil_response = clients['pupil_b'].get(
            f'/api/v1/attachments/{attachment.pk}/download/'
        )
        foreign_teacher_response = clients['foreign_teacher'].get(
            f'/api/v1/attachments/{attachment.pk}/download/'
        )
        teacher_response = clients['resource_teacher'].get(
            f'/api/v1/attachments/{attachment.pk}/download/'
        )

        assert pupil_response.status_code == status.HTTP_200_OK
        assert b''.join(pupil_response.streaming_content) == b'%PDF-1.7\nprivate'
        assert pupil_response['Content-Disposition'].startswith('attachment;')
        assert pupil_response['X-Content-Type-Options'] == 'nosniff'
        assert pupil_response['Cache-Control'] == 'private, no-store'
        assert foreign_pupil_response.status_code == status.HTTP_404_NOT_FOUND
        assert foreign_teacher_response.status_code == status.HTTP_404_NOT_FOUND
        assert teacher_response.status_code == status.HTTP_200_OK
        teacher_response.close()
    finally:
        storages['private'].delete(str(attachment.key))


@pytest.mark.django_db
@pytest.mark.parametrize(
    'scan_status',
    [Attachment.ScanStatus.PENDING_SCAN, Attachment.ScanStatus.REJECTED],
)
def test_attachment_owner_cannot_download_unclean_file(topic_api_data, scan_status):
    attachment = create_stored_attachment(
        topic_api_data['teacher_a'],
        scan_status=scan_status,
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['teacher_a'])

    try:
        response = client.get(
            f'/api/v1/attachments/{attachment.pk}/download/'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        storages['private'].delete(str(attachment.key))


@pytest.mark.django_db
def test_attachment_download_owner_can_access_own_clean_unlinked_file(
    topic_api_data,
):
    attachment = create_stored_attachment(
        topic_api_data['pupil_a'],
        scan_status=Attachment.ScanStatus.CLEAN,
        original_name='owned.pdf',
    )
    client = APIClient()
    client.force_authenticate(topic_api_data['pupil_a'])

    try:
        response = client.get(
            f'/api/v1/attachments/{attachment.pk}/download/'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response['Content-Disposition'].startswith('attachment;')
        assert b''.join(response.streaming_content) == b'%PDF-1.7\nprivate'
    finally:
        storages['private'].delete(str(attachment.key))


@pytest.mark.django_db
def test_attachment_download_requires_authentication():
    response = APIClient().get('/api/v1/attachments/1/download/')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
