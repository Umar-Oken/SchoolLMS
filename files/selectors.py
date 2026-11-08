from django.db.models import Q

from accounts.models import User
from academics.views import pupil_course_scope, teacher_course_scope
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

    return (
        Attachment.objects.filter(scan_status=Attachment.ScanStatus.CLEAN)
        .filter(
            Q(owner=user)
            | Q(pk__in=resources.values('attachment_id'))
        )
        .select_related('owner')
        .distinct()
    )
