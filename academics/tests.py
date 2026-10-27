from datetime import date
from types import SimpleNamespace

import secrets
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from academics.models import (
    AcademicYear,
    ClassMembership,
    CourseOffering,
    Enrollment,
    MembershipStatus,
    SchoolClass,
    Subject,
    TeachingAssignment,
    Term,
)
from academics.services import (
    assign_teacher,
    close_teaching_assignment,
    enroll_pupil_in_class,
    replace_course_teacher,
)
from academics.views import IsCoursePupil, IsCourseTeacher


@pytest.fixture
def academic_year(db):
    return AcademicYear.objects.create(
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        label='2026–2027',
    )


@pytest.mark.django_db
def test_academic_year_rejects_reversed_dates():
    with pytest.raises(IntegrityError), transaction.atomic():
        AcademicYear.objects.create(
            start_date=date(2026, 9, 1),
            end_date=date(2026, 8, 31),
            label='2026–2027',
        )


@pytest.mark.django_db
def test_term_rejects_reversed_dates():
    year = AcademicYear.objects.create(
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        label='2026–2027',
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Term.objects.create(
            year=year,
            order=1,
            start_date=date(2026, 10, 1),
            end_date=date(2026, 9, 30),
        )


@pytest.mark.django_db
def test_term_rejects_duplicate_order_within_year():
    year = AcademicYear.objects.create(
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        label='2026–2027',
    )
    Term.objects.create(
        year=year,
        order=1,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 11, 30),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Term.objects.create(
            year=year,
            order=1,
            start_date=date(2026, 12, 1),
            end_date=date(2027, 2, 28),
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('start_date', 'end_date', 'invalid_field'),
    [
        (date(2026, 8, 31), date(2026, 10, 1), 'start_date'),
        (date(2027, 6, 1), date(2027, 7, 1), 'end_date'),
    ],
)
def test_term_validation_rejects_dates_outside_academic_year(
    start_date,
    end_date,
    invalid_field,
):
    year = AcademicYear.objects.create(
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        label='2026–2027',
    )
    term = Term(
        year=year,
        order=1,
        start_date=start_date,
        end_date=end_date,
    )

    with pytest.raises(ValidationError) as exc_info:
        term.full_clean()

    assert invalid_field in exc_info.value.message_dict


@pytest.mark.django_db
def test_school_class_rejects_grade_outside_one_to_eleven(academic_year):
    with pytest.raises(IntegrityError), transaction.atomic():
        SchoolClass.objects.create(
            year=academic_year,
            grade_level=12,
            section='A',
        )


@pytest.mark.django_db
def test_school_class_rejects_duplicate_year_grade_section(academic_year):
    SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='A',
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        SchoolClass.objects.create(
            year=academic_year,
            grade_level=7,
            section='A',
        )


@pytest.mark.django_db
def test_subject_can_be_offered_in_two_classes(academic_year):
    class_a = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='A',
    )
    class_b = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='B',
    )
    mathematics = Subject.objects.create(code='MATH', title='Matematika')

    offering_a = CourseOffering.objects.create(
        school_class=class_a,
        subject=mathematics,
    )
    offering_b = CourseOffering.objects.create(
        school_class=class_b,
        subject=mathematics,
    )

    assert offering_a.subject_id == offering_b.subject_id
    assert mathematics.course_offerings.count() == 2


@pytest.mark.django_db
def test_course_offering_rejects_duplicate_class_subject(academic_year):
    school_class = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='A',
    )
    subject = Subject.objects.create(code='MATH', title='Matematika')
    CourseOffering.objects.create(school_class=school_class, subject=subject)

    with pytest.raises(IntegrityError), transaction.atomic():
        CourseOffering.objects.create(school_class=school_class, subject=subject)


@pytest.mark.django_db
@pytest.mark.parametrize('entity', ['school_class', 'subject', 'year'])
def test_course_offering_protects_historical_entities(academic_year, entity):
    school_class = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='A',
    )
    subject = Subject.objects.create(code='MATH', title='Matematika')
    CourseOffering.objects.create(school_class=school_class, subject=subject)
    protected_object = {
        'school_class': school_class,
        'subject': subject,
        'year': academic_year,
    }[entity]

    with pytest.raises(ProtectedError):
        with transaction.atomic():
            protected_object.delete()


