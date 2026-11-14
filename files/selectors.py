from django.db.models import Q

from accounts.models import User
from academics.views import pupil_course_scope, teacher_course_scope
from assignments.models import SubmissionAttachment
from files.models import Attachment
from learning.models import Resource, Topic


def attachment_download_scope(user):
    resources = Resource.objects.filter(
        kind=Resource.Kind.PDF,
        state=Resource.State.PUBLISHED,
        topic__state=Topic.State.PUBLISHED,
        topic__archived_at__isnull=True,
        attachment__detected_mime='application/pdf',
    )
    if user.role == User.Role.PUPIL:
        resources = resources.filter(
            topic__course_id__in=pupil_course_scope(user).values('pk'),
        )
    elif user.role == User.Role.TEACHER:
        resources = resources.filter(
            topic__course_id__in=teacher_course_scope(user).values('pk'),
        )
    else:
        resources = resources.none()

    submission_attachments = SubmissionAttachment.objects.none()
    if user.role == User.Role.PUPIL:
        submission_attachments = SubmissionAttachment.objects.filter(
            Q(
                submission__pupil=user,
                submission__assignment__state='published',
                submission__assignment__topic__state=Topic.State.PUBLISHED,
                submission__assignment__topic__archived_at__isnull=True,
                submission__assignment__topic__course_id__in=(
                    pupil_course_scope(user, active_only=True).values('pk')
                ),
            ),
        ) | SubmissionAttachment.objects.filter(
            revision__submission__pupil=user,
            revision__submission__assignment__state='published',
            revision__submission__assignment__topic__state=Topic.State.PUBLISHED,
            revision__submission__assignment__topic__archived_at__isnull=True,
            revision__submission__assignment__topic__course_id__in=(
                pupil_course_scope(user, active_only=True).values('pk')
            ),
        )
    elif user.role == User.Role.TEACHER:
        submission_attachments = SubmissionAttachment.objects.filter(
            revision__isnull=False,
            revision__submission__assignment__state='published',
            revision__submission__assignment__topic__state=Topic.State.PUBLISHED,
            revision__submission__assignment__topic__archived_at__isnull=True,
            revision__submission__assignment__topic__course_id__in=(
                teacher_course_scope(user, active_only=True).values('pk')
            ),
        )

    return (
        Attachment.objects.filter(scan_status=Attachment.ScanStatus.CLEAN)
        .filter(
            Q(owner=user)
            | Q(pk__in=resources.values('attachment_id'))
            | Q(pk__in=submission_attachments.values('attachment_id'))
        )
        .select_related('owner')
        .distinct()
    )
