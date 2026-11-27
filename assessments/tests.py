from datetime import date, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.utils import timezone
from django.db import connection, connections
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
from assessments.models import (
    Attempt,
    AttemptGrade,
    AttemptGradeAudit,
    AttemptResult,
    Exam,
    ExamItem,
    ExamPublishAudit,
    ExamSnapshotItem,
    ExamSnapshotOption,
    Option,
    Question,
)
from assessments.tasks import finalize_expired_attempts


@pytest.fixture
def question_bank_data(db):
    year = AcademicYear.objects.create(
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        label='Question bank year',
    )
    subject = Subject.objects.create(code='Q-BANK', title='Question bank')
    course = CourseOffering.objects.create(
        school_class=SchoolClass.objects.create(
            year=year,
            grade_level=7,
            section='Q',
        ),
        subject=subject,
    )
    other_course = CourseOffering.objects.create(
        school_class=SchoolClass.objects.create(
            year=year,
            grade_level=7,
            section='R',
        ),
        subject=subject,
    )
    teacher = User.objects.create_user(
        username='question-teacher', password='safe-test-password',
        role=User.Role.TEACHER,
    )
    other_teacher = User.objects.create_user(
        username='question-other-teacher', password='safe-test-password',
        role=User.Role.TEACHER,
    )
    pupil = User.objects.create_user(
        username='question-pupil', password='safe-test-password',
        role=User.Role.PUPIL,
    )
    TeachingAssignment.objects.create(
        course=course, teacher=teacher, start_date=date(2026, 9, 1),
    )
    TeachingAssignment.objects.create(
        course=other_course, teacher=other_teacher, start_date=date(2026, 9, 1),
    )
    Enrollment.objects.create(
        course=course, pupil=pupil, start_date=date(2026, 9, 1),
    )
    return {
        'year': year,
        'course': course,
        'other_course': other_course,
        'teacher': teacher,
        'other_teacher': other_teacher,
        'pupil': pupil,
    }


def question_payload(course, options):
    return {
        'course': course.pk,
        'stem': '2 + 2 nechiga teng?',
        'options': options,
    }


