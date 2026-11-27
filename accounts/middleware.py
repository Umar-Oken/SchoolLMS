import hashlib
import json

from django.core.cache import caches
from django.http import JsonResponse

from root.errors import error_payload


class LoginAbuseProtectionMiddleware:
    path = '/api/v1/auth/login/'
    limit = 5
    timeout = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST' and request.path == self.path:
            response = self._check_limit(request)
            if response is not None:
                return response
        return self.get_response(request)

    def _check_limit(self, request):
        try:
            username = self._username(request)
            identity = f'{request.META.get("REMOTE_ADDR", "")}:{username}'
            digest = hashlib.sha256(identity.encode()).hexdigest()
            key = f'login-abuse:{digest}'
            cache = caches['abuse']
            if cache.add(key, 1, timeout=self.timeout):
                return None
            attempts = cache.incr(key)
            if attempts <= self.limit:
                return None
        except Exception:
            return None

        response = JsonResponse(
            error_payload(
                code='login_throttled',
                message='Too many login attempts. Try again later.',
                request=request,
            ),
            status=429,
        )
        response['Retry-After'] = str(self.timeout)
        return response

    @staticmethod
    def _username(request):
        try:
            payload = json.loads(request.body or b'{}')
        except (TypeError, ValueError, UnicodeDecodeError):
            return ''
        return str(payload.get('username', '')).strip().casefold()