@pytest.fixture
def course_offering(academic_year):
    school_class = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='A',
    )
    subject = Subject.objects.create(code='MATH', title='Matematika')
    return CourseOffering.objects.create(school_class=school_class, subject=subject)


@pytest.fixture
def teacher():
    return User.objects.create_user(
        username='teacher-one',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )


@pytest.mark.django_db
def test_assignment_rejects_pupil(course_offering):
    pupil = User.objects.create_user(
        username='pupil-one',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )

    with pytest.raises(ValidationError, match='Faqat o‘qituvchi'):
        assign_teacher(course_offering, pupil, date(2026, 9, 1))


@pytest.mark.django_db
def test_assignment_rejects_inactive_teacher(course_offering, teacher):
    teacher.is_active = False
    teacher.save(update_fields=['is_active'])

    with pytest.raises(ValidationError, match='Faol bo‘lmagan'):
        assign_teacher(course_offering, teacher, date(2026, 9, 1))


@pytest.mark.django_db
def test_assignment_rejects_archived_course(course_offering, teacher):
    course_offering.archived_at = '2026-08-31T00:00:00Z'
    course_offering.save(update_fields=['archived_at'])

    with pytest.raises(ValidationError, match='Arxivlangan kursga'):
        assign_teacher(course_offering, teacher, date(2026, 9, 1))


@pytest.mark.django_db
def test_assignment_rejects_end_date_before_start_date(course_offering, teacher):
    assignment = assign_teacher(course_offering, teacher, date(2026, 9, 1))

    with pytest.raises(ValidationError) as exc_info:
        close_teaching_assignment(assignment, date(2026, 8, 31))

    assert 'end_date' in exc_info.value.message_dict


@pytest.mark.django_db
def test_teacher_replacement_closes_old_assignment_and_keeps_history(
    course_offering,
    teacher,
):
    old_assignment = assign_teacher(course_offering, teacher, date(2026, 9, 1))
    new_teacher = User.objects.create_user(
        username='teacher-two',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )

    new_assignment = replace_course_teacher(
        course_offering,
        new_teacher,
        date(2027, 1, 1),
        can_grade=True,
    )

    old_assignment.refresh_from_db()
    assert old_assignment.end_date == date(2026, 12, 31)
    assert old_assignment.teacher == teacher
    assert new_assignment.start_date == date(2027, 1, 1)
    assert new_assignment.can_grade is True
    assert course_offering.teaching_assignments.count() == 2


@pytest.mark.django_db
def test_assignment_dates_are_checked_by_database(course_offering, teacher):
    with pytest.raises(IntegrityError), transaction.atomic():
        TeachingAssignment.objects.create(
            course=course_offering,
            teacher=teacher,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 8, 31),
        )


@pytest.fixture
def pupil():
    return User.objects.create_user(
        username='pupil-membership',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )


@pytest.fixture
def school_class(academic_year):
    return SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='A',
    )


@pytest.mark.django_db
def test_enrolling_pupil_creates_membership_and_active_course_enrollments(
    school_class,
    pupil,
):
    math = Subject.objects.create(code='MATH', title='Matematika')
    reading = Subject.objects.create(code='READ', title='O‘qish')
    archived_subject = Subject.objects.create(code='ARCH', title='Arxiv fan')
    courses = [
        CourseOffering.objects.create(school_class=school_class, subject=subject)
        for subject in (math, reading, archived_subject)
    ]
    courses[-1].archived_at = '2026-08-31T00:00:00Z'
    courses[-1].save(update_fields=['archived_at'])

    membership = enroll_pupil_in_class(pupil, school_class, date(2026, 9, 1))

    assert membership.status == MembershipStatus.ACTIVE
    assert membership.end_date is None
    enrollments = Enrollment.objects.filter(pupil=pupil, status=MembershipStatus.ACTIVE)
    assert set(enrollments.values_list('course_id', flat=True)) == {
        courses[0].pk,
        courses[1].pk,
    }


