import hashlib
import uuid

from django.conf import settings
from django.core.files.storage import storages
from django.db import DatabaseError
from django.db import transaction
from django.http import FileResponse, HttpResponseRedirect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from files.models import Attachment
from files.serializers import (
    AttachmentReadSerializer,
    AttachmentUploadSerializer,
)
from files.selectors import attachment_download_scope
from files.upload_handlers import AttachmentSizeLimitUploadHandler
from outbox.services import enqueue_outbox_event
from accounts.permissions import MustChangePasswordPermission
from root.openapi import common_error_responses, error_response


@extend_schema(
    summary='Private fayl yuklash',
    description=(
        'Bitta PDF, JPEG yoki PNG faylni multipart/form-data orqali qabul qiladi. '
        'Aniqlanmagan fayl turi va 10 MB dan katta fayl qabul qilinmaydi. '
        'Fayl scan uchun pending_scan holatida saqlanadi; public URL berilmaydi.'
    ),
    tags=['Files'],
    request=AttachmentUploadSerializer,
    responses={
        status.HTTP_201_CREATED: AttachmentReadSerializer,
        **common_error_responses(include_not_found=False),
        413: error_response(
            'Fayl 10 MB limitdan oshdi.',
            {'detail': 'Fayl hajmi 10 MB limitdan oshmasligi kerak.'},
            name='413 — fayl juda katta',
        ),
    },
)
class AttachmentUploadView(GenericAPIView):
    serializer_class = AttachmentUploadSerializer
    parser_classes = (MultiPartParser, FormParser)

    def initialize_request(self, request, *args, **kwargs):
        request.upload_handlers.insert(
            0,
            AttachmentSizeLimitUploadHandler(request),
        )
        return super().initialize_request(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data['file']
        detected_mime = serializer.validated_data['detected_mime']
        uploaded_file.seek(0)
        digest = hashlib.sha256()
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
        content_sha256 = digest.hexdigest()
        uploaded_file.seek(0)
        storage = storages['private']
        existing_clean = Attachment.objects.filter(
            content_sha256=content_sha256,
            scan_status=Attachment.ScanStatus.CLEAN,
        ).first()
        if existing_clean:
            key = existing_clean.key
            stored_key = None
        else:
            key = uuid.uuid4()
            stored_key = storage.save(str(key), uploaded_file)

        try:
            with transaction.atomic():
                if existing_clean:
                    attachment = Attachment.objects.create(
                        owner=request.user,
                        key=key,
                        original_name=uploaded_file.name,
                        size=uploaded_file.size,
                        detected_mime=detected_mime,
                        scan_status=Attachment.ScanStatus.CLEAN,
                        content_sha256=content_sha256,
                        scanned_sha256=content_sha256,
                        scanned_at=existing_clean.scanned_at,
                    )
                else:
                    attachment = Attachment.objects.create(
                        owner=request.user,
                        key=key,
                        original_name=uploaded_file.name,
                        size=uploaded_file.size,
                        detected_mime=detected_mime,
                        scan_status=Attachment.ScanStatus.PENDING_SCAN,
                        content_sha256=content_sha256,
                    )
                    enqueue_outbox_event(
                        kind='attachment.scan_requested',
                        payload={
                            'attachment_id': attachment.pk,
                            'sha256': content_sha256,
                        },
                        dedup_key=(
                            f'attachment:{attachment.pk}:scan:{content_sha256}'
                        ),
                    )
        except (DatabaseError, ValueError):
            if stored_key is not None:
                storage.delete(stored_key)
            raise

        return Response(
            AttachmentReadSerializer(attachment).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    summary='Private faylni yuklab olish',
    description=(
        'Faqat clean Attachment egasi, pupilning o‘z published assignment '
        'drafti, teacherning faol kursidagi assignment revisioni yoki published '
        'PDF resource’ning tegishli scope’i orqali ochiladi. Teacher draft '
        'attachment’ni ololmaydi. Public media URL berilmaydi.'
    ),
    tags=['Files'],
    responses={
        status.HTTP_200_OK: OpenApiTypes.BINARY,
        **common_error_responses(),
    },
)
class AttachmentDownloadView(GenericAPIView):
    serializer_class = AttachmentReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)

    def get(self, request, *args, **kwargs):
        attachment = (
            attachment_download_scope(request.user)
            .filter(pk=kwargs['pk'])
            .first()
        )
        if attachment is None:
            raise NotFound()

        storage = storages['private']
        if getattr(storage, 'supports_signed_urls', False):
            return HttpResponseRedirect(
                storage.signed_download_url(
                    str(attachment.key),
                    filename=attachment.original_name,
                    expire=settings.PRIVATE_S3_SIGNED_URL_TTL,
                )
            )

        try:
            file_handle = storage.open(str(attachment.key), 'rb')
        except FileNotFoundError as exc:
            raise NotFound('Private fayl topilmadi.') from exc

        response = FileResponse(
            file_handle,
            as_attachment=True,
            filename=attachment.original_name,
            content_type=attachment.detected_mime,
        )
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        return response
