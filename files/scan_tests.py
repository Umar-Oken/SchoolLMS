import hashlib
import uuid
from unittest.mock import Mock

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from rest_framework.test import APIClient

from accounts.models import User
from files.models import Attachment
from files.scanners import (
    ScannerUnavailable,
    ScannerVerdict,
    validate_scanner_configuration,
)
from files.tasks import MAX_SCANNER_RETRIES, scan_attachment
from outbox.models import OutboxEvent


@pytest.fixture
def stored_attachment(db):
    owner = User.objects.create_user(
        username='scan-test-owner',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    content = b'%PDF-1.7\nsynthetic scan bytes'
    content_sha256 = hashlib.sha256(content).hexdigest()
    key = uuid.uuid4()
    storage = storages['private']
    storage.save(str(key), ContentFile(content))
    attachment = Attachment.objects.create(
        owner=owner,
        key=key,
        original_name='scan-test.pdf',
        size=len(content),
        detected_mime='application/pdf',
        content_sha256=content_sha256,
    )
    yield attachment
    storage.delete(str(key))


@pytest.mark.django_db
def test_upload_stores_sha256_and_enqueues_scan_outbox_event(
    monkeypatch,
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    owner = User.objects.create_user(
        username='scan-upload-owner',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )
    monkeypatch.setattr(
        'outbox.services.wake_event_dispatcher',
        lambda event_id: None,
    )
    client = APIClient()
    client.force_authenticate(owner)
    content = b'%PDF-1.7\nupload scan content'

    response = client.post(
        '/api/v1/attachments/',
        {
            'file': SimpleUploadedFile(
                'upload.pdf',
                content,
                content_type='application/pdf',
            ),
        },
        format='multipart',
    )

    assert response.status_code == 201
    attachment = Attachment.objects.get()
    event = OutboxEvent.objects.get(kind='attachment.scan_requested')
    expected_hash = hashlib.sha256(content).hexdigest()
    assert attachment.content_sha256 == expected_hash
    assert attachment.scan_status == Attachment.ScanStatus.PENDING_SCAN
    assert event.payload == {
        'attachment_id': attachment.pk,
        'sha256': expected_hash,
    }
    assert event.dedup_key == f'attachment:{attachment.pk}:scan:{expected_hash}'
    storages['private'].delete(str(attachment.key))


@pytest.mark.django_db
def test_clean_scan_and_duplicate_delivery_preserve_result(
    stored_attachment,
    monkeypatch,
):
    scanner = Mock()
    scanner.scan.return_value = ScannerVerdict.CLEAN
    monkeypatch.setattr('files.tasks.get_scanner', lambda: scanner)

    first_result = scan_attachment.run(
        stored_attachment.pk,
        stored_attachment.content_sha256,
    )
    second_result = scan_attachment.run(
        stored_attachment.pk,
        stored_attachment.content_sha256,
    )
    stored_attachment.refresh_from_db()

    assert first_result == Attachment.ScanStatus.CLEAN
    assert second_result == Attachment.ScanStatus.CLEAN
    assert stored_attachment.scanned_sha256 == stored_attachment.content_sha256
    assert stored_attachment.scanned_at is not None
    assert stored_attachment.scan_error == ''
    scanner.scan.assert_called_once()


@pytest.mark.django_db
def test_infected_scan_never_becomes_downloadable(stored_attachment, monkeypatch):
    scanner = Mock()
    scanner.scan.return_value = ScannerVerdict.INFECTED
    monkeypatch.setattr('files.tasks.get_scanner', lambda: scanner)

    result = scan_attachment.run(
        stored_attachment.pk,
        stored_attachment.content_sha256,
    )
    stored_attachment.refresh_from_db()
    client = APIClient()
    client.force_authenticate(stored_attachment.owner)
    response = client.get(
        f'/api/v1/attachments/{stored_attachment.pk}/download/'
    )

    assert result == Attachment.ScanStatus.INFECTED
    assert stored_attachment.scan_status == Attachment.ScanStatus.INFECTED
    assert response.status_code == 404


@pytest.mark.django_db
def test_unavailable_scanner_retries_then_marks_attachment_failed(
    stored_attachment,
    monkeypatch,
):
    monkeypatch.setattr(
        'files.tasks.get_scanner',
        lambda: Mock(
            scan=Mock(side_effect=ScannerUnavailable('unavailable')),
        ),
    )
    scan_attachment.push_request(retries=0)
    try:
        # Celery 5.5.3: task.retry() re-raises the original exception
        with pytest.raises(ScannerUnavailable):
            scan_attachment.run(
                stored_attachment.pk,
                stored_attachment.content_sha256,
            )
    finally:
        scan_attachment.pop_request()
    assert Attachment.objects.get(pk=stored_attachment.pk).scan_status == (
        Attachment.ScanStatus.PENDING_SCAN
    )

    scan_attachment.push_request(retries=MAX_SCANNER_RETRIES)
    try:
        result = scan_attachment.run(
            stored_attachment.pk,
            stored_attachment.content_sha256,
        )
    finally:
        scan_attachment.pop_request()
    stored_attachment.refresh_from_db()

    assert result == Attachment.ScanStatus.FAILED
    assert stored_attachment.scan_status == Attachment.ScanStatus.FAILED
    assert stored_attachment.scan_error == 'scanner_unavailable'
    client = APIClient()
    client.force_authenticate(stored_attachment.owner)
    response = client.get(
        f'/api/v1/attachments/{stored_attachment.pk}/download/'
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_scan_hash_mismatch_fails_closed(stored_attachment):
    result = scan_attachment.run(
        stored_attachment.pk,
        '0' * 64,
    )
    stored_attachment.refresh_from_db()

    assert result == 'stale'
    assert stored_attachment.scan_status == Attachment.ScanStatus.PENDING_SCAN


def test_production_configuration_rejects_fake_scanner():
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured, match='not allowed in production'):
        validate_scanner_configuration(
            app_env='production',
            backend='fake',
        )


def test_scanner_configuration_accepts_real_or_unavailable_backend():
    validate_scanner_configuration(
        app_env='production',
        backend='clamav',
    )
    validate_scanner_configuration(
        app_env='production',
        backend='unavailable',
    )
