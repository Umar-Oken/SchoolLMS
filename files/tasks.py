import hashlib
from io import BytesIO

from celery import shared_task
from django.core.files.storage import storages
from django.db import transaction
from django.utils import timezone

from files.models import Attachment
from files.upload_handlers import MAX_ATTACHMENT_SIZE
from files.scanners import (
    ScannerUnavailable,
    ScannerVerdict,
    get_scanner,
)


MAX_SCANNER_RETRIES = 3
MAX_SCANNER_BACKOFF_SECONDS = 60
SCAN_CHUNK_SIZE = 64 * 1024


def _set_failed(attachment_id, *, expected_sha256, error_code):
    with transaction.atomic():
        attachment = (
            Attachment.objects.select_for_update()
            .filter(pk=attachment_id)
            .first()
        )
        if attachment is None or attachment.content_sha256 != expected_sha256:
            return None
        if (
            attachment.scan_status in (
                Attachment.ScanStatus.CLEAN,
                Attachment.ScanStatus.INFECTED,
            )
            and attachment.scanned_sha256 == expected_sha256
        ):
            return attachment.scan_status
        attachment.scan_status = Attachment.ScanStatus.FAILED
        attachment.scan_error = error_code
        attachment.scanned_at = timezone.now()
        attachment.save(
            update_fields=('scan_status', 'scan_error', 'scanned_at'),
        )
        return attachment.scan_status


def _scan_content(attachment):
    storage = storages['private']
    content = bytearray()
    digest = hashlib.sha256()
    try:
        with storage.open(str(attachment.key), 'rb') as stored_file:
            while True:
                chunk = stored_file.read(SCAN_CHUNK_SIZE)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_ATTACHMENT_SIZE:
                    return None, None, 'file_size_mismatch'
                digest.update(chunk)
    except OSError:
        return None, None, 'private_storage_unavailable'

    if len(content) != attachment.size:
        return None, None, 'file_size_mismatch'
    return bytes(content), digest.hexdigest(), ''


def _record_result(attachment_id, *, expected_sha256, verdict):
    with transaction.atomic():
        attachment = (
            Attachment.objects.select_for_update()
            .filter(pk=attachment_id)
            .first()
        )
        if attachment is None or attachment.content_sha256 != expected_sha256:
            return None
        if (
            attachment.scan_status in (
                Attachment.ScanStatus.CLEAN,
                Attachment.ScanStatus.INFECTED,
            )
            and attachment.scanned_sha256 == expected_sha256
        ):
            return attachment.scan_status
        attachment.scan_status = verdict
        attachment.scanned_sha256 = expected_sha256
        attachment.scanned_at = timezone.now()
        attachment.scan_error = ''
        attachment.save(
            update_fields=(
                'scan_status',
                'scanned_sha256',
                'scanned_at',
                'scan_error',
            ),
        )
        return attachment.scan_status


@shared_task(
    bind=True,
    name='files.tasks.scan_attachment',
    acks_late=True,
    reject_on_worker_lost=True,
)
def scan_attachment(self, attachment_id, expected_sha256):
    attachment = (
        Attachment.objects.filter(pk=attachment_id).first()
    )
    if attachment is None or attachment.content_sha256 != expected_sha256:
        return 'stale'
    if (
        attachment.scan_status in (
            Attachment.ScanStatus.CLEAN,
            Attachment.ScanStatus.INFECTED,
        )
        and attachment.scanned_sha256 == expected_sha256
    ):
        return attachment.scan_status

    content, actual_sha256, error_code = _scan_content(attachment)
    if error_code:
        return _set_failed(
            attachment_id,
            expected_sha256=expected_sha256,
            error_code=error_code,
        )
    if actual_sha256 != expected_sha256:
        return _set_failed(
            attachment_id,
            expected_sha256=expected_sha256,
            error_code='content_hash_mismatch',
        )

    try:
        verdict = get_scanner().scan(BytesIO(content))
    except ScannerUnavailable as exc:
        if self.request.retries >= MAX_SCANNER_RETRIES:
            return _set_failed(
                attachment_id,
                expected_sha256=expected_sha256,
                error_code='scanner_unavailable',
            )
        raise self.retry(
            exc=exc,
            countdown=min(
                2 ** self.request.retries * 15,
                MAX_SCANNER_BACKOFF_SECONDS,
            ),
            max_retries=MAX_SCANNER_RETRIES,
        )
    if verdict not in (ScannerVerdict.CLEAN, ScannerVerdict.INFECTED):
        return _set_failed(
            attachment_id,
            expected_sha256=expected_sha256,
            error_code='invalid_scanner_verdict',
        )
    return _record_result(
        attachment_id,
        expected_sha256=expected_sha256,
        verdict=(
            Attachment.ScanStatus.CLEAN
            if verdict == ScannerVerdict.CLEAN
            else Attachment.ScanStatus.INFECTED
        ),
    )
