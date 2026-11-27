from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

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
from assessments.models import Attempt, Exam
from assignments.models import Assignment, Grade, Submission, SubmissionRevision
from files.models import Attachment
from learning.models import Topic


class Command(BaseCommand):
    help = 'Faqat yangi test DB uchun P083 synthetic restore-drill yozuvlarini yaratadi.'

    def handle(self, *args, **options):
        database_name = str(settings.DATABASES['default']['NAME'])
        if settings.APP_ENV != 'test' or not database_name.startswith('test_p083_'):
            raise CommandError(
                'seed_restore_drill faqat yangi test_p083_* DBda ishlaydi.',
            )
        if CourseOffering.objects.exists():
            raise CommandError('Restore drill source DB bo‘sh bo‘lishi kerak.')

        with transaction.atomic():
            self._seed()
        self.stdout.write('synthetic restore drill data created')

    @staticmethod
    def _seed():
        today = timezone.now()
        year = AcademicYear.objects.create(
            label='P083 synthetic year',
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=AcademicStatus.ACTIVE,
        )
        term = Term.objects.create(
            year=year,
            order=1,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 30),
            status=AcademicStatus.ACTIVE,
        )
        school_class = SchoolClass.objects.create(
            year=year,
            grade_level=7,
            section='R',
        )
        subject = Subject.objects.create(code='P083-SYN', title='Synthetic restore')
        course = CourseOffering.objects.create(
            school_class=school_class,
            subject=subject,
        )
        teacher = User.objects.create_user(
            username='p083.synthetic.teacher',
            password='synthetic-only-password',
            role=User.Role.TEACHER,
        )
        pupil = User.objects.create_user(
            username='p083.synthetic.pupil',
            password='synthetic-only-password',
            role=User.Role.PUPIL,
        )
        TeachingAssignment.objects.create(
            course=course,
            teacher=teacher,
            start_date=date(2026, 1, 1),
            can_grade=True,
        )
        ClassMembership.objects.create(
            pupil=pupil,
            school_class=school_class,
            start_date=date(2026, 1, 1),
            status=MembershipStatus.ACTIVE,
        )
        enrollment = Enrollment.objects.create(
            pupil=pupil,
            course=course,
            start_date=date(2026, 1, 1),
            status=MembershipStatus.ACTIVE,
        )
        topic = Topic.objects.create(
            course=course,
            term=term,
            title='Synthetic restore topic',
            objective='Synthetic restore objective',
            order=1,
            state=Topic.State.PUBLISHED,
            published_at=today,
        )
        assignment = Assignment.objects.create(
            topic=topic,
            title='Synthetic restore assignment',
            instructions='Synthetic only',
            available_at=today - timedelta(days=2),
            due_at=today - timedelta(days=1),
            closes_at=today + timedelta(days=1),
            max_score=Decimal('10.00'),
            max_attempts=1,
            state=Assignment.State.PUBLISHED,
            published_at=today,
        )
        attachment = Attachment.objects.create(
            owner=pupil,
            key=uuid4(),
            original_name='synthetic-restore.pdf',
            size=len(b'%PDF-1.7\n'),
            detected_mime='application/pdf',
            scan_status=Attachment.ScanStatus.CLEAN,
        )
        storages['private'].save(
            str(attachment.key),
            ContentFile(b'%PDF-1.7\n'),
        )
        submission = Submission.objects.create(
            assignment=assignment,
            pupil=pupil,
            draft_text='Synthetic only',
            draft_version=1,
        )
        revision = SubmissionRevision.objects.create(
            submission=submission,
            revision_number=1,
            submitted_version=1,
            submitted_text='Synthetic only',
            submitted_at=today,
            is_late=False,
        )
        Grade.objects.create(
            revision=revision,
            grader=teacher,
            state=Grade.State.PUBLISHED,
            total=Decimal('8.00'),
            feedback='Synthetic only',
            version=1,
            published_at=today,
        )
        exam = Exam.objects.create(
            course=course,
            term=term,
            title='Synthetic restore exam',
            opens_at=today - timedelta(hours=1),
            closes_at=today + timedelta(hours=1),
            duration=timedelta(minutes=30),
            attempt_limit=1,
            state=Exam.State.PUBLISHED,
            published_at=today,
        )
        Attempt.objects.create(
            exam=exam,
            pupil=pupil,
            enrollment=enrollment,
            attempt_number=1,
            started_at=today - timedelta(minutes=30),
            expires_at=today,
            state=Attempt.State.FINISHED,
        )