@pytest.mark.django_db
@pytest.mark.parametrize('options', [
    [{'text': '3', 'is_correct': False}, {'text': '4', 'is_correct': False}],
    [{'text': '3', 'is_correct': True}, {'text': '4', 'is_correct': True}],
])
def test_question_create_requires_exactly_one_correct_option(
    question_bank_data, options,
):
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    response = client.post(
        '/api/v1/questions/',
        question_payload(question_bank_data['course'], options),
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'options' in response.data
    assert Question.objects.count() == 0


@pytest.mark.django_db
def test_assigned_teacher_can_create_list_and_retrieve_own_draft_questions(
    question_bank_data,
):
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])
    response = client.post(
        '/api/v1/questions/',
        question_payload(question_bank_data['course'], [
            {'text': '3', 'is_correct': False},
            {'text': '4', 'is_correct': True},
        ]),
        format='json',
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data['state'] == Question.State.DRAFT
    assert response.data['options'][1]['is_correct'] is True
    question = Question.objects.get()
    assert Option.objects.filter(question=question).count() == 2

    list_response = client.get(
        f"/api/v1/courses/{question_bank_data['course'].pk}/questions/"
    )
    detail_response = client.get(f'/api/v1/questions/{question.pk}/')
    assert [item['id'] for item in list_response.data] == [question.pk]
    assert detail_response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_pupil_and_other_teacher_cannot_access_question_bank(question_bank_data):
    question = Question.objects.create(
        course=question_bank_data['course'], stem='Private draft',
    )
    Option.objects.create(question=question, text='Wrong', is_correct=False)
    Option.objects.create(question=question, text='Correct', is_correct=True)

    pupil_client = APIClient()
    pupil_client.force_authenticate(question_bank_data['pupil'])
    assert pupil_client.get(
        f"/api/v1/courses/{question_bank_data['course'].pk}/questions/"
    ).status_code == status.HTTP_403_FORBIDDEN
    assert pupil_client.get(
        f'/api/v1/questions/{question.pk}/'
    ).status_code == status.HTTP_403_FORBIDDEN
    assert pupil_client.post(
        '/api/v1/questions/',
        question_payload(question_bank_data['course'], [
            {'text': 'Wrong', 'is_correct': False},
            {'text': 'Correct', 'is_correct': True},
        ]),
        format='json',
    ).status_code == status.HTTP_403_FORBIDDEN

    teacher_client = APIClient()
    teacher_client.force_authenticate(question_bank_data['other_teacher'])
    assert teacher_client.get(
        f"/api/v1/courses/{question_bank_data['course'].pk}/questions/"
    ).status_code == status.HTTP_404_NOT_FOUND
    assert teacher_client.get(
        f'/api/v1/questions/{question.pk}/'
    ).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_essay_rejects_option_key_and_keeps_marking_note_teacher_private(
    question_bank_data,
):
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    rejected_response = client.post(
        '/api/v1/questions/',
        {
            'course': question_bank_data['course'].pk,
            'stem': 'Kasrlarni izohlang.',
            'type': Question.Type.ESSAY,
            'options': [],
        },
        format='json',
    )
    assert rejected_response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'options' in rejected_response.data

    response = client.post(
        '/api/v1/questions/',
        {
            'course': question_bank_data['course'].pk,
            'stem': 'Kasrlarni izohlang.',
            'type': Question.Type.ESSAY,
            'marking_note': 'Tushuncha va bitta to‘g‘ri misolni tekshiring.',
        },
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert response.data['options'] == []
    assert response.data['marking_note']


@pytest.mark.django_db
def test_published_question_edit_creates_new_version_without_mutating_source(
    question_bank_data,
):
    original = Question.objects.create(
        course=question_bank_data['course'],
        stem='Original prompt',
        state=Question.State.PUBLISHED,
    )
    Option.objects.create(question=original, text='Wrong', is_correct=False)
    Option.objects.create(question=original, text='Correct', is_correct=True)
    original_options = list(
        original.options.values_list('text', 'is_correct'),
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    response = client.patch(
        f'/api/v1/questions/{original.pk}/',
        {
            'stem': 'Replacement prompt',
            'options': [
                {'text': 'New wrong', 'is_correct': False},
                {'text': 'New correct', 'is_correct': True},
            ],
        },
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    replacement = Question.objects.get(pk=response.data['id'])
    original.refresh_from_db()
    assert replacement.pk != original.pk
    assert replacement.family_id == original.family_id
    assert replacement.version == original.version + 1
    assert replacement.state == Question.State.DRAFT
    assert original.stem == 'Original prompt'
    assert original.state == Question.State.PUBLISHED
    assert list(original.options.values_list('text', 'is_correct')) == original_options
    assert list(replacement.options.values_list('text', 'is_correct')) == [
        ('New wrong', False),
        ('New correct', True),
    ]


@pytest.mark.django_db
def test_teacher_archives_question_without_deleting_it(question_bank_data):
    question = Question.objects.create(
        course=question_bank_data['course'], stem='Archive me',
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    response = client.post(f'/api/v1/questions/{question.pk}/archive/')

    assert response.status_code == status.HTTP_200_OK
    question.refresh_from_db()
    assert question.state == Question.State.ARCHIVED
    assert question.archived_at is not None
    assert client.get(f'/api/v1/questions/{question.pk}/').status_code == (
        status.HTTP_404_NOT_FOUND
    )


def exam_payload(course, term, **overrides):
    opens_at = timezone.now() + timedelta(days=1)
    payload = {
        'course': course.pk,
        'term': term.pk,
        'title': 'Draft nazorat',
        'opens_at': opens_at.isoformat(),
        'closes_at': (opens_at + timedelta(hours=2)).isoformat(),
        'duration': '01:00:00',
        'attempt_limit': 1,
        'result_release_at': (opens_at + timedelta(hours=2)).isoformat(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_assigned_teacher_creates_and_updates_draft_exam(question_bank_data):
    term = Term.objects.create(
        year=question_bank_data['year'], order=1,
        start_date=date(2026, 9, 1), end_date=date(2026, 11, 30),
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    create_response = client.post(
        '/api/v1/exams/', exam_payload(question_bank_data['course'], term),
        format='json',
    )
    assert create_response.status_code == status.HTTP_201_CREATED
    assert create_response.data['state'] == Exam.State.DRAFT
    update_response = client.patch(
        f"/api/v1/exams/{create_response.data['id']}/",
        {'title': 'Updated draft nazorat', 'attempt_limit': 2},
        format='json',
    )
    assert update_response.status_code == status.HTTP_200_OK
    assert update_response.data['title'] == 'Updated draft nazorat'
    assert update_response.data['attempt_limit'] == 2


@pytest.mark.django_db
def test_exam_rejects_invalid_times_duration_and_course_term(question_bank_data):
    term = Term.objects.create(
        year=question_bank_data['year'], order=1,
        start_date=date(2026, 9, 1), end_date=date(2026, 11, 30),
    )
    other_year = AcademicYear.objects.create(
        start_date=date(2027, 9, 1), end_date=date(2028, 6, 30),
        label='Other exam year',
    )
    other_term = Term.objects.create(
        year=other_year, order=1,
        start_date=date(2027, 9, 1), end_date=date(2027, 11, 30),
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])
    payload = exam_payload(question_bank_data['course'], term)
    payload['closes_at'] = payload['opens_at']
    response = client.post('/api/v1/exams/', payload, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'closes_at' in response.data

    response = client.post(
        '/api/v1/exams/',
        exam_payload(question_bank_data['course'], term, duration='00:00:00'),
        format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'duration' in response.data

    response = client.post(
        '/api/v1/exams/',
        exam_payload(question_bank_data['course'], other_term),
        format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'term' in response.data

    response = client.post(
        '/api/v1/exams/',
        exam_payload(question_bank_data['other_course'], term),
        format='json',
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND

    payload = exam_payload(question_bank_data['course'], term)
    payload.pop('result_release_at')
    response = client.post('/api/v1/exams/', payload, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'result_release_at' in response.data


@pytest.mark.django_db
def test_pupil_cannot_see_or_write_draft_exam(question_bank_data):
    term = Term.objects.create(
        year=question_bank_data['year'], order=1,
        start_date=date(2026, 9, 1), end_date=date(2026, 11, 30),
    )
    exam = Exam.objects.create(
        course=question_bank_data['course'], term=term,
        title='Private draft', opens_at=timezone.now() + timedelta(days=1),
        closes_at=timezone.now() + timedelta(days=1, hours=2),
        duration=timedelta(hours=1), attempt_limit=1,
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['pupil'])
    assert client.get('/api/v1/exams/').data == []
    assert client.get(f'/api/v1/exams/{exam.pk}/').status_code == (
        status.HTTP_404_NOT_FOUND
    )
    assert client.post(
        '/api/v1/exams/', exam_payload(question_bank_data['course'], term),
        format='json',
    ).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_teacher_cannot_update_published_exam_in_draft_metadata_api(
    question_bank_data,
):
    term = Term.objects.create(
        year=question_bank_data['year'], order=1,
        start_date=date(2026, 9, 1), end_date=date(2026, 11, 30),
    )
    exam = Exam.objects.create(
        course=question_bank_data['course'], term=term,
        title='Published metadata', opens_at=timezone.now() + timedelta(days=1),
        closes_at=timezone.now() + timedelta(days=1, hours=2),
        duration=timedelta(hours=1), attempt_limit=1,
        result_release_at=timezone.now() + timedelta(days=1, hours=2),
        state=Exam.State.PUBLISHED,
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    response = client.patch(
        f'/api/v1/exams/{exam.pk}/', {'title': 'Mutation'}, format='json',
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    exam.refresh_from_db()
    assert exam.title == 'Published metadata'


def create_draft_exam(question_bank_data):
    term_order = Term.objects.filter(year=question_bank_data['year']).count() + 1
    term = Term.objects.create(
        year=question_bank_data['year'], order=term_order,
        start_date=date(2026, 9, 1), end_date=date(2026, 11, 30),
    )
    opens_at = timezone.now() + timedelta(days=1)
    return Exam.objects.create(
        course=question_bank_data['course'], term=term,
        title='Items draft', opens_at=opens_at,
        closes_at=opens_at + timedelta(hours=2), duration=timedelta(hours=1),
        attempt_limit=1, result_release_at=opens_at + timedelta(hours=2),
    )


@pytest.mark.django_db
def test_teacher_replaces_draft_exam_items_with_question_version_references(
    question_bank_data,
):
    exam = create_draft_exam(question_bank_data)
    first_question = Question.objects.create(
        course=question_bank_data['course'], stem='First bank prompt',
    )
    second_question = Question.objects.create(
        course=question_bank_data['course'], stem='Second bank prompt',
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    response = client.put(
        f'/api/v1/exams/{exam.pk}/items/',
        [
            {
                'question': second_question.pk,
                'question_version': second_question.version,
                'points': '2.50',
                'position': 1,
            },
            {
                'question': first_question.pk,
                'question_version': first_question.version,
                'points': '1.00',
                'position': 0,
            },
        ],
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert [item['question'] for item in response.data] == [
        first_question.pk, second_question.pk,
    ]
    assert list(exam.items.values_list('question_id', 'question_version', 'position')) == [
        (first_question.pk, first_question.version, 0),
        (second_question.pk, second_question.version, 1),
    ]


@pytest.mark.django_db
def test_exam_item_invalid_input_rolls_back_entire_replacement(question_bank_data):
    exam = create_draft_exam(question_bank_data)
    own_question = Question.objects.create(
        course=question_bank_data['course'], stem='Own prompt',
    )
    other_question = Question.objects.create(
        course=question_bank_data['other_course'], stem='Other course prompt',
    )
    existing = ExamItem.objects.create(
        exam=exam, question=own_question,
        question_version=own_question.version, points='1.00', position=0,
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    foreign_response = client.put(
        f'/api/v1/exams/{exam.pk}/items/',
        [
            {
                'question': own_question.pk,
                'question_version': own_question.version,
                'points': '2.00',
                'position': 0,
            },
            {
                'question': other_question.pk,
                'question_version': other_question.version,
                'points': '2.00',
                'position': 1,
            },
        ],
        format='json',
    )
    assert foreign_response.status_code == status.HTTP_400_BAD_REQUEST
    assert list(exam.items.values_list('pk', 'points', 'position')) == [
        (existing.pk, Decimal('1.00'), 0),
    ]

    duplicate_response = client.put(
        f'/api/v1/exams/{exam.pk}/items/',
        [
            {
                'question': own_question.pk,
                'question_version': own_question.version,
                'points': '2.00',
                'position': 0,
            },
            {
                'question': own_question.pk,
                'question_version': own_question.version,
                'points': '3.00',
                'position': 0,
            },
        ],
        format='json',
    )
    assert duplicate_response.status_code == status.HTTP_400_BAD_REQUEST
    assert list(exam.items.values_list('pk', 'points', 'position')) == [
        (existing.pk, Decimal('1.00'), 0),
    ]


@pytest.mark.django_db
def test_publish_materializes_immutable_question_and_key_snapshot(
    question_bank_data,
):
    exam = create_draft_exam(question_bank_data)
    question = Question.objects.create(
        course=question_bank_data['course'], stem='Original snapshot prompt',
    )
    Option.objects.create(question=question, text='Wrong', is_correct=False)
    Option.objects.create(question=question, text='Correct', is_correct=True)
    ExamItem.objects.create(
        exam=exam, question=question, question_version=question.version,
        points='3.00', position=0,
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    publish_response = client.post(f'/api/v1/exams/{exam.pk}/publish/')

    assert publish_response.status_code == status.HTTP_200_OK
    assert publish_response.data['state'] == Exam.State.PUBLISHED
    assert publish_response.data['published_at'] is not None
    snapshot = ExamSnapshotItem.objects.get(exam=exam)
    assert snapshot.stem == 'Original snapshot prompt'
    assert snapshot.points == Decimal('3.00')
    assert list(snapshot.options.values_list('text', 'is_correct')) == [
        ('Wrong', False), ('Correct', True),
    ]

    edit_response = client.patch(
        f'/api/v1/questions/{question.pk}/',
        {
            'stem': 'Changed bank prompt',
            'options': [
                {'text': 'New wrong', 'is_correct': False},
                {'text': 'New correct', 'is_correct': True},
            ],
        },
        format='json',
    )
    assert edit_response.status_code == status.HTTP_200_OK
    snapshot.refresh_from_db()
    assert snapshot.stem == 'Original snapshot prompt'
    assert list(snapshot.options.values_list('text', 'is_correct')) == [
        ('Wrong', False), ('Correct', True),
    ]


@pytest.mark.django_db
def test_publish_retry_is_idempotent_and_invalid_draft_stays_unpublished(
    question_bank_data,
):
    exam = create_draft_exam(question_bank_data)
    question = Question.objects.create(
        course=question_bank_data['course'], stem='Retry prompt',
    )
    Option.objects.create(question=question, text='Wrong', is_correct=False)
    Option.objects.create(question=question, text='Correct', is_correct=True)
    ExamItem.objects.create(
        exam=exam, question=question, question_version=question.version,
        points='2.00', position=0,
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['teacher'])

    first_response = client.post(f'/api/v1/exams/{exam.pk}/publish/')
    second_response = client.post(f'/api/v1/exams/{exam.pk}/publish/')
    assert first_response.status_code == status.HTTP_200_OK
    assert second_response.status_code == status.HTTP_200_OK
    assert first_response.data['published_at'] == second_response.data['published_at']
    assert ExamPublishAudit.objects.filter(exam=exam).count() == 1
    assert ExamSnapshotItem.objects.filter(exam=exam).count() == 1
    assert ExamSnapshotOption.objects.filter(snapshot_item__exam=exam).count() == 2

    invalid_exam = create_draft_exam(question_bank_data)
    response = client.post(f'/api/v1/exams/{invalid_exam.pk}/publish/')
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    invalid_exam.refresh_from_db()
    assert invalid_exam.state == Exam.State.DRAFT
    assert not ExamPublishAudit.objects.filter(exam=invalid_exam).exists()

    invalid_key_exam = create_draft_exam(question_bank_data)
    invalid_question = Question.objects.create(
        course=question_bank_data['course'], stem='No valid key',
    )
    Option.objects.create(
        question=invalid_question, text='Wrong one', is_correct=False,
    )
    Option.objects.create(
        question=invalid_question, text='Wrong two', is_correct=False,
    )
    ExamItem.objects.create(
        exam=invalid_key_exam, question=invalid_question,
        question_version=invalid_question.version, points='1.00', position=0,
    )
    response = client.post(f'/api/v1/exams/{invalid_key_exam.pk}/publish/')
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    invalid_key_exam.refresh_from_db()
    assert invalid_key_exam.state == Exam.State.DRAFT


def contains_private_pupil_field(value):
    if isinstance(value, dict):
        return any(
            key in {'is_correct', 'marking_note', 'score', 'snapshot_items'}
            or contains_private_pupil_field(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(contains_private_pupil_field(item) for item in value)
    return False


@pytest.mark.django_db
def test_pupil_exam_cards_omit_private_key_and_teacher_preview_keeps_it_private(
    question_bank_data,
):
    exam = create_draft_exam(question_bank_data)
    now = timezone.now()
    exam.opens_at = now - timedelta(minutes=30)
    exam.closes_at = now + timedelta(minutes=30)
    exam.result_release_at = exam.closes_at
    exam.save(update_fields=('opens_at', 'closes_at', 'result_release_at'))
    question = Question.objects.create(
        course=question_bank_data['course'], stem='Private key prompt',
        marking_note='Rubric faqat teacher uchun.',
    )
    Option.objects.create(question=question, text='Wrong', is_correct=False)
    Option.objects.create(question=question, text='Correct', is_correct=True)
    ExamItem.objects.create(
        exam=exam, question=question, question_version=question.version,
        points='4.00', position=0,
    )
    teacher_client = APIClient()
    teacher_client.force_authenticate(question_bank_data['teacher'])
    assert teacher_client.post(
        f'/api/v1/exams/{exam.pk}/publish/'
    ).status_code == status.HTTP_200_OK

    pupil_client = APIClient()
    pupil_client.force_authenticate(question_bank_data['pupil'])
    list_response = pupil_client.get('/api/v1/exams/')
    detail_response = pupil_client.get(f'/api/v1/exams/{exam.pk}/')
    preview_response = pupil_client.get(f'/api/v1/exams/{exam.pk}/preview/')

    assert list_response.status_code == status.HTTP_200_OK
    assert [card['id'] for card in list_response.data] == [exam.pk]
    assert list_response.data[0]['status'] == 'open'
    assert detail_response.status_code == status.HTTP_200_OK
    assert set(detail_response.data) == {
        'id', 'title', 'opens_at', 'closes_at', 'duration', 'attempt_limit',
        'status',
    }
    assert not contains_private_pupil_field(list_response.data)
    assert not contains_private_pupil_field(detail_response.data)
    assert preview_response.status_code == status.HTTP_403_FORBIDDEN

    preview_response = teacher_client.get(f'/api/v1/exams/{exam.pk}/preview/')
    assert preview_response.status_code == status.HTTP_200_OK
    preview_item = preview_response.data['snapshot_items'][0]
    assert preview_item['marking_note'] == 'Rubric faqat teacher uchun.'
    assert preview_item['options'][1]['is_correct'] is True


@pytest.mark.django_db
def test_pupil_cannot_access_draft_or_other_course_exam(question_bank_data):
    draft_exam = create_draft_exam(question_bank_data)
    other_term = Term.objects.create(
        year=question_bank_data['year'], order=8,
        start_date=date(2026, 9, 1), end_date=date(2026, 11, 30),
    )
    now = timezone.now()
    other_exam = Exam.objects.create(
        course=question_bank_data['other_course'], term=other_term,
        title='Other course published', opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(hours=1), duration=timedelta(minutes=30),
        attempt_limit=1, result_release_at=now + timedelta(hours=1),
        state=Exam.State.PUBLISHED, published_at=now,
    )
    client = APIClient()
    client.force_authenticate(question_bank_data['pupil'])

    assert client.get(f'/api/v1/exams/{draft_exam.pk}/').status_code == (
        status.HTTP_404_NOT_FOUND
    )
    assert client.get(f'/api/v1/exams/{other_exam.pk}/').status_code == (
        status.HTTP_404_NOT_FOUND
    )


@pytest.mark.django_db
def test_pupil_starts_one_open_attempt_with_keyless_snapshot(question_bank_data):
    exam = create_draft_exam(question_bank_data)
    now = timezone.now()
    exam.opens_at, exam.closes_at = now - timedelta(minutes=5), now + timedelta(hours=2)
    exam.result_release_at = exam.closes_at
    exam.save(update_fields=('opens_at', 'closes_at', 'result_release_at'))
    question = Question.objects.create(course=question_bank_data['course'], stem='Attempt prompt')
    Option.objects.create(question=question, text='Wrong', is_correct=False)
    Option.objects.create(question=question, text='Correct', is_correct=True)
    ExamItem.objects.create(exam=exam, question=question, question_version=1, points='1.00', position=0)
    teacher = APIClient()
    teacher.force_authenticate(question_bank_data['teacher'])
    assert teacher.post(f'/api/v1/exams/{exam.pk}/publish/').status_code == 200
    pupil = APIClient()
    pupil.force_authenticate(question_bank_data['pupil'])
    first = pupil.post(f'/api/v1/exams/{exam.pk}/attempts/')
    second = pupil.post(f'/api/v1/exams/{exam.pk}/attempts/')
    assert first.status_code == second.status_code == status.HTTP_201_CREATED
    assert first.data['id'] == second.data['id']
    assert first.data['expires_at'] <= exam.closes_at.isoformat().replace('+00:00', 'Z')
    assert first.data['items'][0]['options'][1]['text'] == 'Correct'
    assert not contains_private_pupil_field(first.data)
    assert Attempt.objects.filter(exam=exam, pupil=question_bank_data['pupil'], state=Attempt.State.ACTIVE).count() == 1
    Attempt.objects.filter(pk=first.data['id']).update(state=Attempt.State.FINISHED)
    assert pupil.post(f'/api/v1/exams/{exam.pk}/attempts/').status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_attempt_rejects_closed_exam_inactive_enrollment_and_limit(question_bank_data):
    exam = create_draft_exam(question_bank_data)
    now = timezone.now()
    exam.state = Exam.State.PUBLISHED
    exam.published_at = now
    exam.opens_at, exam.closes_at = now - timedelta(hours=2), now - timedelta(hours=1)
    exam.save(update_fields=('state', 'published_at', 'opens_at', 'closes_at'))
    client = APIClient()
    client.force_authenticate(question_bank_data['pupil'])
    assert client.post(f'/api/v1/exams/{exam.pk}/attempts/').status_code == status.HTTP_400_BAD_REQUEST
    exam.opens_at, exam.closes_at = now - timedelta(minutes=1), now + timedelta(hours=1)
    exam.save(update_fields=('opens_at', 'closes_at'))
    enrollment = Enrollment.objects.get(
        course=question_bank_data['course'], pupil=question_bank_data['pupil']
    )
    enrollment.status = 'closed'
    enrollment.end_date = date.today()
    enrollment.save(update_fields=('status', 'end_date'))
    assert client.post(f'/api/v1/exams/{exam.pk}/attempts/').status_code == status.HTTP_400_BAD_REQUEST


def parallel_attempt_start(*, barrier, user_id, exam_id):
    connections.close_all()
    try:
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user_id))
        barrier.wait(timeout=10)
        response = client.post(f'/api/v1/exams/{exam_id}/attempts/')
        return response.status_code, response.data['id']
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != 'postgresql', reason='PostgreSQL lock testi.')
def test_parallel_attempt_start_keeps_one_active_attempt(question_bank_data):
    exam = create_draft_exam(question_bank_data)
    now = timezone.now()
    exam.opens_at, exam.closes_at, exam.result_release_at = now - timedelta(minutes=1), now + timedelta(hours=2), now + timedelta(hours=2)
    exam.save(update_fields=('opens_at', 'closes_at', 'result_release_at'))
    question = Question.objects.create(course=question_bank_data['course'], stem='Parallel prompt')
    Option.objects.create(question=question, text='No', is_correct=False)
    Option.objects.create(question=question, text='Yes', is_correct=True)
    ExamItem.objects.create(exam=exam, question=question, question_version=1, points='1.00', position=0)
    teacher = APIClient()
    teacher.force_authenticate(question_bank_data['teacher'])
    assert teacher.post(f'/api/v1/exams/{exam.pk}/publish/').status_code == 200
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=30) for future in [
            executor.submit(parallel_attempt_start, barrier=barrier, user_id=question_bank_data['pupil'].pk, exam_id=exam.pk)
            for _ in range(2)
        ]]
    assert [code for code, _ in results] == [status.HTTP_201_CREATED] * 2
    assert results[0][1] == results[1][1]
    assert Attempt.objects.filter(exam=exam, pupil=question_bank_data['pupil'], state=Attempt.State.ACTIVE).count() == 1


def create_open_attempt(question_bank_data):
    exam = create_draft_exam(question_bank_data)
    now = timezone.now()
    exam.opens_at = now - timedelta(minutes=1)
    exam.closes_at = now + timedelta(hours=2)
    exam.result_release_at = exam.closes_at
    exam.save(update_fields=('opens_at', 'closes_at', 'result_release_at'))
    single_choice = Question.objects.create(
        course=question_bank_data['course'], stem='Saved choice',
    )
    Option.objects.create(question=single_choice, text='No', is_correct=False)
    Option.objects.create(question=single_choice, text='Yes', is_correct=True)
    other_choice = Question.objects.create(
        course=question_bank_data['course'], stem='Other choice',
    )
    Option.objects.create(question=other_choice, text='Other', is_correct=True)
    essay = Question.objects.create(
        course=question_bank_data['course'], stem='Saved essay',
        type=Question.Type.ESSAY, marking_note='Private rubric',
    )
    ExamItem.objects.bulk_create([
        ExamItem(exam=exam, question=single_choice, question_version=1, points='1.00', position=0),
        ExamItem(exam=exam, question=other_choice, question_version=1, points='1.00', position=1),
        ExamItem(exam=exam, question=essay, question_version=1, points='2.00', position=2),
    ])
    teacher = APIClient()
    teacher.force_authenticate(question_bank_data['teacher'])
    assert teacher.post(f'/api/v1/exams/{exam.pk}/publish/').status_code == 200
    pupil = APIClient()
    pupil.force_authenticate(question_bank_data['pupil'])
    started = pupil.post(f'/api/v1/exams/{exam.pk}/attempts/')
    assert started.status_code == status.HTTP_201_CREATED
    return exam, pupil, started.data


@pytest.mark.django_db
def test_attempt_read_returns_keyless_saved_answers_and_server_ack(question_bank_data):
    _, pupil, started = create_open_attempt(question_bank_data)
    choice_item, _, essay_item = started['items']
    assert choice_item['answer'] is None
    choice_response = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][1]['id'], 'expected_version': 0},
        format='json',
    )
    assert choice_response.status_code == status.HTTP_200_OK
    assert choice_response.data['version'] == 1
    assert choice_response.data['saved_at']
    essay_response = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{essay_item['id']}/",
        {'essay_text': 'My private response', 'expected_version': 0},
        format='json',
    )
    assert essay_response.status_code == status.HTTP_200_OK
    loaded = pupil.get(f"/api/v1/attempts/{started['id']}/")
    assert loaded.status_code == status.HTTP_200_OK
    assert loaded.data['items'][0]['answer']['selected_option'] == choice_item['options'][1]['id']
    assert loaded.data['items'][2]['answer']['essay_text'] == 'My private response'
    assert not contains_private_pupil_field(loaded.data)

    update = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][0]['id'], 'expected_version': 1},
        format='json',
    )
    assert update.status_code == status.HTTP_200_OK
    assert update.data['version'] == 2
    stale = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][1]['id'], 'expected_version': 1},
        format='json',
    )
    assert stale.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_answer_rejects_foreign_option_expired_and_foreign_attempt(question_bank_data):
    _, pupil, started = create_open_attempt(question_bank_data)
    choice_item, other_choice_item, _ = started['items']
    foreign_option = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': other_choice_item['options'][0]['id'], 'expected_version': 0},
        format='json',
    )
    assert foreign_option.status_code == status.HTTP_400_BAD_REQUEST
    wrong_kind = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'essay_text': 'not choice', 'expected_version': 0},
        format='json',
    )
    assert wrong_kind.status_code == status.HTTP_400_BAD_REQUEST
    Attempt.objects.filter(pk=started['id']).update(state=Attempt.State.FINISHED)
    finished = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][0]['id'], 'expected_version': 0},
        format='json',
    )
    assert finished.status_code == status.HTTP_400_BAD_REQUEST
    now = timezone.now()
    Attempt.objects.filter(pk=started['id']).update(
        state=Attempt.State.ACTIVE,
        started_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    expired = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][0]['id'], 'expected_version': 0},
        format='json',
    )
    assert expired.status_code == status.HTTP_400_BAD_REQUEST

    other_pupil = User.objects.create_user(
        username='other-attempt-pupil', password='safe-test-password', role=User.Role.PUPIL,
    )
    foreign_client = APIClient()
    foreign_client.force_authenticate(other_pupil)
    forbidden = foreign_client.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][0]['id'], 'expected_version': 0},
        format='json',
    )
    assert forbidden.status_code == status.HTTP_404_NOT_FOUND


