from django.core.exceptions import ValidationError
from django_tenants.utils import get_public_schema_name
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from tenants.models import Tenant
from tenants.validators import subdomain_validator


class TenantResolutionThrottle(AnonRateThrottle):
    rate = "120/minute"


class TenantResolutionResponseSerializer(serializers.Serializer):
    exists = serializers.BooleanField()


class TenantResolutionAPIView(generics.GenericAPIView):
    """Resolve an active tenant without exposing tenant details."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (TenantResolutionThrottle,)
    serializer_class = TenantResolutionResponseSerializer

    @extend_schema(
        parameters=[OpenApiParameter(name="subdomain", required=True, type=str)],
        responses=TenantResolutionResponseSerializer,
    )
    def get(self, request):
        subdomain = request.query_params.get("subdomain", "").strip().lower()

        try:
            subdomain_validator(subdomain)
        except ValidationError:
            return Response({"exists": False})

        # The public schema is the platform control plane, never a college.
        exists = (
            Tenant.objects.filter(
                subdomain=subdomain,
                is_active=True,
            )
            .exclude(schema_name=get_public_schema_name())
            .exists()
        )
        return Response({"exists": exists})
