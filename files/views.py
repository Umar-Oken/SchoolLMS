import uuid

from django.core.files.storage import storages
from django.db import DatabaseError
from django.http import FileResponse
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
        key = uuid.uuid4()
        storage = storages['private']
        stored_key = storage.save(str(key), uploaded_file)

        try:
            attachment = Attachment.objects.create(
                owner=request.user,
                key=key,
                original_name=uploaded_file.name,
                size=uploaded_file.size,
                detected_mime=detected_mime,
                scan_status=Attachment.ScanStatus.PENDING_SCAN,
            )
        except DatabaseError:
            storage.delete(stored_key)
            raise

        return Response(
            AttachmentReadSerializer(attachment).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    summary='Private faylni yuklab olish',
    description=(
        'Faqat clean Attachment egasi yoki published mavzu ichidagi, '
        'foydalanuvchi role/course scope’iga mos published PDF resurs orqali '
        'ochiladi. Public media URL berilmaydi.'
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

        try:
            file_handle = storages['private'].open(str(attachment.key), 'rb')
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
