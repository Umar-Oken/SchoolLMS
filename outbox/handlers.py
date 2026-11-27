from outbox.models import OutboxEffectReceipt
from notifications.services import fan_out_announcement
from files.tasks import scan_attachment
from ai.tasks import generate_question_drafts


class EventHandler:
    def __init__(self, *, queue, handle):
        self.queue = queue
        self.handle = handle


def _handle_test_effect(event):
    OutboxEffectReceipt.objects.get_or_create(
        dedup_key=event.dedup_key,
        defaults={
            'event': event,
            'kind': event.kind,
        },
    )


def _handle_announcement_published(event):
    fan_out_announcement(event)
    OutboxEffectReceipt.objects.get_or_create(
        dedup_key=event.dedup_key,
        defaults={
            'event': event,
            'kind': event.kind,
        },
    )


def _handle_attachment_scan_requested(event):
    scan_attachment.apply_async(
        args=(
            event.payload['attachment_id'],
            event.payload['sha256'],
        ),
        queue='scans',
    )


def _handle_ai_question_draft_requested(event):
    generate_question_drafts.apply_async(args=(event.payload['job_id'],), queue='ai')
    OutboxEffectReceipt.objects.get_or_create(
        dedup_key=event.dedup_key,
        defaults={
            'event': event,
            'kind': event.kind,
        },
    )
    OutboxEffectReceipt.objects.get_or_create(
        dedup_key=event.dedup_key,
        defaults={
            'event': event,
            'kind': event.kind,
        },
    )


HANDLERS = {
    'test.effect': EventHandler(queue='default', handle=_handle_test_effect),
    'announcement.published': EventHandler(
        queue='default',
        handle=_handle_announcement_published,
    ),
    'attachment.scan_requested': EventHandler(
        queue='default',
        handle=_handle_attachment_scan_requested,
    ),
    'ai.question_draft.requested': EventHandler(
        queue='default',
        handle=_handle_ai_question_draft_requested,
    ),
}
