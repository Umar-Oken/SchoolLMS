import hashlib

from django.core.cache import caches
from rest_framework.throttling import SimpleRateThrottle


class AccountCreateRateThrottle(SimpleRateThrottle):
    scope = 'account_create'

    def allow_request(self, request, view):
        self.cache = caches['abuse']
        try:
            return super().allow_request(request, view)
        except Exception:
            return True

    def get_cache_key(self, request, view):
        username = str(request.data.get('username', '')).strip().casefold()
        identity = f'{self.get_ident(request)}:{username}'
        identity_digest = hashlib.sha256(identity.encode()).hexdigest()
        return self.cache_format % {
            'scope': self.scope,
            'ident': identity_digest,
        }
