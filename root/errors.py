from django.http import JsonResponse
from rest_framework import exceptions, status
from rest_framework.views import exception_handler as drf_exception_handler


def _request_id(request):
    return getattr(request, 'request_id', '')


def error_payload(*, code, message, request, fields=None):
    return {
        'code': code,
        'message': message,
        'fields': fields or {},
        'request_id': _request_id(request),
    }


def _plain_details(value):
    if isinstance(value, dict):
        return {key: _plain_details(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_details(item) for item in value]
    return str(value) if not isinstance(value, str) else value


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    request = context.get('request')
    if response is None:
        return_response = JsonResponse(
            error_payload(
                code='internal_error',
                message='Internal server error.',
                request=request,
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        return return_response

    if isinstance(exc, exceptions.ValidationError):
        code = 'validation_error'
        message = 'Validation failed.'
        fields = _plain_details(exc.get_full_details())
    else:
        codes = exc.get_codes() if isinstance(exc, exceptions.APIException) else None
        code = (
            codes
            if isinstance(codes, str)
            else codes.get('detail', 'api_error')
            if isinstance(codes, dict)
            else {
                status.HTTP_400_BAD_REQUEST: 'bad_request',
                status.HTTP_403_FORBIDDEN: 'permission_denied',
                status.HTTP_404_NOT_FOUND: 'not_found',
                status.HTTP_409_CONFLICT: 'conflict',
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: 'unsupported_media_type',
            }.get(response.status_code, 'api_error')
        )
        detail = getattr(exc, 'detail', None)
        message = str(detail) if isinstance(detail, str) else 'Request failed.'
        fields = {}

    response.data = error_payload(
        code=code,
        message=message,
        fields=fields,
        request=request,
    )
    return response


def csrf_failure(request, reason=''):
    return JsonResponse(
        error_payload(
            code='csrf_failed',
            message='CSRF verification failed.',
            request=request,
        ),
        status=status.HTTP_403_FORBIDDEN,
    )


def bad_request(request, exception=None):
    return _django_error(request, status.HTTP_400_BAD_REQUEST, 'bad_request')


def permission_denied(request, exception=None):
    return _django_error(request, status.HTTP_403_FORBIDDEN, 'permission_denied')


def not_found(request, exception=None):
    return _django_error(request, status.HTTP_404_NOT_FOUND, 'not_found')


def server_error(request):
    return _django_error(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        'internal_error',
    )


def _django_error(request, response_status, code):
    messages = {
        'bad_request': 'Bad request.',
        'permission_denied': 'Permission denied.',
        'not_found': 'Not found.',
        'internal_error': 'Internal server error.',
    }
    return JsonResponse(
        error_payload(
            code=code,
            message=messages[code],
            request=request,
        ),
        status=response_status,
    )
