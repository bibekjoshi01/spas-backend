from django.core.cache import cache
from django_tenants.test.cases import TenantTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory

from src.api.external.views import TenantResolutionAPIView


class TenantResolutionAPITests(TenantTestCase):
    url = "/api/v1/external/tenant-resolution"

    def setUp(self):
        super().setUp()
        cache.clear()
        self.tenant.subdomain = "resolver-college"
        self.tenant.is_active = True
        self.tenant.save(update_fields=["subdomain", "is_active"])
        self.factory = APIRequestFactory()

    def resolve(self, subdomain):
        request = self.factory.get(self.url, {"subdomain": subdomain})
        return TenantResolutionAPIView.as_view()(request)

    def test_active_tenant_exists(self):
        response = self.resolve(self.tenant.subdomain)

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"exists": True}

    def test_unknown_tenant_does_not_exist(self):
        response = self.resolve("not-a-college")

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"exists": False}

    def test_suspended_tenant_does_not_exist(self):
        self.tenant.suspend()

        response = self.resolve(self.tenant.subdomain)

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"exists": False}

    def test_invalid_subdomain_does_not_exist(self):
        response = self.resolve("Not Valid!")

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"exists": False}
