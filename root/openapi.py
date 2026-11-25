from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse
from rest_framework import serializers


class ErrorEnvelopeSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField()
    request_id = serializers.CharField()


CSRF_HEADER = OpenApiParameter(
    name='X-CSRFToken',
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description='CSRF cookie bilan mos token. Session-auth POST/PATCH uchun kerak.',
)


def error_response(description, example, *, name='Xato javobi'):
    if 'code' not in example:
        if 'detail' in example:
            message = example['detail']
            fields = {}
        else:
            message = 'Validation failed.'
            fields = example
        example = {
            'code': 'api_error',
            'message': message,
            'fields': fields,
            'request_id': '8fd95b7d0f2a4e54a8a0e3fb7f7a51aa',
        }
    return OpenApiResponse(
        response=ErrorEnvelopeSerializer,
        description=description,
        examples=[
            OpenApiExample(
                name,
                value=example,
                response_only=True,
            )
        ],
    )


def common_error_responses(*, include_bad_request=True, include_not_found=True):
    responses = {
        401: error_response(
            'Autentifikatsiya ma’lumoti yo‘q yoki yaroqsiz.',
            {
                'code': 'not_authenticated',
                'message': 'Authentication credentials were not provided.',
                'fields': {},
                'request_id': '8fd95b7d0f2a4e54a8a0e3fb7f7a51aa',
            },
            name='401 — autentifikatsiya',
        ),
        403: error_response(
            'Session, CSRF yoki foydalanuvchi huquqi yetarli emas.',
            {
                'code': 'permission_denied',
                'message': 'You do not have permission to perform this action.',
                'fields': {},
                'request_id': '8fd95b7d0f2a4e54a8a0e3fb7f7a51aa',
            },
            name='403 — ruxsat rad etildi',
        ),
    }
    if include_bad_request:
        responses[400] = error_response(
            'Body maydonlari noto‘g‘ri yoki biznes qoidasi bajarilmadi.',
            {
                'code': 'validation_error',
                'message': 'Validation failed.',
                'fields': {
                    'field': [{'message': 'Bu maydon noto‘g‘ri.', 'code': 'invalid'}],
                },
                'request_id': '8fd95b7d0f2a4e54a8a0e3fb7f7a51aa',
            },
            name='400 — noto‘g‘ri so‘rov',
        )
    if include_not_found:
        responses[404] = error_response(
            'Resurs topilmadi yoki joriy foydalanuvchiga ko‘rinmaydi.',
            {
                'code': 'not_found',
                'message': 'Not found.',
                'fields': {},
                'request_id': '8fd95b7d0f2a4e54a8a0e3fb7f7a51aa',
            },
            name='404 — topilmadi',
        )
    responses[409] = error_response(
        'Versiya yoki workflow konflikti.',
        {
            'code': 'stale_draft_version',
            'message': 'Draft boshqa versiyada yangilangan; uni qayta yuklang.',
            'fields': {},
            'request_id': '8fd95b7d0f2a4e54a8a0e3fb7f7a51aa',
        },
        name='409 — konflikt',
    )
    responses[415] = error_response(
        'Content-Type endpoint parseri tomonidan qo‘llab-quvvatlanmaydi.',
        {
            'code': 'unsupported_media_type',
            'message': 'Unsupported media type.',
            'fields': {},
            'request_id': '8fd95b7d0f2a4e54a8a0e3fb7f7a51aa',
        },
        name='415 — media turi qo‘llanmaydi',
    )
    return responses