def create_objective_attempt(question_bank_data):
    exam = create_draft_exam(question_bank_data)
    now = timezone.now()
    exam.opens_at = now - timedelta(minutes=1)
    exam.closes_at = now + timedelta(hours=2)
    exam.result_release_at = exam.closes_at
    exam.save(update_fields=('opens_at', 'closes_at', 'result_release_at'))
    question = Question.objects.create(
        course=question_bank_data['course'], stem='Objective finish',
    )
    Option.objects.create(question=question, text='Wrong', is_correct=False)
    Option.objects.create(question=question, text='Right', is_correct=True)
    ExamItem.objects.create(
        exam=exam, question=question, question_version=1,
        points='3.00', position=0,
    )
    teacher = APIClient()
    teacher.force_authenticate(question_bank_data['teacher'])
    assert teacher.post(f'/api/v1/exams/{exam.pk}/publish/').status_code == 200
    pupil = APIClient()
    pupil.force_authenticate(question_bank_data['pupil'])
    started = pupil.post(f'/api/v1/exams/{exam.pk}/attempts/')
    assert started.status_code == status.HTTP_201_CREATED
    return pupil, started.data


@pytest.mark.django_db
def test_finish_scores_saved_objective_answers_and_is_idempotent(question_bank_data):
    pupil, started = create_objective_attempt(question_bank_data)
    item = started['items'][0]
    saved = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{item['id']}/",
        {'selected_option': item['options'][1]['id'], 'expected_version': 0},
        format='json',
    )
    assert saved.status_code == status.HTTP_200_OK
    injected = pupil.post(
        f"/api/v1/attempts/{started['id']}/finish/",
        {'score': '999.00', 'answer': item['options'][0]['id']},
        format='json',
    )
    assert injected.status_code == status.HTTP_400_BAD_REQUEST
    first = pupil.post(f"/api/v1/attempts/{started['id']}/finish/")
    second = pupil.post(f"/api/v1/attempts/{started['id']}/finish/")
    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert first.data == second.data
    assert first.data['status'] == AttemptResult.Status.FINALIZED
    assert not contains_private_pupil_field(first.data)
    result = AttemptResult.objects.get(attempt_id=started['id'])
    assert result.objective_score == Decimal('3.00')
    assert result.objective_max_score == Decimal('3.00')
    assert Attempt.objects.get(pk=started['id']).state == Attempt.State.FINISHED