@pytest.mark.django_db
def test_enrolling_teacher_as_pupil_is_rejected(school_class, teacher):
    with pytest.raises(ValidationError, match='Faqat o‘quvchi'):
        enroll_pupil_in_class(teacher, school_class, date(2026, 9, 1))

    assert not ClassMembership.objects.exists()
    assert not Enrollment.objects.exists()


@pytest.mark.django_db
def test_database_rejects_two_active_class_memberships(
    school_class,
    academic_year,
    pupil,
):
    other_class = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='B',
    )
    ClassMembership.objects.create(
        pupil=pupil,
        school_class=school_class,
        start_date=date(2026, 9, 1),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        ClassMembership.objects.create(
            pupil=pupil,
            school_class=other_class,
            start_date=date(2027, 1, 1),
        )


@pytest.mark.django_db
def test_class_change_closes_enrollment_and_keeps_history(
    school_class,
    academic_year,
    pupil,
):
    math = Subject.objects.create(code='MATH', title='Matematika')
    old_course = CourseOffering.objects.create(school_class=school_class, subject=math)
    new_class = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='B',
    )
    new_course = CourseOffering.objects.create(school_class=new_class, subject=math)
    old_membership = enroll_pupil_in_class(pupil, school_class, date(2026, 9, 1))
    old_enrollment = Enrollment.objects.get(pupil=pupil, course=old_course)

    new_membership = enroll_pupil_in_class(pupil, new_class, date(2027, 1, 1))

    old_membership.refresh_from_db()
    old_enrollment.refresh_from_db()
    assert old_membership.status == MembershipStatus.CLOSED
    assert old_membership.end_date == date(2026, 12, 31)
    assert old_enrollment.status == MembershipStatus.CLOSED
    assert old_enrollment.end_date == date(2026, 12, 31)
    assert ClassMembership.objects.filter(pupil=pupil).count() == 2
    assert Enrollment.objects.filter(pupil=pupil, course=old_course).count() == 1
    assert Enrollment.objects.get(pupil=pupil, course=new_course).status == MembershipStatus.ACTIVE
    assert new_membership.status == MembershipStatus.ACTIVE


@pytest.mark.django_db
def test_class_change_rejects_backdated_overlap_without_partial_updates(
    school_class,
    academic_year,
    pupil,
):
    math = Subject.objects.create(code='MATH', title='Matematika')
    CourseOffering.objects.create(school_class=school_class, subject=math)
    new_class = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='B',
    )
    new_course = CourseOffering.objects.create(school_class=new_class, subject=math)
    current_membership = enroll_pupil_in_class(pupil, school_class, date(2026, 9, 1))
    current_enrollment = Enrollment.objects.get(
        pupil=pupil,
        course__school_class=school_class,
    )
    Enrollment.objects.create(
        pupil=pupil,
        course=new_course,
        start_date=date(2027, 1, 10),
        end_date=date(2027, 1, 20),
        status=MembershipStatus.CLOSED,
    )

    with pytest.raises(ValidationError, match='ustma-ust'):
        enroll_pupil_in_class(pupil, new_class, date(2027, 1, 1))

    current_membership.refresh_from_db()
    current_enrollment.refresh_from_db()
    assert current_membership.status == MembershipStatus.ACTIVE
    assert current_membership.end_date is None
    assert current_enrollment.status == MembershipStatus.ACTIVE
    assert current_enrollment.end_date is None
    assert ClassMembership.objects.filter(pupil=pupil).count() == 1


