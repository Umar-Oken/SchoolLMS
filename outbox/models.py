from django.db import models
from django.db.models import F, Q


OUTBOX_STATUS_VALUES = ('pending', 'sent', 'processed', 'failed', 'deferred')


class OutboxEvent(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Kutilmoqda'
        SENT = 'sent', 'Brokerga yuborildi'
        PROCESSED = 'processed', 'Bajarildi'
        FAILED = 'failed', 'Yakuniy xato'
        DEFERRED = 'deferred', 'Kechiktirildi'

    kind = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    dedup_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    next_run_at = models.DateTimeField()
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('next_run_at', 'pk')
        constraints = [
            models.CheckConstraint(
                condition=Q(attempts__gte=0),
                name='outbox_event_attempts_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(status__in=OUTBOX_STATUS_VALUES),
                name='outbox_event_status_valid',
            ),
        ]

    def __str__(self):
        return f'{self.kind}:{self.dedup_key} ({self.status})'


class OutboxEffectReceipt(models.Model):
    event = models.OneToOneField(
        OutboxEvent,
        on_delete=models.PROTECT,
        related_name='effect_receipt',
    )
    dedup_key = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')