@pytest.mark.django_db
def test_finish_expired_attempt_uses_only_saved_answers_and_marks_essay_pending(question_bank_data):
    _, pupil, started = create_open_attempt(question_bank_data)
    choice_item, _, _ = started['items']
    assert pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][1]['id'], 'expected_version': 0},
        format='json',
    ).status_code == status.HTTP_200_OK
    now = timezone.now()
    Attempt.objects.filter(pk=started['id']).update(
        started_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    late_answer = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][0]['id'], 'expected_version': 1},
        format='json',
    )
    assert late_answer.status_code == status.HTTP_400_BAD_REQUEST
    finish = pupil.post(f"/api/v1/attempts/{started['id']}/finish/")
    assert finish.status_code == status.HTTP_200_OK
    assert finish.data['status'] == AttemptResult.Status.PENDING_MANUAL
    result = AttemptResult.objects.get(attempt_id=started['id'])
    assert result.objective_score == Decimal('1.00')
    assert result.objective_max_score == Decimal('2.00')


def parallel_finish(*, barrier, user_id, attempt_id):
    connections.close_all()
    try:
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user_id))
        barrier.wait(timeout=10)
        response = client.post(f'/api/v1/attempts/{attempt_id}/finish/')
        return response.status_code, response.data
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != 'postgresql', reason='PostgreSQL lock testi.')
def test_parallel_finish_creates_one_result(question_bank_data):
    pupil, started = create_objective_attempt(question_bank_data)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=30) for future in [
            executor.submit(
                parallel_finish,
                barrier=barrier,
                user_id=question_bank_data['pupil'].pk,
                attempt_id=started['id'],
            )
            for _ in range(2)
        ]]
    assert [code for code, _ in results] == [status.HTTP_200_OK] * 2
    assert results[0][1] == results[1][1]
    assert AttemptResult.objects.filter(attempt_id=started['id']).count() == 1


