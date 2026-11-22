from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from kombu.exceptions import OperationalError

from outbox.handlers import HANDLERS
from outbox.models import OutboxEvent, OutboxEffectReceipt
from outbox.services import defer_outbox_event


DISPATCH_LEASE_SECONDS = 300
MAX_DISPATCH_BATCH = 100


def publish_outbox_event(event_id):
    now = timezone.now()
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        if event.status in (
            OutboxEvent.Status.PROCESSED,
            OutboxEvent.Status.FAILED,
        ):
            return event.status
        if (
            event.status == OutboxEvent.Status.SENT
            and event.next_run_at > now
        ):
            return event.status
        if event.next_run_at > now:
            return event.status

        handler = HANDLERS.get(event.kind)
        if handler is None:
            event.status = OutboxEvent.Status.FAILED
            event.last_error = f'Noma’lum outbox event turi: {event.kind}'
            event.save(update_fields=('status', 'last_error', 'updated_at'))
            return event.status

        event.attempts += 1
        event.next_run_at = now + timedelta(seconds=DISPATCH_LEASE_SECONDS)
        event.save(update_fields=('attempts', 'next_run_at', 'updated_at'))
        attempts = event.attempts

    try:
        consume_outbox_event.apply_async(
            args=(event_id,),
            queue=handler.queue,
        )
    except OperationalError as exc:
        defer_outbox_event(
            event_id=event_id,
            error=(
                'Brokerga yuborish muvaffaqiyatsiz '
                f'({type(exc).__name__}).'
            ),
            attempts=attempts,
        )
        return OutboxEvent.objects.values_list(
            'status',
            flat=True,
        ).get(pk=event_id)

    OutboxEvent.objects.filter(
        pk=event_id,
        status__in=(OutboxEvent.Status.PENDING, OutboxEvent.Status.DEFERRED),
        next_run_at__gt=now,
    ).update(
        status=OutboxEvent.Status.SENT,
        last_error='',
        updated_at=timezone.now(),
    )
    return OutboxEvent.objects.values_list('status', flat=True).get(pk=event_id)


@shared_task(name='outbox.tasks.wake_outbox_dispatcher')
def wake_outbox_dispatcher(event_id):
    return publish_outbox_event(event_id)


@shared_task(name='outbox.tasks.dispatch_pending_outbox_events')
def dispatch_pending_outbox_events():
    now = timezone.now()
    event_ids = list(
        OutboxEvent.objects.filter(
            Q(
                status__in=(
                    OutboxEvent.Status.PENDING,
                    OutboxEvent.Status.DEFERRED,
                ),
                next_run_at__lte=now,
            )
            | Q(status=OutboxEvent.Status.SENT, next_run_at__lte=now),
        )
        .order_by('next_run_at', 'pk')
        .values_list('pk', flat=True)[:MAX_DISPATCH_BATCH]
    )
    return [
        publish_outbox_event(event_id)
        for event_id in event_ids
    ]


@shared_task(
    name='outbox.tasks.consume_outbox_event',
    acks_late=True,
    reject_on_worker_lost=True,
)
def consume_outbox_event(event_id):
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        if event.status == OutboxEvent.Status.PROCESSED:
            return event.status
        if event.status == OutboxEvent.Status.FAILED:
            return event.status

        handler = HANDLERS.get(event.kind)
        if handler is None:
            event.status = OutboxEvent.Status.FAILED
            event.last_error = f'Noma’lum outbox event turi: {event.kind}'
            event.save(update_fields=('status', 'last_error', 'updated_at'))
            return event.status

        if not OutboxEffectReceipt.objects.filter(
            dedup_key=event.dedup_key,
        ).exists():
            handler.handle(event)
        event.status = OutboxEvent.Status.PROCESSED
        event.last_error = ''
        event.save(update_fields=('status', 'last_error', 'updated_at'))
        return event.status
