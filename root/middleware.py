import logging
import time
from uuid import uuid4


request_logger = logging.getLogger('root.request')


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid4().hex
        started_at = time.monotonic()
        try:
            response = self.get_response(request)
        except Exception as exc:
            self._log(
                request,
                started_at,
                status_code=500,
                error_class=type(exc).__name__,
            )
            raise
        response['X-Request-ID'] = request.request_id
        self._log(request, started_at, status_code=response.status_code)
        return response

    @staticmethod
    def _log(request, started_at, *, status_code, error_class=None):
        extra = {
            'request_id': request.request_id,
            'method': request.method,
            # Do not log query strings, body, headers, user data or cookies.
            'path': request.path,
            'status_code': status_code,
            'latency_ms': round((time.monotonic() - started_at) * 1000, 2),
        }
        if error_class:
            extra['error_class'] = error_class
            request_logger.error('http_request_error', extra=extra)
            return
        request_logger.info('http_request', extra=extra)