@pytest.mark.django_db
def test_essay_grading_finalizes_total_after_release_and_uses_exam_gradebook(question_bank_data):
    exam, pupil, started = create_open_attempt(question_bank_data)
    choice_item, _, essay_item = started['items']
    assert pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{choice_item['id']}/",
        {'selected_option': choice_item['options'][1]['id'], 'expected_version': 0},
        format='json',
    ).status_code == status.HTTP_200_OK
    assert pupil.post(f"/api/v1/attempts/{started['id']}/finish/").data['status'] == 'pending_manual'

    teacher = APIClient()
    teacher.force_authenticate(question_bank_data['teacher'])
    grade_url = f"/api/v1/attempts/{started['id']}/items/{essay_item['id']}/grade/"
    assert teacher.put(
        grade_url, {'points': '2.00', 'feedback': 'Good', 'expected_version': 0},
        format='json',
    ).status_code == status.HTTP_404_NOT_FOUND
    TeachingAssignment.objects.filter(
        course=question_bank_data['course'], teacher=question_bank_data['teacher'],
    ).update(can_grade=True)
    too_high = teacher.put(
        grade_url, {'points': '2.01', 'feedback': '', 'expected_version': 0},
        format='json',
    )
    assert too_high.status_code == status.HTTP_400_BAD_REQUEST
    graded = teacher.put(
        grade_url, {'points': '2.00', 'feedback': 'Good', 'expected_version': 0},
        format='json',
    )
    assert graded.status_code == status.HTTP_200_OK
    assert graded.data['version'] == 1
    regraded = teacher.put(
        grade_url, {'points': '1.50', 'feedback': 'Updated', 'expected_version': 1},
        format='json',
    )
    assert regraded.status_code == status.HTTP_200_OK
    assert regraded.data['version'] == 2
    grading_read = teacher.get(f"/api/v1/attempts/{started['id']}/grading/")
    assert grading_read.status_code == status.HTTP_200_OK
    assert grading_read.data['essay_items'][0]['max_score'] == Decimal('2.00')
    assert len(grading_read.data['essay_items'][0]['audit']) == 2
    grade = AttemptGrade.objects.get(attempt_item_id=essay_item['id'])
    assert AttemptGradeAudit.objects.filter(grade=grade).count() == 2
    result = AttemptResult.objects.get(attempt_id=started['id'])
    assert result.status == AttemptResult.Status.FINALIZED
    assert result.objective_score == Decimal('1.00')
    assert result.total_score == Decimal('2.50')
    assert result.total_max_score == Decimal('4.00')

    result_url = f"/api/v1/attempts/{started['id']}/result/"
    assert pupil.get(result_url).status_code == status.HTTP_404_NOT_FOUND
    now = timezone.now()
    exam.opens_at = now - timedelta(minutes=3)
    exam.closes_at = now - timedelta(minutes=2)
    exam.result_release_at = now - timedelta(minutes=1)
    exam.save(update_fields=('opens_at', 'closes_at', 'result_release_at'))
    released = pupil.get(result_url)
    assert released.status_code == status.HTTP_200_OK
    assert released.data['total_score'] == Decimal('2.50')
    assert pupil.get('/api/v1/me/exam-gradebook/').data[0]['source_kind'] == 'exam'
    teacher_book = teacher.get(f'/api/v1/courses/{exam.course_id}/exam-gradebook/')
    assert teacher_book.status_code == status.HTTP_200_OK
    assert len(teacher_book.data) == 1
    assert teacher_book.data[0]['source_kind'] == 'exam'


