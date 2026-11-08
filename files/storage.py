import uuid

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage


class PrivateLocalStorage(FileSystemStorage):
    def __init__(self, location=None, base_url=None, **kwargs):
        super().__init__(location=location, base_url=None, **kwargs)

    def get_available_name(self, name, max_length=None):
        try:
            key = uuid.UUID(name)
        except (AttributeError, TypeError, ValueError) as exc:
            raise SuspiciousFileOperation(
                'Private storage keys must be UUIDs.'
            ) from exc

        normalized_name = str(key)
        if self.exists(normalized_name):
            raise FileExistsError('A private file already exists for this key.')
        if max_length and len(normalized_name) > max_length:
            raise SuspiciousFileOperation(
                'The UUID storage key exceeds the maximum length.'
            )
        return normalized_name

    def url(self, name):
        raise ValueError('Private files do not have public URLs.')
