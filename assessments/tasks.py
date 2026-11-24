from celery import shared_task
from django.db import OperationalError
from django.utils import timezone

from assessments.models import Attempt
from assessments.services import finalize_attempt


MAX_EXPIRED_ATTEMPT_BATCH = 100


@shared_task(
    name='assessments.tasks.finalize_expired_attempts',
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={'max_retries': 3},
    acks_late=True,
    reject_on_worker_lost=True,
)
def finalize_expired_attempts():
    now = timezone.now()
    attempts = list(
        Attempt.objects.filter(
            state=Attempt.State.ACTIVE,
            expires_at__lte=now,
        ).select_related('pupil').order_by('expires_at', 'pk')[:MAX_EXPIRED_ATTEMPT_BATCH]
    )
    finalized = 0
    for attempt in attempts:
        finalize_attempt(attempt_id=attempt.pk, pupil=attempt.pupil)
        finalized += 1
    return finalized
