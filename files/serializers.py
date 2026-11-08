from pathlib import Path

import magic
from rest_framework import serializers
from rest_framework.exceptions import APIException

from files.models import Attachment


MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
ALLOWED_MIME_TYPES = {
    'application/pdf',
    'image/jpeg',
    'image/png',
}
ALLOWED_EXTENSIONS = {
    'application/pdf': {'.pdf'},
    'image/jpeg': {'.jpg', '.jpeg'},
    'image/png': {'.png'},
}


class AttachmentTooLarge(APIException):
    status_code = 413
    default_detail = 'Fayl hajmi 10 MB limitdan oshmasligi kerak.'
    default_code = 'file_too_large'


class AttachmentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate(self, attrs):
        uploaded_files = getattr(self.initial_data, 'getlist', None)
        if uploaded_files and len(uploaded_files('file')) != 1:
            raise serializers.ValidationError(
                {'file': 'Bitta so‘rovda faqat bitta fayl yuboring.'}
            )

        unknown_fields = set(self.initial_data) - {'file'}
        if unknown_fields:
            raise serializers.ValidationError(
                {
                    field: 'Bu maydon qabul qilinmaydi.'
                    for field in sorted(unknown_fields)
                }
            )

        uploaded_file = attrs['file']
        if uploaded_file.size > MAX_ATTACHMENT_SIZE:
            raise AttachmentTooLarge()

        uploaded_file.seek(0)
        detected_mime = magic.from_buffer(uploaded_file.read(8192), mime=True)
        uploaded_file.seek(0)
        if detected_mime not in ALLOWED_MIME_TYPES:
            raise serializers.ValidationError(
                {'file': 'Faqat PDF, JPEG yoki PNG fayllari qabul qilinadi.'}
            )
        extension = Path(uploaded_file.name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS[detected_mime]:
            raise serializers.ValidationError(
                {'file': 'Fayl kengaytmasi aniqlangan turga mos emas.'}
            )

        attrs['detected_mime'] = detected_mime
        return attrs


class AttachmentReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = (
            'id',
            'key',
            'original_name',
            'size',
            'detected_mime',
            'scan_status',
        )
        read_only_fields = fields
