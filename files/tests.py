import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import User
from files.models import Attachment
from files.storage import PrivateLocalStorage, PrivateS3Storage
from files.serializers import MAX_ATTACHMENT_SIZE


@pytest.fixture
def attachment_api_user(db):
    return User.objects.create_user(
        username='attachment-api-owner',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )


def image_bytes(image_format):
    output = BytesIO()
    Image.new('RGB', (1, 1)).save(output, format=image_format)
    return output.getvalue()


@pytest.mark.django_db
def test_attachment_defaults_to_pending_scan():
    owner = User.objects.create_user(
        username='attachment-owner',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )

    attachment = Attachment.objects.create(
        owner=owner,
        original_name='lesson.pdf',
        size=128,
        detected_mime='application/pdf',
    )

    assert attachment.scan_status == Attachment.ScanStatus.PENDING_SCAN
    assert isinstance(attachment.key, uuid.UUID)


def test_private_storage_saves_uuid_outside_public_media(tmp_path):
    storage = PrivateLocalStorage(location=tmp_path)
    key = uuid.uuid4()

    saved_key = storage.save(str(key), ContentFile(b'private bytes'))
    saved_path = Path(storage.path(saved_key))

    assert saved_key == str(key)
    assert saved_path.is_relative_to(tmp_path)
    assert not saved_path.is_relative_to(settings.MEDIA_ROOT)
    with saved_path.open('rb') as stored_file:
        assert stored_file.read() == b'private bytes'
    with pytest.raises(ValueError, match='do not have public URLs'):
        storage.url(saved_key)
    assert Client().get(f'{settings.MEDIA_URL}{saved_key}').status_code == 404


def test_private_storage_rejects_non_uuid_keys(tmp_path):
    storage = PrivateLocalStorage(location=tmp_path)

    with pytest.raises(SuspiciousFileOperation):
        storage.save('../public.txt', ContentFile(b'not allowed'))


def test_private_storage_alias_is_separate_from_media_root():
    storage = storages['private']

    assert isinstance(storage, PrivateLocalStorage)
    assert not Path(storage.location).is_relative_to(Path(settings.MEDIA_ROOT))


def test_private_s3_storage_generates_short_lived_signed_download_url():
    key = uuid.uuid4()
    storage = PrivateS3Storage(
        bucket_name='private-test-bucket',
        endpoint_url='https://s3.example.test',
        region_name='us-east-1',
        access_key='test-access-key',
        secret_key='test-secret-key',
    )

    signed_url = storage.signed_download_url(
        str(key),
        filename='lesson file.pdf',
        expire=60,
    )
    query = parse_qs(urlparse(signed_url).query)

    assert storage.default_acl is None
    assert storage.querystring_auth is True
    assert storage.file_overwrite is False
    assert storage.custom_domain is None
    assert query['X-Amz-Expires'] == ['60']
    assert 'X-Amz-Signature' in query
    assert 'ResponseContentDisposition' not in query


@pytest.mark.django_db
def test_s3_signed_download_requires_clean_owner_scope(attachment_api_user):
    clean_attachment = Attachment.objects.create(
        owner=attachment_api_user,
        original_name='private lesson.pdf',
        size=1,
        detected_mime='application/pdf',
        scan_status=Attachment.ScanStatus.CLEAN,
    )
    unclean_attachment = Attachment.objects.create(
        owner=attachment_api_user,
        original_name='unclean lesson.pdf',
        size=1,
        detected_mime='application/pdf',
        scan_status=Attachment.ScanStatus.PENDING_SCAN,
    )
    other_pupil = User.objects.create_user(
        username='signed-url-foreign-pupil',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )

    class FakeSignedStorage:
        supports_signed_urls = True

        def signed_download_url(self, name, *, filename, expire):
            assert name == str(clean_attachment.key)
            assert filename == clean_attachment.original_name
            assert expire == settings.PRIVATE_S3_SIGNED_URL_TTL
            return 'https://s3.example.test/private-signed-url'

    storage = FakeSignedStorage()
    owner_client = APIClient()
    owner_client.force_authenticate(attachment_api_user)
    other_client = APIClient()
    other_client.force_authenticate(other_pupil)

    with patch('files.views.storages', {'private': storage}):
        allowed = owner_client.get(
            f'/api/v1/attachments/{clean_attachment.pk}/download/',
        )
        foreign = other_client.get(
            f'/api/v1/attachments/{clean_attachment.pk}/download/',
        )
        unclean = owner_client.get(
            f'/api/v1/attachments/{unclean_attachment.pk}/download/',
        )

    assert allowed.status_code == 302
    assert allowed['Location'] == 'https://s3.example.test/private-signed-url'
    assert foreign.status_code == 404
    assert unclean.status_code == 404


