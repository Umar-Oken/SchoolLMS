from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from accounts.models import User
from academics.models import (
    ClassMembership,
    CourseOffering,
    Enrollment,
    MembershipStatus,
    SchoolClass,
    TeachingAssignment,
)


def _validate_new_assignment(course, teacher):
    course = CourseOffering.objects.select_for_update().get(pk=course.pk)
    teacher = User.objects.select_for_update().get(pk=teacher.pk)
    if teacher.role != User.Role.TEACHER:
        raise ValidationError({'teacher': 'Faqat o‘qituvchi rolidagi foydalanuvchi biriktiriladi.'})
    if not teacher.is_active:
        raise ValidationError({'teacher': 'Faol bo‘lmagan o‘qituvchini biriktirib bo‘lmaydi.'})
    if course.archived_at is not None:
        raise ValidationError({'course': 'Arxivlangan kursga yangi o‘qituvchi biriktirib bo‘lmaydi.'})


def _create_assignment(course, teacher, start_date, can_grade):
    _validate_new_assignment(course, teacher)
    assignment = TeachingAssignment(
        course=course,
        teacher=teacher,
        start_date=start_date,
        can_grade=can_grade,
    )
    assignment.full_clean()
    assignment.save()
    return assignment


@transaction.atomic
def assign_teacher(course, teacher, start_date, can_grade=False):
    return _create_assignment(course, teacher, start_date, can_grade)


@transaction.atomic
def replace_course_teacher(course, teacher, start_date, can_grade=False):
    _validate_new_assignment(course, teacher)
    new_end_date = start_date - timedelta(days=1)

    for previous in TeachingAssignment.objects.select_for_update().filter(
        course=course,
        end_date__isnull=True,
    ):
        previous.end_date = new_end_date
        previous.full_clean()
        previous.save(update_fields=['end_date'])

    return _create_assignment(course, teacher, start_date, can_grade)


@transaction.atomic
def close_teaching_assignment(assignment, end_date):
    if assignment.end_date is not None:
        raise ValidationError('Yopilgan biriktirishning sanasini qayta o‘zgartirib bo‘lmaydi.')

    assignment.end_date = end_date
    assignment.full_clean()
    assignment.save(update_fields=['end_date'])
    return assignment


@transaction.atomic
def enroll_pupil_in_class(pupil, school_class, start_date):
    if type(start_date) is not date:
        raise ValidationError({'start_date': 'Boshlanish sanasi sana ko‘rinishida berilishi kerak.'})
    pupil = User.objects.select_for_update().get(pk=pupil.pk)
    if pupil.role != User.Role.PUPIL:
        raise ValidationError({'pupil': 'Faqat o‘quvchi rolidagi foydalanuvchi sinfga biriktiriladi.'})

    school_class = SchoolClass.objects.select_for_update().get(pk=school_class.pk)
    active_membership = (
        ClassMembership.objects.select_for_update()
        .filter(pupil=pupil, status=MembershipStatus.ACTIVE)
        .first()
    )
    if active_membership and active_membership.school_class_id == school_class.pk:
        raise ValidationError({'school_class': 'O‘quvchi bu sinfga allaqachon biriktirilgan.'})

    previous_memberships = ClassMembership.objects.filter(pupil=pupil)
    if active_membership:
        previous_memberships = previous_memberships.exclude(pk=active_membership.pk)
        if start_date <= active_membership.start_date:
            raise ValidationError('Sinf almashish sanasi amaldagi a’zolik boshlangan sanadan keyin bo‘lishi kerak.')
    if previous_memberships.filter(
        Q(end_date__isnull=True) | Q(end_date__gte=start_date)
    ).exists():
        raise ValidationError('Sana avvalgi sinf a’zoligi davri bilan ustma-ust tushadi.')

    if Enrollment.objects.filter(
        pupil=pupil,
        status=MembershipStatus.ACTIVE,
        start_date__gte=start_date,
    ).exists():
        raise ValidationError('Sana faol fan yozuvi boshlangan sanadan keyin bo‘lishi kerak.')

    courses = list(
        CourseOffering.objects.select_for_update()
        .filter(school_class=school_class, archived_at__isnull=True)
        .order_by('pk')
    )
    course_ids = [course.pk for course in courses]
    if Enrollment.objects.filter(
        pupil=pupil,
        course_id__in=course_ids,
        status=MembershipStatus.CLOSED,
        end_date__gte=start_date,
    ).exists():
        raise ValidationError('Sana avvalgi fan yozuvi davri bilan ustma-ust tushadi.')
    if active_membership:
        end_date = start_date - timedelta(days=1)
        active_membership.end_date = end_date
        active_membership.status = MembershipStatus.CLOSED
        active_membership.full_clean()
        active_membership.save(update_fields=['end_date', 'status'])

        for enrollment in Enrollment.objects.select_for_update().filter(
            pupil=pupil,
            status=MembershipStatus.ACTIVE,
        ):
            enrollment.end_date = end_date
            enrollment.status = MembershipStatus.CLOSED
            enrollment.full_clean()
            enrollment.save(update_fields=['end_date', 'status'])

    membership = ClassMembership(
        pupil=pupil,
        school_class=school_class,
        start_date=start_date,
    )
    membership.full_clean()
    membership.save()

    for course in courses:
        enrollment = Enrollment(
            pupil=pupil,
            course=course,
            start_date=start_date,
        )
        enrollment.full_clean()
        enrollment.save()

    return membership
