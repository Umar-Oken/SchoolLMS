from django.db.models import BooleanField, OuterRef, Prefetch, Q, Subquery
from rest_framework.exceptions import ValidationError

from academics.views import pupil_course_scope, teacher_course_scope
from assignments.models import (
    Assignment,
    Submission,
    SubmissionRevision,
)


def _positive_id(query_params, name):
    value = query_params.get(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({name: 'Musbat butun ID yuboring.'}) from exc
    if parsed < 1:
        raise ValidationError({name: 'Musbat butun ID yuboring.'})
    return parsed


def pupil_gradebook_queryset(pupil, query_params):
    course_id = _positive_id(query_params, 'course')
    term_id = _positive_id(query_params, 'term')
    courses = pupil_course_scope(pupil, active_only=True)
    queryset = Assignment.objects.filter(
        topic__course_id__in=courses.values('pk'),
        topic__state='published',
        topic__archived_at__isnull=True,
        state=Assignment.State.PUBLISHED,
    )
    if course_id is not None:
        queryset = queryset.filter(topic__course_id=course_id)
    if term_id is not None:
        queryset = queryset.filter(topic__term_id=term_id)

    revisions = (
        SubmissionRevision.objects.select_related(
            'grade',
            'return_record',
        )
        .prefetch_related('grade__criterion_scores__criterion')
        .order_by('revision_number')
    )
    submissions = (
        Submission.objects.filter(pupil=pupil)
        .prefetch_related(
            Prefetch(
                'revisions',
                queryset=revisions,
                to_attr='gradebook_revisions',
            )
        )
    )
    return (
        queryset.select_related(
            'topic__course__subject',
            'topic__term__year',
        )
        .prefetch_related(
            Prefetch(
                'submissions',
                queryset=submissions,
                to_attr='pupil_submissions',
            )
        )
        .order_by('topic__term__year__start_date', 'topic__term__order', 'pk')
    )


def teacher_submission_queue_queryset(
    *,
    course_id=None,
    assignment_id=None,
    late=None,
    status=None,
):
    revisions = (
        SubmissionRevision.objects.select_related(
            'grade',
            'return_record',
        )
        .order_by('revision_number')
    )
    queryset = (
        Submission.objects.filter(
            assignment__state=Assignment.State.PUBLISHED,
            assignment__topic__state='published',
            assignment__topic__archived_at__isnull=True,
            revisions__isnull=False,
        )
        .select_related(
            'pupil',
            'assignment',
            'assignment__topic',
        )
        .prefetch_related(
            Prefetch(
                'revisions',
                queryset=revisions,
                to_attr='queue_revisions',
            )
        )
        .distinct()
        .order_by(
            'assignment__due_at',
            'pupil__last_name',
            'pupil__first_name',
            'pupil_id',
        )
    )
    if course_id is not None:
        queryset = queryset.filter(
            assignment__topic__course_id=course_id,
        )
    if assignment_id is not None:
        queryset = queryset.filter(assignment_id=assignment_id)
    if late is not None or status is not None:
        latest_revision = SubmissionRevision.objects.filter(
            submission_id=OuterRef('pk'),
        ).order_by('-revision_number')
        queryset = queryset.annotate(
            latest_is_late=Subquery(
                latest_revision.values('is_late')[:1],
                output_field=BooleanField(),
            ),
            latest_returned_id=Subquery(
                latest_revision.values('return_record__pk')[:1],
            ),
            latest_grade_state=Subquery(
                latest_revision.values('grade__state')[:1],
            ),
        )
        if late is not None:
            queryset = queryset.filter(latest_is_late=late)
        status_filters = {
            'submitted': Q(
                latest_returned_id__isnull=True,
                latest_grade_state__isnull=True,
            ),
            'draft': Q(
                latest_returned_id__isnull=True,
                latest_grade_state='draft',
            ),
            'graded': Q(
                latest_returned_id__isnull=True,
                latest_grade_state='published',
            ),
            'returned': Q(latest_returned_id__isnull=False),
        }
        if status is not None:
            queryset = queryset.filter(status_filters[status])
    return queryset


def teacher_course_scope_queryset(teacher):
    return teacher_course_scope(teacher, active_only=True)
