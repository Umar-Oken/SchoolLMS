import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Attachment(models.Model):
    class ScanStatus(models.TextChoices):
        PENDING_SCAN = 'pending_scan', 'Pending scan'
        CLEAN = 'clean', 'Clean'
        REJECTED = 'rejected', 'Rejected'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='attachments',
    )
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    original_name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    detected_mime = models.CharField(max_length=127, blank=True, default='')
    scan_status = models.CharField(
        max_length=16,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING_SCAN,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    scan_status__in=['pending_scan', 'clean', 'rejected']
                ),
                name='files_attachment_scan_status_valid',
            ),
        ]
