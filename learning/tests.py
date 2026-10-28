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
def test_topic_endpoints_are_read_only(topic_api_data):
    client = APIClient()
    client.force_authenticate(user=topic_api_data['teacher_a'])

    create_response = client.post(
        f"/api/v1/courses/{topic_api_data['course_a'].pk}/topics/",
        {
            'course': topic_api_data['course_a'].pk,
            'term': topic_api_data['term'].pk,
            'title': 'Not writable',
        },
        format='json',
    )
    update_response = client.patch(
        f"/api/v1/topics/{topic_api_data['draft'].pk}/",
        {'title': 'Not writable'},
        format='json',
    )

    assert create_response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert update_response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
