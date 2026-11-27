import uuid
from urllib.parse import quote

from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage
from storages.backends.s3 import S3Storage


class PrivateStorageKeyMixin:
    """Private storage object keys are opaque UUIDs, never user filenames."""

    supports_signed_urls = False

    @staticmethod
    def normalize_key(name):
        try:
            return str(uuid.UUID(str(name)))
        except (AttributeError, TypeError, ValueError) as exc:
            raise SuspiciousFileOperation(
                'Private storage keys must be UUIDs.'
            ) from exc

    def get_available_name(self, name, max_length=None):
        normalized_name = self.normalize_key(name)
        if self.exists(normalized_name):
            raise FileExistsError('A private file already exists for this key.')
        if max_length and len(normalized_name) > max_length:
            raise SuspiciousFileOperation(
                'The UUID storage key exceeds the maximum length.'
            )
        return normalized_name


class PrivateLocalStorage(PrivateStorageKeyMixin, FileSystemStorage):
    def __init__(self, location=None, base_url=None, **kwargs):
        super().__init__(location=location, base_url=None, **kwargs)

    def url(self, name):
        raise ValueError('Private files do not have public URLs.')


class PrivateS3Storage(PrivateStorageKeyMixin, S3Storage):
    """Private S3-compatible storage with short-lived signed downloads only."""

    supports_signed_urls = True

    def __init__(self, **kwargs):
        kwargs.update(
            default_acl=None,
            querystring_auth=True,
            file_overwrite=False,
            custom_domain=None,
            signature_version='s3v4',
        )
        super().__init__(**kwargs)
        if not self.bucket_name:
            raise ImproperlyConfigured('PRIVATE_S3_BUCKET must be configured.')

    def signed_download_url(self, name, *, filename, expire):
        key = self.normalize_key(name)
        disposition = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
        return self.url(
            key,
            parameters={'ResponseContentDisposition': disposition},
            expire=expire,
            http_method='get_object',
        )
