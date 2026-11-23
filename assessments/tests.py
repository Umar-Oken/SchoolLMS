from datetime import date, timedelta
from decimal import Decimal

import pytest
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
from assessments.models import (
    Exam,
    ExamItem,
    ExamPublishAudit,
    ExamSnapshotItem,
    ExamSnapshotOption,
    Option,
    Question,
)


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

