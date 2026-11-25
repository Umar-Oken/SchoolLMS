from django.db.models import (
    BooleanField,
    Count,
    Exists,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
)
from django.utils import timezone

from academics.models import Enrollment
from academics.views import pupil_course_scope, teacher_course_scope
from assignments.models import (
    Assignment,
    Grade,
    Submission,
    SubmissionRevision,
)
from learning.models import Completion, Topic
from scheduling.models import LessonSession


def pupil_dashboard_data(pupil):
    """Return only the dashboard resources visible to ``pupil`` right now."""
    now = timezone.now()
    today = timezone.localdate(now)
    courses = pupil_course_scope(pupil, active_only=True)

    enrolled_for_session = Enrollment.objects.filter(
        course_id=OuterRef('slot__course_id'),
        pupil=pupil,
        start_date__lte=OuterRef('scheduled_date'),
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=OuterRef('scheduled_date')))
    lessons = (
        LessonSession.objects.filter(scheduled_date=today)
        .exclude(status=LessonSession.Status.CANCELLED)
        .annotate(is_enrolled=Exists(enrolled_for_session))
        .filter(is_enrolled=True)
        .select_related('slot__course__subject', 'slot__room', 'room_override')
        .order_by('starts_at', 'pk')
    )

    topics = Topic.objects.filter(
        course__in=courses,
        state=Topic.State.PUBLISHED,
        archived_at__isnull=True,
    )
    completed_topic_ids = set(
        Completion.objects.filter(
            pupil=pupil,
            kind=Completion.Kind.READ,
            topic__in=topics,
        ).values_list('topic_id', flat=True)
    )

    revisions = SubmissionRevision.objects.select_related('grade', 'return_record')
    submissions = Submission.objects.filter(pupil=pupil).prefetch_related(
        Prefetch('revisions', queryset=revisions, to_attr='dashboard_revisions'),
    )
    deadlines = list(
        Assignment.objects.filter(
            topic__in=topics,
            state=Assignment.State.PUBLISHED,
            due_at__gte=now,
        )
        .select_related('topic__course__subject')
        .prefetch_related(
            Prefetch('submissions', queryset=submissions, to_attr='pupil_submissions'),
        )
        .order_by('due_at', 'pk')[:3]
    )

    feedback = (
        Grade.objects.filter(
            state=Grade.State.PUBLISHED,
            revision__submission__pupil=pupil,
            revision__submission__assignment__topic__in=topics,
        )
        .select_related('revision__submission__assignment__topic__course__subject')
        .order_by('-published_at', '-pk')[:3]
    )
    return {
        'lessons': lessons,
        'topics': topics,
        'completed_topic_ids': completed_topic_ids,
        'deadlines': deadlines,
        'feedback': feedback,
    }


def teacher_dashboard_data(teacher):
    """Return actionable, teacher-scoped dashboard data without pupil profiles."""
    today = timezone.localdate()
    courses = teacher_course_scope(teacher, active_only=True)
    gradable_courses = teacher_course_scope(
        teacher,
        active_only=True,
        can_grade=True,
    )
    sessions = (
        LessonSession.objects.filter(
            scheduled_date=today,
            slot__course__in=courses,
        )
        .exclude(status=LessonSession.Status.CANCELLED)
        .filter(
            Q(teacher_override=teacher)
            | Q(teacher_override__isnull=True, slot__teacher=teacher),
        )
        .select_related('slot__course__subject', 'slot__room', 'room_override')
        .order_by('starts_at', 'pk')
    )

    latest_revision = SubmissionRevision.objects.filter(
        submission_id=OuterRef('pk'),
    ).order_by('-revision_number')
    review_queue = (
        Submission.objects.filter(
            assignment__topic__course__in=gradable_courses,
            assignment__topic__state='published',
            assignment__topic__archived_at__isnull=True,
            assignment__state=Assignment.State.PUBLISHED,
        )
        .annotate(
            latest_revision_id=Subquery(latest_revision.values('pk')[:1]),
            latest_is_late=Subquery(
                latest_revision.values('is_late')[:1],
                output_field=BooleanField(),
            ),
            latest_returned_id=Subquery(
                latest_revision.values('return_record__pk')[:1],
            ),
            latest_grade_state=Subquery(latest_revision.values('grade__state')[:1]),
        )
        .filter(latest_revision_id__isnull=False)
    )
    counts = review_queue.aggregate(
        submitted_ungraded_count=Count(
            'pk',
            filter=Q(
                latest_returned_id__isnull=True,
                latest_grade_state__isnull=True,
            ),
        ),
        returned_count=Count('pk', filter=Q(latest_returned_id__isnull=False)),
        late_submitted_ungraded_count=Count(
            'pk',
            filter=Q(
                latest_is_late=True,
                latest_returned_id__isnull=True,
                latest_grade_state__isnull=True,
            ),
        ),
    )
    return {'sessions': sessions, 'counts': counts}
