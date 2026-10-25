from datetime import date

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
def test_course_endpoints_reject_anonymous_requests():
    client = APIClient()

    assert client.get('/api/v1/courses/').status_code == status.HTTP_403_FORBIDDEN
    assert client.get('/api/v1/courses/1/').status_code == status.HTTP_403_FORBIDDEN
