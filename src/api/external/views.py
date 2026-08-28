from django.core.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from tenants.models import Tenant
from tenants.validators import subdomain_validator


class TenantResolutionThrottle(AnonRateThrottle):
    rate = "120/minute"


class TenantResolutionAPIView(APIView):
    """Resolve an active tenant without exposing tenant details."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (TenantResolutionThrottle,)

    def get(self, request):
        subdomain = request.query_params.get("subdomain", "").strip().lower()

        try:
            subdomain_validator(subdomain)
        except ValidationError:
            return Response({"exists": False})

        exists = Tenant.objects.filter(
            subdomain=subdomain,
            is_active=True,
        ).exists()
        return Response({"exists": exists})
