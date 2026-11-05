from datetime import date, timedelta
from types import SimpleNamespace
from typing import cast

import secrets
from io import StringIO

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from academics.models import (
    AcademicStatus,
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
from academics.views import (
    IsCoursePupil,
    IsCourseTeacher,
    teacher_course_scope,
)
from learning.models import Topic


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


def grant_manage_academics(user):
    permission = Permission.objects.get(
        content_type__app_label='accounts',
        codename='manage_academics',
    )
    user.user_permissions.add(permission)


@pytest.mark.django_db
def test_course_admin_create_update_requires_manage_academics(course_api_data):
    teacher = cast(User, course_api_data['teacher_a'])
    client = APIClient()
    client.force_authenticate(user=teacher)
    class_a_id = course_api_data['course_a'].school_class_id
    subject = Subject.objects.create(code='ADMIN-SCI', title='Tabiiy fan')
    payload = {'school_class': class_a_id, 'subject': subject.pk}

    denied_create = client.post('/api/v1/admin/courses/', payload, format='json')
    denied_update = client.patch(
        f"/api/v1/admin/courses/{course_api_data['course_a'].pk}/",
        payload,
        format='json',
    )

    assert denied_create.status_code == status.HTTP_403_FORBIDDEN
    assert denied_update.status_code == status.HTTP_403_FORBIDDEN
    assert not CourseOffering.objects.filter(subject=subject).exists()

    operator = User.objects.create_user(
        username='academic-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client.force_authenticate(user=operator)

    created = client.post('/api/v1/admin/courses/', payload, format='json')

    assert created.status_code == status.HTTP_201_CREATED
    course_id = created.data['id']
    updated = client.patch(
        f'/api/v1/admin/courses/{course_id}/',
        {'school_class': course_api_data['course_b'].school_class_id},
        format='json',
    )
    assert updated.status_code == status.HTTP_200_OK
    assert updated.data['school_class'] == course_api_data['course_b'].school_class_id
    assert client.delete(f'/api/v1/admin/courses/{course_id}/').status_code == (
        status.HTTP_405_METHOD_NOT_ALLOWED
    )


@pytest.mark.django_db
@pytest.mark.parametrize('history_kind', ('assignment', 'enrollment', 'topic'))
@pytest.mark.parametrize('parent_field', ('school_class', 'subject'))
def test_course_admin_rejects_parent_change_when_history_exists(
    course_api_data,
    academic_year,
    history_kind,
    parent_field,
):
    course = CourseOffering.objects.create(
        school_class=course_api_data['course_a'].school_class,
        subject=Subject.objects.create(
            code=f'HIST-{history_kind}-{parent_field}',
            title='Tarixli fan',
        ),
    )
    if history_kind == 'assignment':
        assign_teacher(
            course,
            course_api_data['teacher_a'],
            date(2026, 9, 1),
        )
    elif history_kind == 'enrollment':
        Enrollment.objects.create(
            pupil=course_api_data['pupil_a'],
            course=course,
            start_date=date(2026, 9, 1),
        )
    else:
        term = Term.objects.create(
            year=academic_year,
            order=1,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 11, 30),
        )
        Topic.objects.create(
            course=course,
            term=term,
            title='Mavzu',
            objective='Maqsad',
            order=1,
        )

    new_class = SchoolClass.objects.create(
        year=academic_year,
        grade_level=8,
        section='C',
    )
    new_subject = Subject.objects.create(
        code=f'NEW-{history_kind}-{parent_field}',
        title='Yangi fan',
    )
    value = (
        new_class.pk
        if parent_field == 'school_class'
        else new_subject.pk
    )
    operator = User.objects.create_user(
        username=f'operator-{history_kind}-{parent_field}',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)

    response = client.patch(
        f'/api/v1/admin/courses/{course.pk}/',
        {parent_field: value},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    course.refresh_from_db()
    assert course.school_class_id == course_api_data['course_a'].school_class_id
    assert course.subject_id != new_subject.pk


@pytest.mark.django_db
def test_catalog_options_are_scoped_read_only_lists(course_api_data):
    client = APIClient()
    client.force_authenticate(user=cast(User, course_api_data['teacher_a']))
    class_a = course_api_data['course_a'].school_class
    unused_subject = Subject.objects.create(
        code='OPTION-SCI',
        title='Tanlash uchun fan',
    )

    classes = client.get('/api/v1/catalog/classes/')
    subjects = client.get(
        f'/api/v1/catalog/classes/{class_a.pk}/subjects/',
    )

    assert classes.status_code == status.HTTP_200_OK
    assert any(item['id'] == class_a.pk for item in classes.data)
    assert subjects.status_code == status.HTTP_200_OK
    assert set(subjects.data[0]) == {'id', 'code', 'title'}
    assert unused_subject.pk in {item['id'] for item in subjects.data}
    assert course_api_data['course_a'].subject_id not in {
        item['id'] for item in subjects.data
    }
    assert client.post('/api/v1/catalog/classes/', {}, format='json').status_code == (
        status.HTTP_405_METHOD_NOT_ALLOWED
    )
    assert client.get('/api/v1/catalog/classes/999999/subjects/').status_code == (
        status.HTTP_404_NOT_FOUND
    )


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

    replacement_date = timezone.localdate()
    close_teaching_assignment(
        old_assignment,
        replacement_date - timedelta(days=1),
    )
    assign_teacher(course, teacher, replacement_date, can_grade=False)
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

    assert client.get('/api/v1/courses/').status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get('/api/v1/courses/1/').status_code == status.HTTP_401_UNAUTHORIZED


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
def test_login_does_not_require_csrf_token(csrf_client, login_user):
    response = csrf_client.post(
        '/api/v1/auth/login/',
        {'username': login_user.username, 'password': 'synthetic-valid-password'},
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert set(response.data) == {'access', 'refresh'}
    assert '_auth_user_id' not in csrf_client.session


@pytest.mark.django_db
def test_csrf_endpoint_remains_available_but_login_returns_jwts(
    csrf_client,
    login_user,
):
    csrf_response = csrf_client.get('/api/v1/auth/csrf/')
    assert csrf_response.status_code == status.HTTP_200_OK
    assert 'csrftoken' in csrf_client.cookies

    response = csrf_client.post(
        '/api/v1/auth/login/',
        {'username': login_user.username, 'password': 'synthetic-valid-password'},
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert set(response.data) == {'access', 'refresh'}
    assert '_auth_user_id' not in csrf_client.session


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
    response = csrf_client.post(
        '/api/v1/auth/login/',
        {'username': username, 'password': password},
        format='json',
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
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

    assert client.get('/api/v1/me/').status_code == status.HTTP_401_UNAUTHORIZED
    assert client.patch(
        '/api/v1/me/',
        {'first_name': 'Nope'},
        format='json',
    ).status_code == status.HTTP_401_UNAUTHORIZED


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
def test_logout_blacklists_refresh_without_csrf(
    csrf_client,
    course_api_data,
):
    pupil = course_api_data['pupil_a']
    login = csrf_client.post(
        '/api/v1/auth/login/',
        {'username': pupil.username, 'password': 'safe-test-password'},
        format='json',
    )
    assert login.status_code == status.HTTP_200_OK
    csrf_client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
    )

    missing_refresh = csrf_client.post('/api/v1/auth/logout/', {}, format='json')
    assert missing_refresh.status_code == status.HTTP_400_BAD_REQUEST
    assert csrf_client.get('/api/v1/courses/').status_code == status.HTTP_200_OK

    logout_response = csrf_client.post(
        '/api/v1/auth/logout/',
        {'refresh': login.data['refresh']},
        format='json',
    )

    assert logout_response.status_code == status.HTTP_204_NO_CONTENT
    assert csrf_client.get('/api/v1/courses/').status_code == status.HTTP_200_OK
    refresh_response = APIClient().post(
        '/api/v1/auth/token/refresh/',
        {'refresh': login.data['refresh']},
        format='json',
    )
    assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED


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
def test_must_change_password_gates_courses_and_revokes_old_jwts(
    csrf_client,
    course_api_data,
):
    pupil = course_api_data['pupil_a']
    pupil.must_change_password = True
    pupil.save(update_fields=['must_change_password'])
    clients = [csrf_client, APIClient(enforce_csrf_checks=True)]
    for client in clients:
        login = client.post(
            '/api/v1/auth/login/',
            {'username': pupil.username, 'password': 'safe-test-password'},
            format='json',
        )
        assert login.status_code == status.HTTP_200_OK
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
        )

    assert csrf_client.get('/api/v1/courses/').status_code == status.HTTP_403_FORBIDDEN
    course_id = course_api_data['course_a'].pk
    assert (
        csrf_client.get(f'/api/v1/courses/{course_id}/').status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert csrf_client.get('/api/v1/me/').status_code == status.HTTP_200_OK

    response = csrf_client.post(
        '/api/v1/me/change-password/',
        {
            'current_password': 'safe-test-password',
            'new_password': 'new-safe-password-2026',
        },
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {'detail': 'Parol muvaffaqiyatli o‘zgartirildi.'}
    pupil.refresh_from_db()
    assert not pupil.must_change_password
    assert pupil.check_password('new-safe-password-2026')
    for client in clients:
        assert client.get('/api/v1/courses/').status_code == status.HTTP_401_UNAUTHORIZED

    new_login = APIClient().post(
        '/api/v1/auth/login/',
        {'username': pupil.username, 'password': 'new-safe-password-2026'},
        format='json',
    )
    assert new_login.status_code == status.HTTP_200_OK
    csrf_client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {new_login.data['access']}"
    )
    assert csrf_client.get('/api/v1/courses/').status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_must_change_password_user_can_logout(csrf_client, login_user):
    login_user.must_change_password = True
    login_user.save(update_fields=['must_change_password'])
    login = csrf_client.post(
        '/api/v1/auth/login/',
        {'username': login_user.username, 'password': 'synthetic-valid-password'},
        format='json',
    )
    assert login.status_code == status.HTTP_200_OK
    csrf_client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
    )

    response = csrf_client.post(
        '/api/v1/auth/logout/',
        {'refresh': login.data['refresh']},
        format='json',
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
def test_academic_year_aggregate_permissions_and_term_closure(academic_year):
    teacher = User.objects.create_user(
        username='year-api-teacher',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    pupil = User.objects.create_user(
        username='year-api-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    operator = User.objects.create_user(
        username='year-api-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    year_payload = {
        'label': '2028–2029',
        'start_date': '2028-09-01',
        'end_date': '2029-06-30',
        'terms': [
            {
                'order': 1,
                'start_date': '2028-09-01',
                'end_date': '2028-12-31',
            },
            {
                'order': 2,
                'start_date': '2029-01-01',
                'end_date': '2029-06-30',
            },
        ],
    }

    teacher_client = APIClient()
    teacher_client.force_authenticate(user=teacher)
    pupil_client = APIClient()
    pupil_client.force_authenticate(user=pupil)
    assert teacher_client.get('/api/v1/catalog/academic-years/').status_code == (
        status.HTTP_200_OK
    )
    assert pupil_client.get('/api/v1/catalog/academic-years/').status_code == (
        status.HTTP_200_OK
    )
    assert teacher_client.post(
        '/api/v1/admin/academic-years/',
        year_payload,
        format='json',
    ).status_code == status.HTTP_403_FORBIDDEN
    assert teacher_client.patch(
        f'/api/v1/admin/academic-years/{academic_year.pk}/',
        {'status': AcademicStatus.CLOSED},
        format='json',
    ).status_code == status.HTTP_403_FORBIDDEN

    operator_client = APIClient()
    operator_client.force_authenticate(user=operator)
    created = operator_client.post(
        '/api/v1/admin/academic-years/',
        year_payload,
        format='json',
    )
    assert created.status_code == status.HTTP_201_CREATED
    year_id = created.data['id']
    terms = created.data['terms']
    assert len(terms) == 2
    assert all(term['status'] == AcademicStatus.UPCOMING for term in terms)

    update = operator_client.patch(
        f'/api/v1/admin/academic-years/{year_id}/',
        {
            'status': AcademicStatus.CLOSED,
            'terms': [
                {
                    'id': terms[0]['id'],
                    'order': 2,
                    'start_date': '2028-09-01',
                    'end_date': '2028-12-31',
                    'status': AcademicStatus.CLOSED,
                },
                {
                    'id': terms[1]['id'],
                    'order': 1,
                    'start_date': '2029-01-01',
                    'end_date': '2029-06-30',
                    'status': AcademicStatus.CLOSED,
                },
            ],
        },
        format='json',
    )
    assert update.status_code == status.HTTP_200_OK
    assert update.data['status'] == AcademicStatus.CLOSED
    updated_orders = {
        term['id']: term['order']
        for term in update.data['terms']
    }
    assert updated_orders[terms[0]['id']] == 2
    assert updated_orders[terms[1]['id']] == 1
    assert all(term['status'] == AcademicStatus.CLOSED for term in update.data['terms'])
    assert operator_client.delete(
        f'/api/v1/admin/academic-years/{year_id}/',
    ).status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert AcademicYear.objects.filter(pk=year_id).exists()


@pytest.mark.django_db
def test_academic_year_aggregate_rejects_duplicate_or_out_of_bounds_terms(
    academic_year,
):
    operator = User.objects.create_user(
        username='year-invalid-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)

    duplicate_orders = {
        'label': 'Invalid duplicate',
        'start_date': '2028-09-01',
        'end_date': '2029-06-30',
        'terms': [
            {'order': 1, 'start_date': '2028-09-01', 'end_date': '2028-12-31'},
            {'order': 1, 'start_date': '2029-01-01', 'end_date': '2029-06-30'},
        ],
    }
    out_of_bounds = {
        'label': 'Invalid range',
        'start_date': '2028-09-01',
        'end_date': '2029-06-30',
        'terms': [
            {'order': 1, 'start_date': '2028-08-31', 'end_date': '2028-12-31'},
        ],
    }
    duplicate_response = client.post(
        '/api/v1/admin/academic-years/',
        duplicate_orders,
        format='json',
    )
    bounds_response = client.post(
        '/api/v1/admin/academic-years/',
        out_of_bounds,
        format='json',
    )

    assert duplicate_response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'terms' in duplicate_response.data
    assert bounds_response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'terms' in bounds_response.data
    assert AcademicYear.objects.count() == 1


@pytest.mark.django_db
def test_referenced_term_cannot_be_deleted(course_api_data):
    year = course_api_data['course_a'].school_class.year
    term = Term.objects.create(
        year=year,
        order=1,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 11, 30),
    )
    Topic.objects.create(
        course=course_api_data['course_a'],
        term=term,
        title='Historical topic',
        objective='Keep published history',
        order=1,
        state=Topic.State.PUBLISHED,
    )

    with pytest.raises(ProtectedError):
        term.delete()

    assert Term.objects.filter(pk=term.pk).exists()
    assert Topic.objects.filter(term=term, state=Topic.State.PUBLISHED).exists()


@pytest.mark.django_db
def test_school_class_catalog_requires_operator_for_writes(course_api_data):
    teacher = course_api_data['teacher_a']
    pupil = course_api_data['pupil_a']
    class_payload = {
        'year': course_api_data['course_a'].school_class.year_id,
        'grade_level': 8,
        'section': 'C',
    }

    for user in (teacher, pupil):
        client = APIClient()
        client.force_authenticate(user=user)
        assert client.get('/api/v1/admin/classes/').status_code == (
            status.HTTP_200_OK
        )
        assert client.post(
            '/api/v1/admin/classes/',
            class_payload,
            format='json',
        ).status_code == status.HTTP_403_FORBIDDEN
        assert client.patch(
            f"/api/v1/admin/classes/{course_api_data['course_a'].school_class_id}/",
            {'section': 'Z'},
            format='json',
        ).status_code == status.HTTP_403_FORBIDDEN

    operator = User.objects.create_user(
        username='class-catalog-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)

    created = client.post(
        '/api/v1/admin/classes/',
        class_payload,
        format='json',
    )
    assert created.status_code == status.HTTP_201_CREATED
    assert created.data['year'] == class_payload['year']
    assert created.data['grade_level'] == 8
    assert created.data['section'] == 'C'

    updated = client.patch(
        f"/api/v1/admin/classes/{created.data['id']}/",
        {'section': 'D'},
        format='json',
    )
    assert updated.status_code == status.HTTP_200_OK
    assert updated.data['section'] == 'D'


@pytest.mark.django_db
def test_school_class_catalog_rejects_duplicate_and_preserves_year_identity(
    course_api_data,
):
    historic_class = course_api_data['course_a'].school_class
    next_year = AcademicYear.objects.create(
        start_date=date(2027, 9, 1),
        end_date=date(2028, 6, 30),
        label='2027–2028',
    )
    operator = User.objects.create_user(
        username='class-identity-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)

    duplicate = client.post(
        '/api/v1/admin/classes/',
        {
            'year': historic_class.year_id,
            'grade_level': historic_class.grade_level,
            'section': historic_class.section,
        },
        format='json',
    )
    assert duplicate.status_code == status.HTTP_400_BAD_REQUEST
    assert 'section' in duplicate.data
    invalid_grade = client.post(
        '/api/v1/admin/classes/',
        {
            'year': historic_class.year_id,
            'grade_level': 12,
            'section': 'C',
        },
        format='json',
    )
    assert invalid_grade.status_code == status.HTTP_400_BAD_REQUEST
    assert 'grade_level' in invalid_grade.data

    moved = client.patch(
        f'/api/v1/admin/classes/{historic_class.pk}/',
        {
            'year': next_year.pk,
            'grade_level': 8,
            'section': historic_class.section,
        },
        format='json',
    )
    renamed = client.patch(
        f'/api/v1/admin/classes/{historic_class.pk}/',
        {'grade_level': 8},
        format='json',
    )
    assert moved.status_code == status.HTTP_400_BAD_REQUEST
    assert 'year' in moved.data
    assert renamed.status_code == status.HTTP_400_BAD_REQUEST
    assert 'grade_level' in renamed.data

    historic_class.refresh_from_db()
    assert historic_class.year_id != next_year.pk
    assert historic_class.grade_level == 7
    assert historic_class.section == 'A'
    assert SchoolClass.objects.filter(
        year=next_year,
        grade_level=8,
        section='A',
    ).count() == 0


@pytest.mark.django_db
def test_subject_catalog_requires_operator_and_rejects_duplicate_code(
    course_api_data,
):
    teacher = course_api_data['teacher_a']
    pupil = course_api_data['pupil_a']
    for user in (teacher, pupil):
        client = APIClient()
        client.force_authenticate(user=user)
        assert client.get('/api/v1/catalog/subjects/').status_code == (
            status.HTTP_200_OK
        )
        assert client.post(
            '/api/v1/admin/subjects/',
            {'code': 'NEW-SUBJECT', 'title': 'Yangi fan'},
            format='json',
        ).status_code == status.HTTP_403_FORBIDDEN
        assert client.patch(
            f"/api/v1/admin/subjects/{course_api_data['course_a'].subject_id}/",
            {'title': 'Yangilangan fan'},
            format='json',
        ).status_code == status.HTTP_403_FORBIDDEN

    operator = User.objects.create_user(
        username='subject-catalog-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)

    created = client.post(
        '/api/v1/admin/subjects/',
        {'code': 'NEW-SUBJECT', 'title': 'Yangi fan'},
        format='json',
    )
    assert created.status_code == status.HTTP_201_CREATED
    assert created.data['archived_at'] is None

    duplicate = client.post(
        '/api/v1/admin/subjects/',
        {'code': 'NEW-SUBJECT', 'title': 'Takror fan'},
        format='json',
    )
    assert duplicate.status_code == status.HTTP_400_BAD_REQUEST
    assert 'code' in duplicate.data

    updated = client.patch(
        f"/api/v1/admin/subjects/{created.data['id']}/",
        {'title': 'Tahrirlangan fan'},
        format='json',
    )
    assert updated.status_code == status.HTTP_200_OK
    assert updated.data['title'] == 'Tahrirlangan fan'


@pytest.mark.django_db
def test_archiving_subject_preserves_course_history_and_hides_it_from_options(
    course_api_data,
):
    subject = course_api_data['course_a'].subject
    course = course_api_data['course_a']
    assignment_id = course.teaching_assignments.get().pk
    term = Term.objects.create(
        year=course.school_class.year,
        order=1,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 11, 30),
    )
    historical_topic = Topic.objects.create(
        course=course,
        term=term,
        title='Historical course topic',
        objective='Keep associated history.',
        order=1,
        state=Topic.State.PUBLISHED,
        published_at=timezone.now(),
    )
    other_class = SchoolClass.objects.create(
        year=course.school_class.year,
        grade_level=9,
        section='C',
    )
    operator = User.objects.create_user(
        username='subject-archive-operator',
        password='safe-test-password',
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)

    archived = client.post(
        f'/api/v1/admin/subjects/{subject.pk}/archive/',
        {},
        format='json',
    )

    assert archived.status_code == status.HTTP_200_OK
    assert archived.data['archived_at'] is not None
    subject.refresh_from_db()
    course.refresh_from_db()
    assert subject.archived_at is not None
    assert CourseOffering.objects.filter(pk=course.pk, subject=subject).exists()
    assert TeachingAssignment.objects.filter(pk=assignment_id, course=course).exists()
    assert Topic.objects.filter(pk=historical_topic.pk, course=course).exists()
    assert not client.get('/api/v1/catalog/subjects/').data
    available = client.get(
        f'/api/v1/catalog/classes/{other_class.pk}/subjects/',
    )
    assert available.status_code == status.HTTP_200_OK
    assert subject.pk not in {item['id'] for item in available.data}
    assert client.delete(
        f'/api/v1/admin/subjects/{subject.pk}/',
    ).status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    create_course = client.post(
        '/api/v1/admin/courses/',
        {'school_class': other_class.pk, 'subject': subject.pk},
        format='json',
    )
    assert create_course.status_code == status.HTTP_400_BAD_REQUEST
    assert 'subject' in create_course.data


@pytest.mark.django_db(transaction=True)
def test_catalog_detail_reads_do_not_lock_rows_outside_a_transaction(
    course_api_data,
):
    client = APIClient()
    client.force_authenticate(user=course_api_data['teacher_a'])

    class_response = client.get(
        f"/api/v1/admin/classes/{course_api_data['course_a'].school_class_id}/",
    )
    subject_response = client.get(
        f"/api/v1/admin/subjects/{course_api_data['course_a'].subject_id}/",
    )

    assert class_response.status_code == status.HTTP_200_OK
    assert subject_response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_teacher_assignment_api_limits_teacher_profile_and_rejects_self_grant(
    course_api_data,
):
    teacher = course_api_data["teacher_a"]
    pupil = course_api_data["pupil_a"]
    operator = User.objects.create_user(
        username="assignment-operator",
        first_name="Operator",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    operator_client = APIClient()
    operator_client.force_authenticate(user=operator)

    teacher_list = operator_client.get("/api/v1/catalog/teachers/")
    assert teacher_list.status_code == status.HTTP_200_OK
    assert teacher.pk in {item["id"] for item in teacher_list.data}
    assert pupil.pk not in {item["id"] for item in teacher_list.data}
    assert set(teacher_list.data[0]) == {
        "id",
        "username",
        "first_name",
        "last_name",
    }

    teacher_client = APIClient()
    teacher_client.force_authenticate(user=teacher)
    self_grant = teacher_client.post(
        "/api/v1/admin/teaching-assignments/",
        {
            "course": course_api_data["course_a"].pk,
            "teacher": teacher.pk,
            "start_date": timezone.localdate().isoformat(),
            "can_grade": True,
        },
        format="json",
    )
    assert self_grant.status_code == status.HTTP_403_FORBIDDEN

    pupil_assignment = operator_client.post(
        "/api/v1/admin/teaching-assignments/",
        {
            "course": course_api_data["course_a"].pk,
            "teacher": pupil.pk,
            "start_date": timezone.localdate().isoformat(),
            "can_grade": True,
        },
        format="json",
    )
    assert pupil_assignment.status_code == status.HTTP_400_BAD_REQUEST
    assert "teacher" in pupil_assignment.data
    assert not TeachingAssignment.objects.filter(teacher=pupil).exists()

    operator_self_grant = operator_client.post(
        "/api/v1/admin/teaching-assignments/",
        {
            "course": course_api_data["course_a"].pk,
            "teacher": operator.pk,
            "start_date": timezone.localdate().isoformat(),
            "can_grade": True,
        },
        format="json",
    )
    assert operator_self_grant.status_code == status.HTTP_400_BAD_REQUEST
    assert "teacher" in operator_self_grant.data

    operator_self_assignment = operator_client.post(
        "/api/v1/admin/teaching-assignments/",
        {
            "course": course_api_data["course_a"].pk,
            "teacher": operator.pk,
            "start_date": timezone.localdate().isoformat(),
            "can_grade": False,
        },
        format="json",
    )
    assert operator_self_assignment.status_code == status.HTTP_400_BAD_REQUEST
    assert "teacher" in operator_self_assignment.data
    assert not TeachingAssignment.objects.filter(
        course=course_api_data["course_a"],
        teacher=operator,
    ).exists()


@pytest.mark.django_db
def test_superuser_can_manage_teacher_assignments_regardless_of_business_role(
    course_api_data,
):
    superuser = User.objects.create_superuser(
        username="academic-superuser",
        password="synthetic-superuser-password",
        role=User.Role.PUPIL,
    )
    client = APIClient()
    client.force_authenticate(user=superuser)

    response = client.get("/api/v1/catalog/teachers/")

    assert response.status_code == status.HTTP_200_OK
    assert course_api_data["teacher_a"].pk in {
        item["id"] for item in response.data
    }


@pytest.mark.django_db
def test_replacing_course_teacher_closes_interval_and_preserves_history(
    course_api_data,
):
    course = course_api_data["course_a"]
    previous_teacher = course_api_data["teacher_a"]
    new_teacher = course_api_data["teacher_b"]
    old_assignment = TeachingAssignment.objects.get(
        course=course,
        teacher=previous_teacher,
    )
    old_assignment.can_grade = True
    old_assignment.save(update_fields=["can_grade"])
    term = Term.objects.create(
        year=course.school_class.year,
        order=1,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
    )
    published_topic = Topic.objects.create(
        course=course,
        term=term,
        title="Published history",
        objective="Keep this published record.",
        order=1,
        state=Topic.State.PUBLISHED,
        published_at=timezone.now(),
    )
    operator = User.objects.create_user(
        username="assignment-replacer",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)
    start_date = timezone.localdate()

    response = client.post(
        "/api/v1/admin/teaching-assignments/",
        {
            "course": course.pk,
            "teacher": new_teacher.pk,
            "start_date": start_date.isoformat(),
            "can_grade": False,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["teacher"] == new_teacher.pk
    assert response.data["can_grade"] is False
    old_assignment.refresh_from_db()
    published_topic.refresh_from_db()
    assert old_assignment.end_date == start_date - timedelta(days=1)
    assert old_assignment.teacher_id == previous_teacher.pk
    assert old_assignment.can_grade is True
    assert published_topic.state == Topic.State.PUBLISHED
    assert published_topic.published_at is not None
    assert TeachingAssignment.objects.filter(course=course).count() == 2
    assert not teacher_course_scope(
        previous_teacher,
        active_only=True,
        can_grade=True,
    ).filter(pk=course.pk).exists()


@pytest.mark.django_db
def test_closing_assignment_ends_grading_scope_and_cannot_reclose(
    course_api_data,
):
    course = course_api_data["course_a"]
    teacher = course_api_data["teacher_a"]
    assignment = TeachingAssignment.objects.get(
        course=course,
        teacher=teacher,
    )
    assignment.can_grade = True
    assignment.save(update_fields=["can_grade"])
    operator = User.objects.create_user(
        username="assignment-closer",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)
    end_date = timezone.localdate() - timedelta(days=1)

    response = client.post(
        f"/api/v1/admin/teaching-assignments/{assignment.pk}/close/",
        {"end_date": end_date.isoformat()},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["end_date"] == end_date.isoformat()
    assert response.data["can_grade"] is True
    assert not teacher_course_scope(
        teacher,
        active_only=True,
        can_grade=True,
    ).filter(pk=course.pk).exists()
    repeated = client.post(
        f"/api/v1/admin/teaching-assignments/{assignment.pk}/close/",
        {"end_date": end_date.isoformat()},
        format="json",
    )
    assert repeated.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_future_assignment_is_not_yet_in_teacher_write_or_grading_scope(
    course_api_data,
):
    teacher = User.objects.create_user(
        username="future-assignment-teacher",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    course = course_api_data["course_a"]
    assign_teacher(
        course,
        teacher,
        timezone.localdate() + timedelta(days=1),
        can_grade=True,
    )

    assert teacher_course_scope(teacher).filter(pk=course.pk).exists()
    assert not teacher_course_scope(
        teacher,
        active_only=True,
        can_grade=True,
    ).filter(pk=course.pk).exists()
    request = SimpleNamespace(user=teacher, method="PATCH")
    assert not IsCourseTeacher().has_object_permission(request, None, course)


@pytest.mark.django_db
def test_class_membership_api_transfers_pupil_and_preserves_enrollment_history(
    course_api_data,
):
    pupil = course_api_data["pupil_a"]
    old_course = course_api_data["course_a"]
    new_course = course_api_data["course_b"]
    old_membership = ClassMembership.objects.get(pupil=pupil)
    old_enrollment = Enrollment.objects.get(pupil=pupil, course=old_course)
    operator = User.objects.create_user(
        username="membership-operator",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)
    effective_date = timezone.localdate()

    response = client.post(
        "/api/v1/admin/class-memberships/",
        {
            "pupil": pupil.pk,
            "school_class": new_course.school_class_id,
            "start_date": effective_date.isoformat(),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["pupil"] == pupil.pk
    assert response.data["school_class"] == new_course.school_class_id
    assert response.data["start_date"] == effective_date.isoformat()
    assert response.data["status"] == MembershipStatus.ACTIVE
    old_membership.refresh_from_db()
    old_enrollment.refresh_from_db()
    assert old_membership.status == MembershipStatus.CLOSED
    assert old_membership.end_date == effective_date - timedelta(days=1)
    assert old_enrollment.status == MembershipStatus.CLOSED
    assert old_enrollment.end_date == effective_date - timedelta(days=1)
    assert ClassMembership.objects.filter(pupil=pupil).count() == 2
    assert Enrollment.objects.filter(pupil=pupil, course=old_course).count() == 1
    assert Enrollment.objects.get(pupil=pupil, course=new_course).status == (
        MembershipStatus.ACTIVE
    )
    assert ClassMembership.objects.filter(
        pupil=pupil,
        status=MembershipStatus.ACTIVE,
    ).count() == 1


@pytest.mark.django_db
def test_class_membership_api_rejects_non_operator_and_non_pupil_targets(
    course_api_data,
):
    client = APIClient()
    client.force_authenticate(user=course_api_data["teacher_a"])
    payload = {
        "pupil": course_api_data["pupil_a"].pk,
        "school_class": course_api_data["course_b"].school_class_id,
        "start_date": timezone.localdate().isoformat(),
    }

    forbidden = client.post(
        "/api/v1/admin/class-memberships/",
        payload,
        format="json",
    )
    assert forbidden.status_code == status.HTTP_403_FORBIDDEN
    assert not ClassMembership.objects.filter(
        pupil=course_api_data["pupil_a"],
        school_class=course_api_data["course_b"].school_class,
        status=MembershipStatus.ACTIVE,
    ).exists()

    operator = User.objects.create_user(
        username="membership-role-operator",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client.force_authenticate(user=operator)
    payload["pupil"] = course_api_data["teacher_a"].pk

    invalid_target = client.post(
        "/api/v1/admin/class-memberships/",
        payload,
        format="json",
    )

    assert invalid_target.status_code == status.HTTP_400_BAD_REQUEST
    assert "pupil" in invalid_target.data


@pytest.mark.django_db
def test_class_membership_api_rejects_overlapping_effective_date(
    course_api_data,
):
    pupil = course_api_data["pupil_a"]
    operator = User.objects.create_user(
        username="membership-date-operator",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)

    response = client.post(
        "/api/v1/admin/class-memberships/",
        {
            "pupil": pupil.pk,
            "school_class": course_api_data["course_b"].school_class_id,
            "start_date": date(2026, 8, 31).isoformat(),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert ClassMembership.objects.filter(
        pupil=pupil,
        status=MembershipStatus.ACTIVE,
    ).count() == 1
    assert Enrollment.objects.filter(
        pupil=pupil,
        status=MembershipStatus.ACTIVE,
    ).count() == 1


@pytest.mark.django_db
def test_class_membership_api_rolls_back_partial_transfer(
    course_api_data,
    monkeypatch,
):
    pupil = course_api_data["pupil_a"]
    old_membership = ClassMembership.objects.get(pupil=pupil)
    old_enrollment = Enrollment.objects.get(
        pupil=pupil,
        course=course_api_data["course_a"],
    )
    operator = User.objects.create_user(
        username="membership-rollback-operator",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)

    def fail_save(self, *args, **kwargs):
        raise RuntimeError("Sintetik enrollment yozuv xatosi")

    monkeypatch.setattr(Enrollment, "save", fail_save)

    with pytest.raises(RuntimeError, match="Sintetik enrollment yozuv xatosi"):
        client.post(
            "/api/v1/admin/class-memberships/",
            {
                "pupil": pupil.pk,
                "school_class": course_api_data["course_b"].school_class_id,
                "start_date": timezone.localdate().isoformat(),
            },
            format="json",
        )

    old_membership.refresh_from_db()
    old_enrollment.refresh_from_db()
    assert old_membership.status == MembershipStatus.ACTIVE
    assert old_membership.end_date is None
    assert old_enrollment.status == MembershipStatus.ACTIVE
    assert old_enrollment.end_date is None
    assert ClassMembership.objects.filter(pupil=pupil).count() == 1
    assert Enrollment.objects.filter(pupil=pupil).count() == 1


@pytest.mark.django_db
def test_course_pupil_roster_is_scoped_and_returns_minimal_pupil_data(
    course_api_data,
):
    client = APIClient()
    client.force_authenticate(user=course_api_data["teacher_a"])

    own_roster = client.get(
        f'/api/v1/courses/{course_api_data["course_a"].pk}/pupils/',
    )
    other_roster = client.get(
        f'/api/v1/courses/{course_api_data["course_b"].pk}/pupils/',
    )

    assert own_roster.status_code == status.HTTP_200_OK
    assert own_roster.data == [
        {
            "pupil_id": course_api_data["pupil_a"].pk,
            "first_name": "",
            "last_name": "",
        }
    ]
    assert other_roster.status_code == status.HTTP_404_NOT_FOUND
    assert set(own_roster.data[0]) == {"pupil_id", "first_name", "last_name"}

    client.force_authenticate(user=course_api_data["pupil_a"])
    denied = client.get(
        f'/api/v1/courses/{course_api_data["course_a"].pk}/pupils/',
    )
    assert denied.status_code == status.HTTP_404_NOT_FOUND

    operator = User.objects.create_user(
        username="roster-operator",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client.force_authenticate(user=operator)
    operator_roster = client.get(
        f'/api/v1/courses/{course_api_data["course_b"].pk}/pupils/',
    )
    assert operator_roster.status_code == status.HTTP_200_OK
    assert operator_roster.data[0]["pupil_id"] == course_api_data["pupil_b"].pk


@pytest.mark.django_db
def test_course_enrollment_sync_is_idempotent_and_preserves_history(
    course_api_data,
):
    class_a = course_api_data["course_a"].school_class
    today = timezone.localdate()
    new_pupil = User.objects.create_user(
        username="sync-active-pupil",
        password="safe-test-password",
        role=User.Role.PUPIL,
    )
    enroll_pupil_in_class(new_pupil, class_a, today - timedelta(days=5))

    inactive_pupil = User.objects.create_user(
        username="sync-inactive-pupil",
        password="safe-test-password",
        role=User.Role.PUPIL,
    )
    ClassMembership.objects.create(
        pupil=inactive_pupil,
        school_class=class_a,
        start_date=today - timedelta(days=10),
        end_date=today - timedelta(days=1),
        status=MembershipStatus.CLOSED,
    )

    new_course = CourseOffering.objects.create(
        school_class=class_a,
        subject=Subject.objects.create(
            code="SYNC-COURSE",
            title="Sinxronlash kursi",
        ),
    )
    historical_enrollment = Enrollment.objects.create(
        pupil=course_api_data["pupil_a"],
        course=new_course,
        start_date=today - timedelta(days=10),
        end_date=today - timedelta(days=1),
        status=MembershipStatus.CLOSED,
    )
    original_history = (
        historical_enrollment.start_date,
        historical_enrollment.end_date,
        historical_enrollment.status,
    )
    operator = User.objects.create_user(
        username="course-sync-operator",
        password="safe-test-password",
        role=User.Role.TEACHER,
    )
    grant_manage_academics(operator)
    client = APIClient()
    client.force_authenticate(user=operator)
    url = f"/api/v1/admin/courses/{new_course.pk}/sync-enrollments/"

    first_sync = client.post(url, {}, format="json")
    second_sync = client.post(url, {}, format="json")

    assert first_sync.status_code == status.HTTP_200_OK
    assert first_sync.data == {"course_id": new_course.pk, "created_count": 1}
    assert second_sync.status_code == status.HTTP_200_OK
    assert second_sync.data == {"course_id": new_course.pk, "created_count": 0}
    created_enrollment = Enrollment.objects.get(
        pupil=new_pupil,
        course=new_course,
    )
    assert created_enrollment.status == MembershipStatus.ACTIVE
    assert created_enrollment.start_date == today - timedelta(days=5)
    assert Enrollment.objects.filter(
        pupil=new_pupil,
        course=new_course,
    ).count() == 1
    historical_enrollment.refresh_from_db()
    assert (
        historical_enrollment.start_date,
        historical_enrollment.end_date,
        historical_enrollment.status,
    ) == original_history
    assert not Enrollment.objects.filter(
        pupil=inactive_pupil,
        course=new_course,
    ).exists()
    assert ClassMembership.objects.get(pupil=inactive_pupil).status == (
        MembershipStatus.CLOSED
    )


@pytest.mark.django_db
def test_course_enrollment_sync_requires_academic_operator(course_api_data):
    client = APIClient()
    client.force_authenticate(user=course_api_data["teacher_a"])

    response = client.post(
        f'/api/v1/admin/courses/{course_api_data["course_a"].pk}/sync-enrollments/',
        {},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
