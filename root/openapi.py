from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse


CSRF_HEADER = OpenApiParameter(
    name='X-CSRFToken',
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description='CSRF cookie bilan mos token. Session-auth POST/PATCH uchun kerak.',
)


def error_response(description, example, *, name='Xato javobi'):
    return OpenApiResponse(
        response=OpenApiTypes.OBJECT,
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
            {'detail': 'Authentication credentials were not provided.'},
            name='401 — autentifikatsiya',
        ),
        403: error_response(
            'Session, CSRF yoki foydalanuvchi huquqi yetarli emas.',
            {'detail': 'You do not have permission to perform this action.'},
            name='403 — ruxsat rad etildi',
        ),
    }
    if include_bad_request:
        responses[400] = error_response(
            'Body maydonlari noto‘g‘ri yoki biznes qoidasi bajarilmadi.',
            {'field': ['Bu maydon noto‘g‘ri.']},
            name='400 — noto‘g‘ri so‘rov',
        )
    if include_not_found:
        responses[404] = error_response(
            'Resurs topilmadi yoki joriy foydalanuvchiga ko‘rinmaydi.',
            {'detail': 'Not found.'},
            name='404 — topilmadi',
        )
    return responses
