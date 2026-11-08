from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from accounts.models import User
from academics.services import ensure_term_open
from academics.views import teacher_course_scope
from files.models import Attachment
from learning.models import Resource, Topic


@transaction.atomic
def publish_topic(topic_id, *, actor):
    if not isinstance(actor, User) or actor.role != User.Role.TEACHER:
        raise NotFound()

    topic = (
        Topic.objects.select_for_update()
        .select_related('course__school_class', 'term')
        .filter(
            pk=topic_id,
            course_id__in=teacher_course_scope(
                actor,
                active_only=True,
            ).values('pk'),
            archived_at__isnull=True,
        )
        .first()
    )
    if topic is None:
        raise NotFound()

    ensure_term_open(topic.term, lock=True)

    if topic.state == Topic.State.PUBLISHED:
        if topic.published_at is None:
            raise ValidationError({
                'published_at': 'Published topic must have a publication time.'
            })
        return topic

    if topic.state != Topic.State.DRAFT:
        raise ValidationError({'state': 'Only draft topics can be published.'})

    errors = {}
    if not topic.title.strip():
        errors['title'] = 'Title must not be blank.'
    if not topic.objective.strip():
        errors['objective'] = 'Objective must not be blank.'
    if topic.term.year_id != topic.course.school_class.year_id:
        errors['term'] = 'Term and course must belong to the same academic year.'
    if errors:
        raise ValidationError(errors)

    topic.state = Topic.State.PUBLISHED
    topic.published_at = timezone.now()
    try:
        topic.full_clean()
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict) from exc
    topic.save(update_fields=['state', 'published_at'])
    return topic


@transaction.atomic
def publish_resource(resource_id, *, actor):
    if not isinstance(actor, User) or actor.role != User.Role.TEACHER:
        raise NotFound()

    resource = (
        Resource.objects.select_for_update(of=('self',))
        .select_related('topic', 'topic__course', 'attachment')
        .filter(
            pk=resource_id,
            topic__course_id__in=teacher_course_scope(
                actor,
                active_only=True,
            ).values('pk'),
            topic__archived_at__isnull=True,
        )
        .first()
    )
    if resource is None:
        raise NotFound()
    if resource.topic.state != Topic.State.PUBLISHED:
        raise ValidationError({
            'topic': 'Resursni nashr qilishdan oldin mavzuni nashr qiling.'
        })
    if resource.kind == Resource.Kind.PDF:
        attachment = (
            Attachment.objects.select_for_update()
            .filter(pk=resource.attachment_id)
            .first()
        )
        if (
            attachment is None
            or attachment.owner_id != actor.pk
            or attachment.scan_status != Attachment.ScanStatus.CLEAN
            or attachment.detected_mime != 'application/pdf'
        ):
            raise ValidationError({
                'attachment': 'Resurs uchun egasiga tegishli, clean PDF fayl kerak.'
            })
    elif resource.kind in (Resource.Kind.VIDEO, Resource.Kind.LINK):
        if not resource.url:
            raise ValidationError({'url': 'Published video/link resurs URL talab qiladi.'})
    else:
        raise ValidationError({'kind': 'Resurs turi noto‘g‘ri.'})
    if resource.state == Resource.State.PUBLISHED:
        return resource
    if resource.state != Resource.State.DRAFT:
        raise ValidationError({'state': 'Faqat draft resurs nashr qilinadi.'})

    resource.state = Resource.State.PUBLISHED
    resource.save(update_fields=['state'])
    return resource