@pytest.mark.django_db
def test_class_change_rolls_back_when_enrollment_write_fails(
    school_class,
    academic_year,
    pupil,
    monkeypatch,
):
    math = Subject.objects.create(code='MATH', title='Matematika')
    old_course = CourseOffering.objects.create(school_class=school_class, subject=math)
    new_class = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='B',
    )
    CourseOffering.objects.create(school_class=new_class, subject=math)
    old_membership = enroll_pupil_in_class(pupil, school_class, date(2026, 9, 1))
    old_enrollment = Enrollment.objects.get(pupil=pupil, course=old_course)

    def fail_save(self, *args, **kwargs):
        raise RuntimeError('Sintetik yozuv xatosi')

    monkeypatch.setattr(Enrollment, 'save', fail_save)

    with pytest.raises(RuntimeError, match='Sintetik yozuv xatosi'):
        enroll_pupil_in_class(pupil, new_class, date(2027, 1, 1))

    old_membership.refresh_from_db()
    old_enrollment.refresh_from_db()
    assert old_membership.status == MembershipStatus.ACTIVE
    assert old_membership.end_date is None
    assert old_enrollment.status == MembershipStatus.ACTIVE
    assert old_enrollment.end_date is None
    assert ClassMembership.objects.filter(pupil=pupil).count() == 1


@pytest.mark.django_db
def test_database_rejects_duplicate_active_pupil_course_enrollment(
    pupil,
    course_offering,
):
    Enrollment.objects.create(
        pupil=pupil,
        course=course_offering,
        start_date=date(2026, 9, 1),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.create(
            pupil=pupil,
            course=course_offering,
            start_date=date(2027, 1, 1),
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('status', 'end_date'),
    [
        (MembershipStatus.ACTIVE, date(2026, 8, 31)),
        (MembershipStatus.CLOSED, None),
    ],
)
def test_membership_database_requires_consistent_status_and_dates(
    pupil,
    school_class,
    status,
    end_date,
):
    with pytest.raises(IntegrityError), transaction.atomic():
        ClassMembership.objects.create(
            pupil=pupil,
            school_class=school_class,
            start_date=date(2026, 9, 1),
            end_date=end_date,
            status=status,
        )


@pytest.mark.django_db
def test_enrollment_database_rejects_reversed_dates(pupil, course_offering):
    with pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.create(
            pupil=pupil,
            course=course_offering,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 8, 31),
            status=MembershipStatus.CLOSED,
        )


@pytest.fixture
def seed_school_passwords(monkeypatch):
    users = (
        ('seed.teacher.one', 'SEED_TEACHER_ONE_PASSWORD'),
        ('seed.teacher.two', 'SEED_TEACHER_TWO_PASSWORD'),
        ('seed.pupil.one', 'SEED_PUPIL_ONE_PASSWORD'),
        ('seed.pupil.two', 'SEED_PUPIL_TWO_PASSWORD'),
        ('seed.pupil.three', 'SEED_PUPIL_THREE_PASSWORD'),
        ('seed.pupil.four', 'SEED_PUPIL_FOUR_PASSWORD'),
    )
    passwords = _SeedSchoolPasswords()
    for username, password_env in users:
        password = secrets.token_urlsafe(24)
        monkeypatch.setenv(password_env, password)
        passwords[username] = password
    return passwords


class _SeedSchoolPasswords(dict):
    def __repr__(self):
        return '<seed passwords>'


@pytest.mark.django_db
@override_settings(APP_ENV='test')
def test_seed_school_is_idempotent_and_hashes_user_passwords(seed_school_passwords):
    output = StringIO()

    call_command('seed_school', stdout=output)
    initial_counts = (
        AcademicYear.objects.count(),
        Term.objects.count(),
        SchoolClass.objects.count(),
        User.objects.count(),
        Subject.objects.count(),
        CourseOffering.objects.count(),
        TeachingAssignment.objects.count(),
        ClassMembership.objects.count(),
        Enrollment.objects.count(),
    )
    call_command('seed_school', stdout=StringIO())
    repeated_counts = (
        AcademicYear.objects.count(),
        Term.objects.count(),
        SchoolClass.objects.count(),
        User.objects.count(),
        Subject.objects.count(),
        CourseOffering.objects.count(),
        TeachingAssignment.objects.count(),
        ClassMembership.objects.count(),
        Enrollment.objects.count(),
    )

    assert initial_counts == (1, 4, 2, 6, 3, 6, 6, 4, 12)
    assert repeated_counts == initial_counts
    assert 'Synthetic seed tayyor' in output.getvalue()

    users = User.objects.all()
    assert all(user.must_change_password for user in users)
    for username, password in seed_school_passwords.items():
        user = User.objects.get(username=username)
        assert user.check_password(password)
        assert user.password.startswith('pbkdf2_sha256$')


