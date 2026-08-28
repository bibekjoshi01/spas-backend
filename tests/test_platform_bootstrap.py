"""The public control plane needs a tenant row and a host before it resolves."""

from io import StringIO

from django.contrib.auth.models import AnonymousUser
from django.core.management import CommandError, call_command
from django_tenants.test.cases import TenantTestCase
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIRequestFactory

from control_plane.models import PlatformUser
from control_plane.permissions import IsPlatformUser
from src.user.models import User
from tenants.models import Domain, Tenant


class BootstrapPlatformCommandTests(TenantTestCase):
    def bootstrap(self, **options):
        out = StringIO()
        with schema_context(get_public_schema_name()):
            call_command("bootstrap_platform", stdout=out, **options)
        return out.getvalue()

    def public_tenant(self):
        return Tenant.objects.filter(schema_name=get_public_schema_name()).first()

    def test_creates_public_tenant_and_domain(self):
        self.bootstrap(domain="platform.test")

        tenant = self.public_tenant()
        assert tenant is not None
        assert tenant.is_active is True
        assert Domain.objects.filter(
            domain="platform.test", tenant=tenant, is_primary=True
        ).exists()

    def test_is_idempotent(self):
        self.bootstrap(domain="platform.test")
        output = self.bootstrap(domain="platform.test")

        assert Tenant.objects.filter(schema_name=get_public_schema_name()).count() == 1
        assert Domain.objects.filter(domain="platform.test").count() == 1
        assert "exists" in output

    def test_reactivates_a_suspended_platform_tenant(self):
        self.bootstrap(domain="platform.test")
        tenant = self.public_tenant()

        # django-tenants only allows writing a tenant row from the public schema.
        with schema_context(get_public_schema_name()):
            tenant.suspend()

        self.bootstrap(domain="platform.test")

        tenant.refresh_from_db()
        assert tenant.is_active is True

    def test_refuses_a_host_owned_by_a_college(self):
        college_domain = self.tenant.get_primary_domain().domain

        with self.assertRaises(CommandError):
            self.bootstrap(domain=college_domain)

    def test_leaves_an_existing_college_domain_alone(self):
        self.bootstrap(domain="platform.test")

        college_domain = self.tenant.get_primary_domain()
        college_domain.refresh_from_db()
        assert college_domain.tenant_id == self.tenant.id


class IsPlatformUserTests(TenantTestCase):
    """The control-plane permission must fail closed, not default to allow."""

    def setUp(self):
        super().setUp()
        self.permission = IsPlatformUser()
        self.factory = APIRequestFactory()

    def allows(self, user):
        request = self.factory.get("/dashboard")
        request.user = user
        return self.permission.has_permission(request, view=None)

    def platform_user(self, **overrides):
        fields = {"username": "platform-admin", "is_active": True, "is_platform_admin": True}
        fields.update(overrides)
        return PlatformUser(**fields)

    def test_allows_an_active_platform_admin(self):
        assert self.allows(self.platform_user()) is True

    def test_rejects_a_platform_user_who_is_not_an_admin(self):
        assert self.allows(self.platform_user(is_platform_admin=False)) is False

    def test_rejects_an_inactive_platform_admin(self):
        assert self.allows(self.platform_user(is_active=False)) is False

    def test_rejects_an_anonymous_request(self):
        assert self.allows(AnonymousUser()) is False

    def test_rejects_a_college_user(self):
        """A college account has a username but no platform authority."""
        college_user = User.objects.create_user(
            username="teacher", email="teacher@college.edu", password="StaffPass!234"
        )

        assert self.allows(college_user) is False