@pytest.mark.django_db
def test_attachment_scan_status_is_restricted_by_database():
    owner = User.objects.create_user(
        username='invalid-scan-owner',
        password='safe-test-password',
        role=User.Role.PUPIL,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Attachment.objects.create(
            owner=owner,
            original_name='unsafe.bin',
            size=1,
            scan_status='unknown',
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('filename', 'content', 'declared_type', 'detected_type'),
    [
        ('lesson.pdf', b'%PDF-1.7\n', 'text/plain', 'application/pdf'),
        ('picture.jpg', image_bytes('JPEG'), 'application/pdf', 'image/jpeg'),
        ('picture.png', image_bytes('PNG'), 'image/jpeg', 'image/png'),
    ],
)
def test_upload_accepts_allowed_detected_mime(
    attachment_api_user,
    filename,
    content,
    declared_type,
    detected_type,
):
    client = APIClient()
    client.force_authenticate(attachment_api_user)

    response = client.post(
        '/api/v1/attachments/',
        {
            'file': SimpleUploadedFile(
                filename,
                content,
                content_type=declared_type,
            )
        },
        format='multipart',
    )

    assert response.status_code == 201
    attachment = Attachment.objects.get()
    assert attachment.owner == attachment_api_user
    assert attachment.scan_status == Attachment.ScanStatus.PENDING_SCAN
    assert attachment.detected_mime == detected_type
    assert response.data['detected_mime'] == detected_type
    assert 'owner' not in response.data

    storage = storages['private']
    try:
        assert storage.exists(str(attachment.key))
        assert storage.path(str(attachment.key)).endswith(str(attachment.key))
        with pytest.raises(ValueError, match='do not have public URLs'):
            storage.url(str(attachment.key))
    finally:
        storage.delete(str(attachment.key))


@pytest.mark.django_db
def test_upload_rejects_owner_status_injection(attachment_api_user):
    client = APIClient()
    client.force_authenticate(attachment_api_user)

    response = client.post(
        '/api/v1/attachments/',
        {
            'file': SimpleUploadedFile(
                'lesson.pdf',
                b'%PDF-1.7\n',
                content_type='application/pdf',
            ),
            'owner': '99999',
            'scan_status': Attachment.ScanStatus.CLEAN,
            'key': str(uuid.uuid4()),
        },
        format='multipart',
    )

    assert response.status_code == 400
    assert not Attachment.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('filename', 'content'),
    [
        ('notes.txt', b'plain text'),
        ('disguised.exe', b'%PDF-1.7\n'),
        ('wrong-extension.png', image_bytes('JPEG')),
    ],
)
def test_upload_rejects_unsupported_or_mismatched_file(
    attachment_api_user,
    filename,
    content,
):
    client = APIClient()
    client.force_authenticate(attachment_api_user)

    response = client.post(
        '/api/v1/attachments/',
        {
            'file': SimpleUploadedFile(
                filename,
                content,
                content_type='application/pdf',
            )
        },
        format='multipart',
    )

    assert response.status_code == 400
    assert not Attachment.objects.exists()


@pytest.mark.django_db
def test_upload_over_limit_returns_413(attachment_api_user):
    client = APIClient()
    client.force_authenticate(attachment_api_user)

    with patch(
        'files.serializers.AttachmentUploadSerializer.validate',
        side_effect=AssertionError('Oversized upload reached serializer.'),
    ) as serializer_validate:
        response = client.post(
            '/api/v1/attachments/',
            {
                'file': SimpleUploadedFile(
                    'large.pdf',
                    b'x' * (MAX_ATTACHMENT_SIZE + 1),
                    content_type='application/pdf',
                )
            },
            format='multipart',
        )

    assert response.status_code == 413
    serializer_validate.assert_not_called()
    assert response.data['message'] == (
        'Fayl hajmi 10 MB limitdan oshmasligi kerak.'
    )
    assert not Attachment.objects.exists()


@pytest.mark.django_db
def test_upload_accepts_file_at_size_limit(attachment_api_user):
    client = APIClient()
    client.force_authenticate(attachment_api_user)
    header = b'%PDF-1.7\n'
    content = header + b' ' * (MAX_ATTACHMENT_SIZE - len(header))

    response = client.post(
        '/api/v1/attachments/',
        {
            'file': SimpleUploadedFile(
                'limit.pdf',
                content,
                content_type='application/pdf',
            )
        },
        format='multipart',
    )

    assert response.status_code == 201
    attachment = Attachment.objects.get()
    assert attachment.size == MAX_ATTACHMENT_SIZE
    storages['private'].delete(str(attachment.key))


@pytest.mark.django_db
def test_upload_requires_authentication():
    response = APIClient().post(
        '/api/v1/attachments/',
        {
            'file': SimpleUploadedFile(
                'lesson.pdf',
                b'%PDF-1.7\n',
                content_type='application/pdf',
            )
        },
        format='multipart',
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_upload_rejects_multiple_files_in_one_request(attachment_api_user):
    client = APIClient()
    client.force_authenticate(attachment_api_user)

    response = client.post(
        '/api/v1/attachments/',
        {
            'file': [
                SimpleUploadedFile('one.pdf', b'%PDF-1.7\n'),
                SimpleUploadedFile('two.pdf', b'%PDF-1.7\n'),
            ]
        },
        format='multipart',
    )

    assert response.status_code == 400
    assert not Attachment.objects.exists()
