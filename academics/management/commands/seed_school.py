import os
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from academics.models import (
    AcademicYear,
    ClassMembership,
    CourseOffering,
    MembershipStatus,
    SchoolClass,
    Subject,
    TeachingAssignment,
    Term,
)
from academics.services import assign_teacher, enroll_pupil_in_class


YEAR_LABEL = "2026-2027"
YEAR_START = date(2026, 9, 1)
YEAR_END = date(2027, 6, 30)
TERMS = (
    (1, date(2026, 9, 1), date(2026, 11, 30)),
    (2, date(2026, 12, 1), date(2027, 2, 28)),
    (3, date(2027, 3, 1), date(2027, 4, 30)),
    (4, date(2027, 5, 1), date(2027, 6, 30)),
)
TEACHERS = (
    ("seed.teacher.one", "SEED_TEACHER_ONE_PASSWORD", "Sami", "O‘qituvchi"),
    ("seed.teacher.two", "SEED_TEACHER_TWO_PASSWORD", "Dilorom", "O‘qituvchi"),
)
PUPILS = (
    ("seed.pupil.one", "SEED_PUPIL_ONE_PASSWORD", "Aziz", "O‘quvchi", "A"),
    ("seed.pupil.two", "SEED_PUPIL_TWO_PASSWORD", "Malika", "O‘quvchi", "A"),
    ("seed.pupil.three", "SEED_PUPIL_THREE_PASSWORD", "Kamol", "O‘quvchi", "B"),
    ("seed.pupil.four", "SEED_PUPIL_FOUR_PASSWORD", "Zarina", "O‘quvchi", "B"),
)
SUBJECTS = (
    ("SYN-MATH", "Matematika", "seed.teacher.one"),
    ("SYN-UZB", "O‘zbek tili", "seed.teacher.two"),
    ("SYN-SCI", "Tabiiy fanlar", "seed.teacher.two"),
)


class Command(BaseCommand):
    help = "Create a repeatable synthetic school dataset for development or tests."

    def handle(self, *args, **options):
        if settings.APP_ENV not in {"development", "test"}:
            raise CommandError("seed_school faqat development yoki test muhitida ishlaydi.")
        if settings.APP_ENV == "development" and not settings.DEBUG:
            raise CommandError("Development seed uchun DJANGO_DEBUG=true bo‘lishi kerak.")

        with transaction.atomic():
            self._seed()

        self.stdout.write(
            self.style.SUCCESS(
                "Synthetic seed tayyor: 1 yil, 4 chorak, 2 sinf, "
                "2 o‘qituvchi, 4 o‘quvchi, 3 fan."
            )
        )

    @staticmethod
    def _get_user(username, password_env, role, first_name, last_name):
        user = User.objects.filter(username=username).first()
        if user:
            if user.role != role:
                raise CommandError(
                    f"{username} foydalanuvchisining roli seed uchun mos emas."
                )
            if not user.is_active:
                raise CommandError(f"{username} foydalanuvchisi faol emas.")
            return user

        password = os.environ.get(password_env)
        if not password:
            raise CommandError(f"Yangi seed foydalanuvchisi uchun {password_env} talab qilinadi.")
        return User.objects.create_user(
            username=username,
            password=password,
            role=role,
            first_name=first_name,
            last_name=last_name,
            must_change_password=True,
        )

    def _seed(self):
        year, _ = AcademicYear.objects.get_or_create(
            label=YEAR_LABEL,
            defaults={
                "start_date": YEAR_START,
                "end_date": YEAR_END,
            },
        )

        for order, start_date, end_date in TERMS:
            Term.objects.get_or_create(
                year=year,
                order=order,
                defaults={"start_date": start_date, "end_date": end_date},
            )

        school_classes = {}
        for section in ("A", "B"):
            school_classes[section], _ = SchoolClass.objects.get_or_create(
                year=year,
                grade_level=7,
                section=section,
            )

        teachers = {
            username: self._get_user(
                username,
                password_env,
                User.Role.TEACHER,
                first_name,
                last_name,
            )
            for username, password_env, first_name, last_name in TEACHERS
        }
        pupils = [
            (
                self._get_user(
                    username,
                    password_env,
                    User.Role.PUPIL,
                    first_name,
                    last_name,
                ),
                school_classes[section],
            )
            for username, password_env, first_name, last_name, section in PUPILS
        ]

        offerings = []
        for code, title, teacher_username in SUBJECTS:
            subject, _ = Subject.objects.get_or_create(
                code=code,
                defaults={"title": title},
            )
            for school_class in school_classes.values():
                course, _ = CourseOffering.objects.get_or_create(
                    school_class=school_class,
                    subject=subject,
                )
                offerings.append((course, teachers[teacher_username]))

        for course, teacher in offerings:
            if not TeachingAssignment.objects.filter(
                course=course,
                teacher=teacher,
                start_date=YEAR_START,
            ).exists():
                assign_teacher(course, teacher, YEAR_START)

        for pupil, school_class in pupils:
            membership = (
                ClassMembership.objects.filter(
                    pupil=pupil,
                    status=MembershipStatus.ACTIVE,
                )
                .first()
            )
            if membership:
                if membership.school_class_id != school_class.pk:
                    raise CommandError(
                        f"{pupil.username} boshqa sinfga faol biriktirilgan."
                    )
                continue
            enroll_pupil_in_class(pupil, school_class, YEAR_START)
