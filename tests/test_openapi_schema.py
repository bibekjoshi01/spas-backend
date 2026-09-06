"""The API contract must show authentication on protected tenant endpoints."""

from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator


class TenantOpenAPITests(SimpleTestCase):
    def test_tenant_endpoints_document_bearer_authentication(self):
        schema = SchemaGenerator(urlconf="config.tenant_urls").get_schema(public=True)
        scheme = schema["components"]["securitySchemes"]["TenantJWTAuth"]
        assert scheme == {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        paths = schema["paths"]
        for path in (
            "/api/v1/internal/user-mod/users",
            "/api/v1/internal/performance-mod/analytics/overview",
            "/api/v1/internal/students-mod/students",
        ):
            assert {"TenantJWTAuth": []} in paths[path]["get"]["security"]
            assert {} not in paths[path]["get"]["security"]
        assert {} in paths["/api/v1/internal/user-mod/account/login"]["post"]["security"]
