"""Bounded, idempotent AI draft worker; it never publishes assessment items."""
from celery import shared_task
from django.db import transaction
from rest_framework.exceptions import ValidationError as DRFValidationError

from ai.models import AIJob
from ai.pdf_extraction import PDFExtractionError, extract_pdf_text
from ai.providers import ProviderTimeout, ProviderUnavailable, get_configured_provider
from ai.services import store_result_drafts


def _fail(job_id, code):
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job_id)
        if job.state != AIJob.State.REVIEW:
            job.state, job.error = AIJob.State.FAILED, code
            job.save(update_fields=('state', 'error', 'updated_at'))


@shared_task(bind=True, name='ai.tasks.generate_question_drafts', acks_late=True,
             reject_on_worker_lost=True, max_retries=2)
def generate_question_drafts(self, job_id):
    with transaction.atomic():
        job = AIJob.objects.select_for_update().select_related('actor').filter(pk=job_id).first()
        if job is None or job.state in (AIJob.State.REVIEW, AIJob.State.FAILED):
            return 'already_finished'
        if job.state == AIJob.State.RUNNING:
            return 'already_running'
        if job.state != AIJob.State.QUEUED:
            return 'not_claimable'
        job.state = AIJob.State.RUNNING
        job.save(update_fields=('state', 'updated_at'))
    try:
        extraction = extract_pdf_text(actor=job.actor, resource_id=job.resource_id,
                                      resource_version=job.resource_version)
        citations = tuple({
            'source_id': chunk.source_id,
            'resource_version': chunk.resource_version,
            'resource_sha256': chunk.resource_sha256,
            'page_number': chunk.page_number,
        } for chunk in extraction.chunks)
        # Text only: no user profile, submission, or other PII is sent.
        from ai.providers import ResourceSnapshot
        snapshot = ResourceSnapshot(job.resource_id, job.resource_version, job.resource.title,
                                    '\n'.join(chunk.text for chunk in extraction.chunks), citations)
        provider = get_configured_provider()
        drafts = provider.generate_question_drafts(resource=snapshot, prompt_version=job.prompt_version,
                                                   timeout_seconds=60)
        allowed = {tuple(sorted(citation.items())) for citation in citations}
        store_result_drafts(job=job, drafts=drafts, allowed_citations=allowed)
        return 'review'
    except (PDFExtractionError, DRFValidationError, ValueError) as exc:
        _fail(job_id, getattr(exc, 'code', 'invalid_provider_output'))
        return 'failed'
    except ProviderTimeout as exc:
        with transaction.atomic():
            AIJob.objects.select_for_update().filter(pk=job_id, state=AIJob.State.RUNNING).update(
                state=AIJob.State.QUEUED, error='provider_timeout',
            )
        raise self.retry(exc=exc, countdown=15 * (self.request.retries + 1), max_retries=2)
    except ProviderUnavailable:
        _fail(job_id, 'provider_unconfigured')
        return 'failed'
