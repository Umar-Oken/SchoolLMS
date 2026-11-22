from celery import shared_task


@shared_task(name='root.tasks.diagnostic_ping')
def diagnostic_ping():
    return 'celery-worker-ok'
