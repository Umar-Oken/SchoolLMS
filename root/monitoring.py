from django.conf import settings
from django.db.models import Count

from files.models import Attachment
from outbox.models import OutboxEvent
from root.celery import app


def _broker_status():
    try:
        with app.connection_for_read() as connection:
            connection.ensure_connection(max_retries=0)
        return 'ok'
    except Exception:
        return 'unavailable'


def _worker_status():
    try:
        replies = app.control.inspect(timeout=1.0).ping() or {}
    except Exception:
        return {'status': 'unavailable', 'count': 0}
    return {
        'status': 'ok' if replies else 'unavailable',
        'count': len(replies),
    }


def _outbox_counts():
    counts = {
        status: 0
        for status in (
            OutboxEvent.Status.PENDING,
            OutboxEvent.Status.DEFERRED,
            OutboxEvent.Status.SENT,
            OutboxEvent.Status.FAILED,
        )
    }
    for row in OutboxEvent.objects.values('status').annotate(total=Count('pk')):
        if row['status'] in counts:
            counts[row['status']] = row['total']
    return counts


def background_status():
    scanner_backend = settings.ANTIVIRUS_SCANNER_BACKEND
    scanner_status = (
        'not_configured'
        if scanner_backend == 'unavailable'
        else 'simulated'
        if scanner_backend == 'fake'
        else 'configured'
    )
    return {
        'broker': {'status': _broker_status()},
        'workers': _worker_status(),
        'outbox': _outbox_counts(),
        'scanner': {
            'status': scanner_status,
            'failed_attachments': Attachment.objects.filter(
                scan_status=Attachment.ScanStatus.FAILED,
            ).count(),
        },
        # AI worker queue exists, but no AI dependency is configured yet.
        'ai': {'status': 'not_configured'},
    }