@pytest.mark.django_db
@override_settings(APP_ENV='production')
def test_seed_school_is_rejected_in_production(seed_school_passwords):
    with pytest.raises(CommandError, match='faqat development yoki test'):
        call_command('seed_school', stdout=StringIO())

    assert not AcademicYear.objects.exists()
    assert not User.objects.exists()


@pytest.fixture
def course_api_data(academic_year):
    class_a = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='A',
    )
    class_b = SchoolClass.objects.create(
        year=academic_year,
        grade_level=7,
        section='B',
    )
    subject = Subject.objects.create(code='API-MATH', title='Matematika')
    course_a = CourseOffering.objects.create(
        school_class=class_a,
        subject=subject,
    )
    course_b = CourseOffering.objects.create(
        school_class=class_b,
        subject=subject,
    )
    teacher_a = User.objects.create_user(
        username='api-teacher-a',
        first_name='Teacher',
        last_name='A',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    teacher_b = User.objects.create_user(
        username='api-teacher-b',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    assign_teacher(course_a, teacher_a, date(2026, 9, 1))
    assign_teacher(course_b, teacher_b, date(2026, 9, 1))
    pupil_a = User.objects.create_user(
        username='api-pupil-a',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    pupil_b = User.objects.create_user(
        username='api-pupil-b',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    enroll_pupil_in_class(pupil_a, class_a, date(2026, 9, 1))
    enroll_pupil_in_class(pupil_b, class_b, date(2026, 9, 1))
    return {
        'course_a': course_a,
        'course_b': course_b,
        'teacher_a': teacher_a,
        'teacher_b': teacher_b,
        'pupil_a': pupil_a,
        'pupil_b': pupil_b,
    }


@pytest.mark.django_db
def test_course_list_is_scoped_to_pupil_enrollments_and_has_explicit_fields(
    course_api_data,
):
    client = APIClient()
    client.force_authenticate(user=course_api_data['pupil_a'])

    response = client.get('/api/v1/courses/')

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data) == 1
    course = response.data[0]
    assert course['id'] == course_api_data['course_a'].pk
    assert set(course) == {'id', 'subject', 'school_class', 'teacher_display', 'year'}
    assert course['subject'] == {'code': 'API-MATH', 'title': 'Matematika'}
    assert course['school_class'] == {'grade_level': 7, 'section': 'A'}
    assert course['teacher_display'] == 'Teacher A'
    assert course['year'] == {'label': '2026–2027'}
    assert 'password' not in response.content.decode()
    assert 'is_staff' not in response.content.decode()


@pytest.mark.django_db
def test_pupil_cannot_read_another_classes_course_by_list_or_detail(course_api_data):
    client = APIClient()
    client.force_authenticate(user=course_api_data['pupil_a'])

    list_response = client.get('/api/v1/courses/')
    detail_response = client.get(
        f"/api/v1/courses/{course_api_data['course_b'].pk}/"
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert course_api_data['course_b'].pk not in {
        item['id'] for item in list_response.data
    }
    assert detail_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_teacher_course_list_and_detail_are_assignment_scoped(course_api_data):
    client = APIClient()
    client.force_authenticate(user=course_api_data['teacher_a'])

    list_response = client.get('/api/v1/courses/')
    own_detail = client.get(
        f"/api/v1/courses/{course_api_data['course_a'].pk}/"
    )
    other_detail = client.get(
        f"/api/v1/courses/{course_api_data['course_b'].pk}/"
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert [item['id'] for item in list_response.data] == [
        course_api_data['course_a'].pk
    ]
    assert own_detail.status_code == status.HTTP_200_OK
    assert other_detail.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('user_key', 'own_course_key', 'other_course_key'),
    [
        ('pupil_a', 'course_a', 'course_b'),
        ('pupil_b', 'course_b', 'course_a'),
        ('teacher_a', 'course_a', 'course_b'),
        ('teacher_b', 'course_b', 'course_a'),
    ],
)
def test_course_list_and_detail_reject_ids_from_other_users(
    course_api_data,
    user_key,
    own_course_key,
    other_course_key,
):
    client = APIClient()
    client.force_authenticate(user=course_api_data[user_key])
    own_course_id = course_api_data[own_course_key].pk
    other_course_id = course_api_data[other_course_key].pk

    list_response = client.get('/api/v1/courses/')
    own_detail = client.get(f'/api/v1/courses/{own_course_id}/')
    swapped_detail = client.get(f'/api/v1/courses/{other_course_id}/')

    assert list_response.status_code == status.HTTP_200_OK
    assert own_course_id in {item['id'] for item in list_response.data}
    assert other_course_id not in {item['id'] for item in list_response.data}
    assert own_detail.status_code == status.HTTP_200_OK
    assert swapped_detail.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_pupil_can_read_course_with_closed_enrollment_but_cannot_write(
    course_api_data,
):
    pupil = course_api_data['pupil_a']
    course = course_api_data['course_a']
    enrollment = Enrollment.objects.get(pupil=pupil, course=course)
    permission = IsCoursePupil()
    write_request = SimpleNamespace(user=pupil, method='PATCH')
    assert permission.has_object_permission(write_request, None, course)

    enrollment.status = MembershipStatus.CLOSED
    enrollment.end_date = date(2027, 1, 31)
    enrollment.save(update_fields=['status', 'end_date'])
    client = APIClient()
    client.force_authenticate(user=pupil)
    read_request = SimpleNamespace(user=pupil, method='GET')

    assert client.get('/api/v1/courses/').data[0]['id'] == course.pk
    assert client.get(f'/api/v1/courses/{course.pk}/').status_code == status.HTTP_200_OK
    assert permission.has_object_permission(read_request, None, course)
    assert not permission.has_object_permission(write_request, None, course)


@pytest.mark.django_db
def test_teacher_history_is_readable_but_closed_or_non_grading_assignment_cannot_write(
    course_api_data,
):
    teacher = course_api_data['teacher_a']
    course = course_api_data['course_a']
    old_assignment = TeachingAssignment.objects.get(teacher=teacher, course=course)
    old_assignment.can_grade = True
    old_assignment.save(update_fields=['can_grade'])
    permission = IsCourseTeacher()
    write_request = SimpleNamespace(user=teacher, method='PATCH')
    assert permission.has_object_permission(write_request, None, course)

    close_teaching_assignment(old_assignment, date(2027, 1, 31))
    assign_teacher(course, teacher, date(2027, 2, 1), can_grade=False)
    client = APIClient()
    client.force_authenticate(user=teacher)
    read_request = SimpleNamespace(user=teacher, method='GET')

    assert client.get('/api/v1/courses/').data[0]['id'] == course.pk
    assert client.get(f'/api/v1/courses/{course.pk}/').status_code == status.HTTP_200_OK
    assert permission.has_object_permission(read_request, None, course)
    assert not permission.has_object_permission(write_request, None, course)


@pytest.mark.django_db
def test_course_endpoints_reject_anonymous_requests():
    client = APIClient()

    assert client.get('/api/v1/courses/').status_code == status.HTTP_403_FORBIDDEN
    assert client.get('/api/v1/courses/1/').status_code == status.HTTP_403_FORBIDDEN


@pytest.fixture
def login_user(db):
    return User.objects.create_user(
        username='login-pupil',
        password='synthetic-valid-password',
        role=User.Role.PUPIL,
    )


@pytest.fixture
def csrf_client():
    return APIClient(enforce_csrf_checks=True)


@pytest.mark.django_db
def test_login_rejects_missing_csrf_token(csrf_client, login_user):
    response = csrf_client.post(
        '/api/v1/auth/login/',
        {'username': login_user.username, 'password': 'synthetic-valid-password'},
        format='json',
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert '_auth_user_id' not in csrf_client.session


@pytest.mark.django_db
def test_csrf_endpoint_sets_cookie_and_valid_login_rotates_session(
    csrf_client,
    login_user,
):
    csrf_response = csrf_client.get('/api/v1/auth/csrf/')
    csrf_token = csrf_response.data['csrfToken']
    assert csrf_response.status_code == status.HTTP_200_OK
    assert 'csrftoken' in csrf_client.cookies

    session = csrf_client.session
    session['pre_login'] = 'session'
    session.save()
    old_session_key = session.session_key

    response = csrf_client.post(
        '/api/v1/auth/login/',
        {'username': login_user.username, 'password': 'synthetic-valid-password'},
        format='json',
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == status.HTTP_200_OK
    assert csrf_client.session['_auth_user_id'] == str(login_user.pk)
    assert csrf_client.session.session_key != old_session_key
    assert response.data == {'detail': 'Muvaffaqiyatli tizimga kirildi.'}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('username', 'password'),
    [
        ('missing-login-user', 'synthetic-valid-password'),
        ('login-pupil', 'wrong-synthetic-password'),
    ],
)
def test_login_failure_is_generic_and_does_not_log_secrets(
    csrf_client,
    login_user,
    username,
    password,
    caplog,
):
    csrf_token = csrf_client.get('/api/v1/auth/csrf/').data['csrfToken']

    response = csrf_client.post(
        '/api/v1/auth/login/',
        {'username': username, 'password': password},
        format='json',
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data == {'detail': 'Login yoki parol noto‘g‘ri.'}
    assert username not in response.content.decode()
    assert password not in response.content.decode()
    assert username not in caplog.text
    assert password not in caplog.text
    assert '_auth_user_id' not in csrf_client.session


@pytest.mark.django_db
def test_login_serializer_rejects_missing_fields(csrf_client):
    csrf_token = csrf_client.get('/api/v1/auth/csrf/').data['csrfToken']

    response = csrf_client.post(
        '/api/v1/auth/login/',
        {},
        format='json',
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.data) == {'username', 'password'}


@pytest.mark.django_db
def test_current_user_profile_only_reads_and_updates_own_allowed_fields(login_user):
    client = APIClient()
    client.force_authenticate(user=login_user)

    profile_response = client.get('/api/v1/me/')
    patch_response = client.patch(
        '/api/v1/me/',
        {'first_name': 'Sintetik', 'preferred_language': 'uz'},
        format='json',
    )

    assert profile_response.status_code == status.HTTP_200_OK
    assert profile_response.data == {
        'id': login_user.pk,
        'username': 'login-pupil',
        'first_name': '',
        'last_name': '',
        'role': User.Role.PUPIL,
        'preferred_language': '',
    }
    assert patch_response.status_code == status.HTTP_200_OK
    login_user.refresh_from_db()
    assert login_user.first_name == 'Sintetik'
    assert login_user.preferred_language == 'uz'
    assert login_user.role == User.Role.PUPIL
    assert not login_user.is_staff


@pytest.mark.django_db
@pytest.mark.parametrize(
    'forbidden_data',
    [
        {'role': User.Role.TEACHER},
        {'is_staff': True},
        {'username': 'different-user'},
        {'enrollment': 123},
    ],
)
def test_current_user_profile_rejects_forbidden_fields(login_user, forbidden_data):
    client = APIClient()
    client.force_authenticate(user=login_user)

    response = client.patch(
        '/api/v1/me/',
        forbidden_data,
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.data) == set(forbidden_data)
    login_user.refresh_from_db()
    assert login_user.role == User.Role.PUPIL
    assert login_user.username == 'login-pupil'
    assert not login_user.is_staff


@pytest.mark.django_db
def test_current_user_profile_is_private():
    client = APIClient()

    assert client.get('/api/v1/me/').status_code == status.HTTP_403_FORBIDDEN
    assert client.patch(
        '/api/v1/me/',
        {'first_name': 'Nope'},
        format='json',
    ).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_current_user_profile_rejects_put(login_user):
    client = APIClient()
    client.force_authenticate(user=login_user)

    response = client.put(
        '/api/v1/me/',
        {'first_name': 'Not allowed'},
        format='json',
    )

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    login_user.refresh_from_db()
    assert login_user.first_name == ''


@pytest.mark.django_db
def test_logout_requires_csrf_and_revokes_session_for_private_course_api(
    csrf_client,
    course_api_data,
):
    pupil = course_api_data['pupil_a']
    csrf_token = csrf_client.get('/api/v1/auth/csrf/').data['csrfToken']
    assert csrf_client.login(
        username=pupil.username,
        password='safe-test-password',
    )

    csrf_rejected = csrf_client.post('/api/v1/auth/logout/', format='json')
    assert csrf_rejected.status_code == status.HTTP_403_FORBIDDEN
    assert csrf_client.get('/api/v1/courses/').status_code == status.HTTP_200_OK

    logout_response = csrf_client.post(
        '/api/v1/auth/logout/',
        format='json',
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert logout_response.status_code == status.HTTP_200_OK
    assert logout_response.data == {'detail': 'Tizimdan chiqildi.'}
    assert '_auth_user_id' not in csrf_client.session
    assert csrf_client.get('/api/v1/courses/').status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('current_password', 'new_password', 'invalid_field'),
    [
        ('wrong-current-password', 'another-strong-password-2026', 'current_password'),
        ('synthetic-valid-password', '123', 'new_password'),
        (
            'synthetic-valid-password',
            'synthetic-valid-password',
            'new_password',
        ),
    ],
)
def test_change_password_rejects_invalid_passwords(
    login_user,
    current_password,
    new_password,
    invalid_field,
):
    client = APIClient()
    client.force_authenticate(user=login_user)

    response = client.post(
        '/api/v1/me/change-password/',
        {
            'current_password': current_password,
            'new_password': new_password,
        },
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert invalid_field in response.data
    login_user.refresh_from_db()
    assert login_user.check_password('synthetic-valid-password')
    assert not login_user.must_change_password


@pytest.mark.django_db
def test_must_change_password_gates_courses_and_rotates_only_current_session(
    csrf_client,
    course_api_data,
):
    pupil = course_api_data['pupil_a']
    pupil.must_change_password = True
    pupil.save(update_fields=['must_change_password'])
    other_client = APIClient()
    assert csrf_client.login(
        username=pupil.username,
        password='safe-test-password',
    )
    assert other_client.login(
        username=pupil.username,
        password='safe-test-password',
    )
    old_current_session_key = csrf_client.session.session_key
    csrf_token = csrf_client.get('/api/v1/auth/csrf/').data['csrfToken']

    assert csrf_client.get('/api/v1/courses/').status_code == status.HTTP_403_FORBIDDEN
    course_id = course_api_data['course_a'].pk
    assert (
        csrf_client.get(f'/api/v1/courses/{course_id}/').status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert csrf_client.get('/api/v1/me/').status_code == status.HTTP_200_OK

    csrf_rejected = csrf_client.post(
        '/api/v1/me/change-password/',
        {
            'current_password': 'safe-test-password',
            'new_password': 'new-safe-password-2026',
        },
        format='json',
    )
    assert csrf_rejected.status_code == status.HTTP_403_FORBIDDEN

    response = csrf_client.post(
        '/api/v1/me/change-password/',
        {
            'current_password': 'safe-test-password',
            'new_password': 'new-safe-password-2026',
        },
        format='json',
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {'detail': 'Parol muvaffaqiyatli o‘zgartirildi.'}
    pupil.refresh_from_db()
    assert not pupil.must_change_password
    assert pupil.check_password('new-safe-password-2026')
    assert csrf_client.session.session_key != old_current_session_key
    assert csrf_client.get('/api/v1/courses/').status_code == status.HTTP_200_OK

    assert other_client.get('/api/v1/courses/').status_code == status.HTTP_403_FORBIDDEN
    assert '_auth_user_id' not in other_client.session


@pytest.mark.django_db
def test_must_change_password_user_can_logout(csrf_client, login_user):
    login_user.must_change_password = True
    login_user.save(update_fields=['must_change_password'])
    assert csrf_client.login(
        username=login_user.username,
        password='synthetic-valid-password',
    )
    csrf_token = csrf_client.get('/api/v1/auth/csrf/').data['csrfToken']

    response = csrf_client.post(
        '/api/v1/auth/logout/',
        format='json',
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == status.HTTP_200_OK
    assert '_auth_user_id' not in csrf_client.session
