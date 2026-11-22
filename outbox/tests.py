from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError

from outbox.models import OutboxEffectReceipt, OutboxEvent
from outbox.services import enqueue_outbox_event
from outbox.tasks import (
    consume_outbox_event,
    dispatch_pending_outbox_events,
    publish_outbox_event,
    wake_outbox_dispatcher,
)


@pytest.mark.django_db(transaction=True)
def test_outbox_event_is_rolled_back_without_waking_dispatcher(
    monkeypatch,
):
    wake_calls = []
    monkeypatch.setattr(
        'outbox.services.wake_event_dispatcher',
        lambda event_id: wake_calls.append(event_id),
    )
    with pytest.raises(RuntimeError, match='rollback'):
        with transaction.atomic():
            enqueue_outbox_event(
                kind='test.effect',
                payload={'value': 'temporary'},
                dedup_key='rollback-event',
            )
            raise RuntimeError('rollback')

    assert not OutboxEvent.objects.filter(dedup_key='rollback-event').exists()
    assert wake_calls == []


@pytest.mark.django_db(transaction=True)
def test_broker_failure_defers_event_and_dispatcher_replays_after_recovery(
    monkeypatch,
):
    def broker_is_down(*args, **kwargs):
        raise OperationalError('Redis unavailable')

    monkeypatch.setattr(
        wake_outbox_dispatcher,
        'apply_async',
        broker_is_down,
    )
    event, created = enqueue_outbox_event(
        kind='test.effect',
        payload={'value': 'safe'},
        dedup_key='replay-event',
    )

    assert created is True
    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.DEFERRED
    assert event.next_run_at > timezone.now()
    assert 'OperationalError' in event.last_error

    event.next_run_at = timezone.now() - timedelta(seconds=1)
    event.save(update_fields=('next_run_at',))
    dispatched_ids = []
    monkeypatch.setattr(
        'outbox.tasks.consume_outbox_event.apply_async',
        lambda *, args, queue: dispatched_ids.append((args, queue)),
    )

    assert dispatch_pending_outbox_events.run() == [
        OutboxEvent.Status.SENT,
    ]
    assert dispatched_ids == [((event.pk,), 'default')]
    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.SENT
    assert event.attempts == 1
    event.next_run_at = timezone.now() - timedelta(seconds=1)
    event.save(update_fields=('next_run_at',))
    assert dispatch_pending_outbox_events.run() == [
        OutboxEvent.Status.SENT,
    ]
    assert event.pk == dispatched_ids[-1][0][0]
    event.refresh_from_db()
    assert event.attempts == 2
    assert consume_outbox_event.run(event.pk) == OutboxEvent.Status.PROCESSED
    assert consume_outbox_event.run(event.pk) == OutboxEvent.Status.PROCESSED
    assert OutboxEffectReceipt.objects.filter(
        dedup_key=event.dedup_key,
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_duplicate_consumer_delivery_creates_one_effect_and_processes_once(
    monkeypatch,
):
    monkeypatch.setattr(
        'outbox.services.wake_event_dispatcher',
        lambda event_id: None,
    )
    event, _ = enqueue_outbox_event(
        kind='test.effect',
        payload={'value': 'once'},
        dedup_key='duplicate-delivery-event',
    )
    event.status = OutboxEvent.Status.SENT
    event.save(update_fields=('status',))

    assert consume_outbox_event.run(event.pk) == OutboxEvent.Status.PROCESSED
    assert consume_outbox_event.run(event.pk) == OutboxEvent.Status.PROCESSED

    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.PROCESSED
    assert OutboxEffectReceipt.objects.filter(
        dedup_key=event.dedup_key,
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_ambiguous_broker_failure_does_not_overwrite_processed_event(
    monkeypatch,
):
    event = OutboxEvent.objects.create(
        kind='test.effect',
        payload={'value': 'accepted-before-disconnect'},
        dedup_key='ambiguous-publish-event',
        next_run_at=timezone.now(),
    )

    def publish_then_disconnect(*, args, queue):
        consume_outbox_event.run(*args)
        raise OperationalError('connection lost after publish')

    monkeypatch.setattr(
        'outbox.tasks.consume_outbox_event.apply_async',
        publish_then_disconnect,
    )

    assert publish_outbox_event(event.pk) == OutboxEvent.Status.PROCESSED
    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.PROCESSED
    assert OutboxEffectReceipt.objects.filter(
        dedup_key=event.dedup_key,
    ).count() == 1


@pytest.mark.django_db
def test_unknown_kind_is_explicitly_failed_without_broker_publish(monkeypatch):
    event = OutboxEvent.objects.create(
        kind='future.unregistered',
        payload={},
        dedup_key='unknown-event',
        next_run_at=timezone.now(),
    )
    publish_calls = []
    monkeypatch.setattr(
        'outbox.tasks.consume_outbox_event.apply_async',
        lambda **kwargs: publish_calls.append(kwargs),
    )

    assert publish_outbox_event(event.pk) == OutboxEvent.Status.FAILED
    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.FAILED
    assert 'Noma’lum outbox event turi' in event.last_error
    assert publish_calls == []


@pytest.mark.django_db(transaction=True)
def test_duplicate_dedup_key_cannot_change_existing_event_payload(
    monkeypatch,
):
    monkeypatch.setattr(
        'outbox.services.wake_event_dispatcher',
        lambda event_id: None,
    )
    event, created = enqueue_outbox_event(
        kind='test.effect',
        payload={'value': 'first'},
        dedup_key='same-key',
    )

    with pytest.raises(ValueError, match='dedup_key'):
        enqueue_outbox_event(
            kind='test.effect',
            payload={'value': 'different'},
            dedup_key='same-key',
        )
    assert created is True
    assert OutboxEvent.objects.filter(dedup_key='same-key').count() == 1
    event.refresh_from_db()
    assert event.payload == {'value': 'first'}
