from datetime import timedelta
from functools import partial

from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError

from outbox.models import OutboxEvent


@transaction.atomic
def enqueue_outbox_event(*, kind, payload, dedup_key):
    event, created = OutboxEvent.objects.get_or_create(
        dedup_key=dedup_key,
        defaults={
            'kind': kind,
            'payload': payload,
            'status': OutboxEvent.Status.PENDING,
            'next_run_at': timezone.now(),
        },
    )
    if not created and (event.kind != kind or event.payload != payload):
        raise ValueError(
            'Mavjud dedup_key boshqa event turi yoki payload bilan ishlatilgan.'
        )
    if created:
        transaction.on_commit(partial(wake_event_dispatcher, event.pk))
    return event, created


def wake_event_dispatcher(event_id):
    from outbox.tasks import wake_outbox_dispatcher

    try:
        wake_outbox_dispatcher.apply_async(
            args=(event_id,),
            queue='default',
        )
    except OperationalError as exc:
        defer_outbox_event(
            event_id=event_id,
            error=(
                'Dispatcher uyg‘otilmadi '
                f'({type(exc).__name__}).'
            ),
        )


def defer_outbox_event(*, event_id, error, attempts=None):
    event = OutboxEvent.objects.get(pk=event_id)
    retry_number = attempts if attempts is not None else max(event.attempts, 1)
    delay_seconds = min(2 ** min(retry_number, 12), 3600)
    OutboxEvent.objects.filter(
        pk=event_id,
        status__in=(
            OutboxEvent.Status.PENDING,
            OutboxEvent.Status.DEFERRED,
        ),
    ).update(
        status=OutboxEvent.Status.DEFERRED,
        next_run_at=timezone.now() + timedelta(seconds=delay_seconds),
        last_error=error[:4000],
        updated_at=timezone.now(),
    )
