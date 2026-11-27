from datetime import date
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.utils import timezone
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from accounts.models import User
from academics.models import AcademicYear, CourseOffering, SchoolClass, Subject, TeachingAssignment, Term
from ai.pdf_extraction import (
    MAX_EXTRACTED_CHARACTERS,
    MAX_PDF_BYTES,
    PDFNoText,
    PDFParseError,
    PDFResourceUnavailable,
    PDFTooLarge,
    PDFTooManyPages,
    extract_pdf_text,
)
from files.models import Attachment
from learning.models import Resource, Topic


def _pdf_with_pages(*texts):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        if text is None:
            continue
        font = DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        })
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(
            b'BT /F1 12 Tf 72 720 Td ('
            + text.encode('latin-1')
            + b') Tj ET'
        )
        page[NameObject('/Contents')] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def pdf_resource(db):
    year = AcademicYear.objects.create(
        label='PDF 2026', start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
    )
    term = Term.objects.create(
        year=year, order=1, start_date=date(2026, 1, 1), end_date=date(2026, 6, 1),
    )
    course = CourseOffering.objects.create(
        school_class=SchoolClass.objects.create(year=year, grade_level=7, section='P'),
        subject=Subject.objects.create(code='PDF-TEST', title='PDF test'),
    )
    teacher = User.objects.create_user(
        username='pdf-teacher', password='safe-test-password', role=User.Role.TEACHER,
    )
    other_teacher = User.objects.create_user(
        username='pdf-other', password='safe-test-password', role=User.Role.TEACHER,
    )
    TeachingAssignment.objects.create(
        course=course, teacher=teacher, start_date=date(2026, 1, 1),
    )
    topic = Topic.objects.create(
        course=course, term=term, title='PDF source', objective='obj', order=1,
        state=Topic.State.PUBLISHED, published_at=timezone.now(),
    )

    def make_resource(content, *, version=7, size=None):
        digest = sha256(content).hexdigest()
        attachment = Attachment.objects.create(
            owner=teacher,
            key=uuid4(),
            original_name='source.pdf',
            size=len(content) if size is None else size,
            detected_mime='application/pdf',
            scan_status=Attachment.ScanStatus.CLEAN,
            content_sha256=digest,
            scanned_sha256=digest,
        )
        if size is None:
            storages['private'].save(str(attachment.key), ContentFile(content))
        return Resource.objects.create(
            topic=topic, kind=Resource.Kind.PDF, title='Source', attachment=attachment,
            state=Resource.State.PUBLISHED, version=version,
        )

    return teacher, other_teacher, make_resource


def test_extracts_private_pdf_text_with_page_and_versioned_provenance(pdf_resource):
    teacher, _, make_resource = pdf_resource
    resource = make_resource(_pdf_with_pages('First page', 'Second page'))

    result = extract_pdf_text(
        actor=teacher, resource_id=resource.pk, resource_version=resource.version,
    )

    assert [(chunk.page_number, chunk.text.strip()) for chunk in result.chunks] == [
        (1, 'First page'), (2, 'Second page'),
    ]
    assert all(chunk.source_id == resource.pk for chunk in result.chunks)
    assert all(chunk.resource_version == 7 for chunk in result.chunks)
    assert result.resource_sha256 == resource.attachment.content_sha256


def test_scan_pdf_without_text_is_no_text_and_does_not_start_ocr(pdf_resource):
    teacher, _, make_resource = pdf_resource
    resource = make_resource(_pdf_with_pages(None))

    with pytest.raises(PDFNoText) as error:
        extract_pdf_text(actor=teacher, resource_id=resource.pk, resource_version=7)
    assert error.value.code == 'no_text'


def test_scope_and_exact_version_are_checked_before_private_file_read(pdf_resource):
    _, other_teacher, make_resource = pdf_resource
    resource = make_resource(_pdf_with_pages('Private'))

    with pytest.raises(PDFResourceUnavailable):
        extract_pdf_text(actor=other_teacher, resource_id=resource.pk, resource_version=7)
    with pytest.raises(PDFResourceUnavailable):
        extract_pdf_text(actor=resource.attachment.owner, resource_id=resource.pk, resource_version=6)


def test_large_and_excess_page_pdf_fail_with_controlled_errors(pdf_resource):
    teacher, _, make_resource = pdf_resource
    oversized = make_resource(b'x', size=MAX_PDF_BYTES + 1)
    with pytest.raises(PDFTooLarge):
        extract_pdf_text(actor=teacher, resource_id=oversized.pk, resource_version=7)

    too_many_pages = make_resource(_pdf_with_pages(*([None] * 101)), version=8)
    with pytest.raises(PDFTooManyPages):
        extract_pdf_text(actor=teacher, resource_id=too_many_pages.pk, resource_version=8)

    malformed = make_resource(b'not a PDF document', version=9)
    with pytest.raises(PDFParseError) as error:
        extract_pdf_text(actor=teacher, resource_id=malformed.pk, resource_version=9)
    assert error.value.code == 'pdf_parse_error'


def test_text_is_capped_at_twenty_thousand_characters(pdf_resource):
    teacher, _, make_resource = pdf_resource
    resource = make_resource(_pdf_with_pages('x' * (MAX_EXTRACTED_CHARACTERS + 100)))

    result = extract_pdf_text(actor=teacher, resource_id=resource.pk, resource_version=7)

    assert sum(len(chunk.text) for chunk in result.chunks) == MAX_EXTRACTED_CHARACTERS
    assert result.truncated is True
