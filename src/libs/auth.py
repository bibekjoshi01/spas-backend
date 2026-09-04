from django.db import connection
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class PlatformJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        # optional: enforce platform-specific claims
        user_auth = super().authenticate(request)
        if not user_auth:
            return None

        user, token = user_auth

        if not getattr(user, "is_platform_admin", False):
            return None

        return (user, token)


class TenantJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        user_auth = super().authenticate(request)
        if not user_auth:
            return None

        user, token = user_auth
        if token.get("tenant_schema") != connection.schema_name:
            raise AuthenticationFailed("This session belongs to a different college.")
        return (user, token)


class MultiDomainAuthentication(BaseAuthentication):
    def authenticate(self, request):
        path = request.path

        if path.startswith("/api/v1/platform/"):
            return PlatformJWTAuthentication().authenticate(request)

        if path.startswith("/api/v1/internal/") or path.startswith("/api/v1/external/"):
            return TenantJWTAuthentication().authenticate(request)

        return None