@pytest.mark.django_db
def test_pending_essay_result_hides_total_and_other_scopes_are_rejected(question_bank_data):
    exam, pupil, started = create_open_attempt(question_bank_data)
    assert pupil.post(f"/api/v1/attempts/{started['id']}/finish/").status_code == status.HTTP_200_OK
    now = timezone.now()
    exam.opens_at = now - timedelta(minutes=3)
    exam.closes_at = now - timedelta(minutes=2)
    exam.result_release_at = now - timedelta(minutes=1)
    exam.save(update_fields=('opens_at', 'closes_at', 'result_release_at'))
    response = pupil.get(f"/api/v1/attempts/{started['id']}/result/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data['status'] == AttemptResult.Status.PENDING_MANUAL
    assert response.data['total_score'] is None
    assert response.data['total_max_score'] is None

    other_teacher = APIClient()
    other_teacher.force_authenticate(question_bank_data['other_teacher'])
    assert other_teacher.get(f'/api/v1/attempts/{started["id"]}/grading/').status_code == status.HTTP_404_NOT_FOUND
    other_pupil = User.objects.create_user(
        username='result-other-pupil', password='safe-test-password', role=User.Role.PUPIL,
    )
    other_client = APIClient()
    other_client.force_authenticate(other_pupil)
    assert other_client.get(f'/api/v1/attempts/{started["id"]}/result/').status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_expired_attempt_read_finalizes_and_sweep_is_idempotent(question_bank_data):
    pupil, started = create_objective_attempt(question_bank_data)
    now = timezone.now()
    Attempt.objects.filter(pk=started['id']).update(
        started_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    read = pupil.get(f"/api/v1/attempts/{started['id']}/")
    assert read.status_code == status.HTTP_200_OK
    assert read.data['state'] == Attempt.State.FINISHED
    assert AttemptResult.objects.filter(attempt_id=started['id']).count() == 1

    _, second_started = create_objective_attempt(question_bank_data)
    Attempt.objects.filter(pk=second_started['id']).update(
        started_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    assert finalize_expired_attempts.run() == 1
    assert finalize_expired_attempts.run() == 0
    assert AttemptResult.objects.filter(attempt_id=second_started['id']).count() == 1


def parallel_expiry_sweep(*, barrier):
    connections.close_all()
    try:
        barrier.wait(timeout=10)
        return finalize_expired_attempts.run()
    finally:
        connections.close_all()


def parallel_expired_finish(*, barrier, user_id, attempt_id):
    connections.close_all()
    try:
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user_id))
        barrier.wait(timeout=10)
        return client.post(f'/api/v1/attempts/{attempt_id}/finish/').status_code
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != 'postgresql', reason='PostgreSQL lock testi.')
def test_expiry_sweep_and_api_finish_share_one_finalization(question_bank_data):
    _, started = create_objective_attempt(question_bank_data)
    now = timezone.now()
    Attempt.objects.filter(pk=started['id']).update(
        started_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        sweep = executor.submit(parallel_expiry_sweep, barrier=barrier)
        finish = executor.submit(
            parallel_expired_finish,
            barrier=barrier,
            user_id=question_bank_data['pupil'].pk,
            attempt_id=started['id'],
        )
        assert sweep.result(timeout=30) in (0, 1)
        assert finish.result(timeout=30) == status.HTTP_200_OK
    assert AttemptResult.objects.filter(attempt_id=started['id']).count() == 1


@pytest.mark.django_db
def test_p072_attempt_lifecycle_contract_from_start_to_released_result(question_bank_data):
    pupil, started = create_objective_attempt(question_bank_data)
    attempt = Attempt.objects.select_related('exam').get(pk=started['id'])
    second_start = pupil.post(f'/api/v1/exams/{attempt.exam_id}/attempts/')
    assert second_start.status_code == status.HTTP_201_CREATED
    assert second_start.data['id'] == started['id']

    item = started['items'][0]
    saved = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{item['id']}/",
        {'selected_option': item['options'][1]['id'], 'expected_version': 0},
        format='json',
    )
    assert saved.status_code == status.HTTP_200_OK
    stale = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{item['id']}/",
        {'selected_option': item['options'][0]['id'], 'expected_version': 0},
        format='json',
    )
    assert stale.status_code == status.HTTP_400_BAD_REQUEST

    now = timezone.now()
    Attempt.objects.filter(pk=started['id']).update(
        started_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    expired_write = pupil.put(
        f"/api/v1/attempts/{started['id']}/answers/{item['id']}/",
        {'selected_option': item['options'][0]['id'], 'expected_version': 1},
        format='json',
    )
    assert expired_write.status_code == status.HTTP_400_BAD_REQUEST
    assert finalize_expired_attempts.run() == 1

    first_finish = pupil.post(f"/api/v1/attempts/{started['id']}/finish/")
    second_finish = pupil.post(f"/api/v1/attempts/{started['id']}/finish/")
    assert first_finish.status_code == second_finish.status_code == status.HTTP_200_OK
    assert first_finish.data == second_finish.data

    attempt.exam.opens_at = now - timedelta(minutes=3)
    attempt.exam.closes_at = now - timedelta(minutes=2)
    attempt.exam.result_release_at = now - timedelta(minutes=1)
    attempt.exam.save(update_fields=('opens_at', 'closes_at', 'result_release_at'))
    result = pupil.get(f"/api/v1/attempts/{started['id']}/result/")
    assert result.status_code == status.HTTP_200_OK
    assert result.data['total_score'] == Decimal('3.00')
    payloads = [started, second_start.data, pupil.get(f"/api/v1/attempts/{started['id']}/").data]
    assert all(not contains_private_pupil_field(payload) for payload in payloads)

