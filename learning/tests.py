from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from academics.models import (
    AcademicYear,
    CourseOffering,
    Enrollment,
    SchoolClass,
    Subject,
    TeachingAssignment,
    Term,
)
from learning.models import Topic


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
def test_topic_create_requires_session_csrf_token(topic_api_data):
    client = APIClient(enforce_csrf_checks=True)
    csrf_token = client.get('/api/v1/auth/csrf/').data['csrfToken']
    assert client.login(
        username=topic_api_data['teacher_a'].username,
        password='safe-test-password',
    )
    payload = {
        'course': topic_api_data['course_a'].pk,
        'term': topic_api_data['term'].pk,
        'title': 'CSRF-protected draft',
        'objective': 'Synthetic objective.',
        'order': 3,
    }

    rejected_response = client.post('/api/v1/topics/', payload, format='json')
    accepted_response = client.post(
        '/api/v1/topics/',
        payload,
        format='json',
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert rejected_response.status_code == status.HTTP_403_FORBIDDEN
    assert accepted_response.status_code == status.HTTP_201_CREATED
    assert accepted_response.data['state'] == Topic.State.DRAFT


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
