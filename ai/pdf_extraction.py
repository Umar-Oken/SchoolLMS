"""Bounded, private PDF text extraction for later AI job workers.

The returned document text is *untrusted source material*.  In particular, a
sentence in a PDF that looks like an instruction is not an application or
system instruction.  This module does not call a model, fetch URLs, or OCR
image-only PDFs.
"""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from django.core.files.storage import storages
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from accounts.models import User
from academics.views import teacher_course_scope
from files.models import Attachment
from learning.models import Resource, Topic


MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_EXTRACTED_CHARACTERS = 20_000


class PDFExtractionError(Exception):
    """A safe, stable extraction failure suitable for a future worker."""

    code = 'pdf_extraction_error'

    def __init__(self, message=None):
        super().__init__(message or self.code)


class PDFResourceUnavailable(PDFExtractionError):
    code = 'resource_unavailable'


class PDFResourceHashMismatch(PDFExtractionError):
    code = 'resource_hash_mismatch'


class PDFTooLarge(PDFExtractionError):
    code = 'pdf_too_large'


class PDFTooManyPages(PDFExtractionError):
    code = 'pdf_too_many_pages'


class PDFParseError(PDFExtractionError):
    code = 'pdf_parse_error'


class PDFNoText(PDFExtractionError):
    """PDF has no extractable text; OCR is deliberately out of scope."""

    code = 'no_text'


@dataclass(frozen=True)
class PDFTextChunk:
    """Text with immutable source provenance, not executable instructions."""

    source_id: int
    resource_version: int
    resource_sha256: str
    page_number: int
    text: str


@dataclass(frozen=True)
class PDFTextExtraction:
    source_id: int
    resource_version: int
    resource_sha256: str
    chunks: tuple[PDFTextChunk, ...]
    truncated: bool


def _scoped_resource(*, actor, resource_id, resource_version):
    """Re-read the exact allowed version; do not trust a caller's instance."""
    if not isinstance(actor, User) or actor.role != User.Role.TEACHER:
        raise PDFResourceUnavailable()

    resource = (
        Resource.objects.select_related('attachment')
        .filter(
            pk=resource_id,
            version=resource_version,
            kind=Resource.Kind.PDF,
            state=Resource.State.PUBLISHED,
            topic__state=Topic.State.PUBLISHED,
            topic__archived_at__isnull=True,
            attachment__owner=actor,
            attachment__scan_status=Attachment.ScanStatus.CLEAN,
            attachment__detected_mime='application/pdf',
            topic__course_id__in=teacher_course_scope(
                actor,
                active_only=True,
            ).values('pk'),
        )
        .first()
    )
    if resource is None:
        raise PDFResourceUnavailable()
    return resource


def _read_private_pdf(attachment):
    """Read bounded bytes via the private Storage API, never a public URL."""
    if attachment.size > MAX_PDF_BYTES:
        raise PDFTooLarge()
    resource_hash = attachment.content_sha256
    if (
        len(resource_hash) != 64
        or attachment.scanned_sha256 != resource_hash
    ):
        raise PDFResourceHashMismatch()
    try:
        with storages['private'].open(str(attachment.key), 'rb') as source:
            content = source.read(MAX_PDF_BYTES + 1)
    except OSError as exc:
        raise PDFParseError('private_storage_unavailable') from exc
    if len(content) > MAX_PDF_BYTES:
        raise PDFTooLarge()
    if len(content) != attachment.size or sha256(content).hexdigest() != resource_hash:
        raise PDFResourceHashMismatch()
    return content, resource_hash


def extract_pdf_text(*, actor, resource_id, resource_version):
    """Extract at most 100 pages/20,000 characters from one authorized PDF.

    Image-only (scanned) PDFs consistently raise ``PDFNoText``.  No OCR,
    network request, persistence, model call, or vector/RAG operation occurs.
    """
    resource = _scoped_resource(
        actor=actor,
        resource_id=resource_id,
        resource_version=resource_version,
    )
    content, resource_hash = _read_private_pdf(resource.attachment)
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        page_count = len(reader.pages)
    except (PdfReadError, ValueError, TypeError, KeyError, OSError) as exc:
        raise PDFParseError() from exc
    if page_count > MAX_PDF_PAGES:
        raise PDFTooManyPages()

    chunks = []
    extracted_characters = 0
    truncated = False
    try:
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ''
            if not text.strip():
                continue
            remaining = MAX_EXTRACTED_CHARACTERS - extracted_characters
            if remaining <= 0:
                truncated = True
                break
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            chunks.append(PDFTextChunk(
                source_id=resource.pk,
                resource_version=resource.version,
                resource_sha256=resource_hash,
                page_number=page_number,
                text=text,
            ))
            extracted_characters += len(text)
    except (PdfReadError, ValueError, TypeError, KeyError, OSError) as exc:
        raise PDFParseError() from exc

    if not chunks:
        raise PDFNoText()
    return PDFTextExtraction(
        source_id=resource.pk,
        resource_version=resource.version,
        resource_sha256=resource_hash,
        chunks=tuple(chunks),
        truncated=truncated,
    )
