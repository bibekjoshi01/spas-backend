"""Shared tenant API test scaffolding."""

import json
from pathlib import Path

from django.core import serializers
from django.core.cache import cache
from django.core.management import call_command
from django_tenants.test.cases import TenantTestCase
from rest_framework import status
from rest_framework.test import APIClient

from src.user.models import User, UserRole

INTERNAL = "/api/v1/internal"


class TenantAPIClient(APIClient):
    """An APIClient that addresses the test tenant's domain."""

    def __init__(self, tenant, **kwargs):
        super().__init__(**kwargs)
        self.defaults["HTTP_HOST"] = tenant.get_primary_domain().domain


class TenantAPITestCase(TenantTestCase):
    """A tenant schema with the shipped roles and permissions loaded."""

    password = "StaffPass!234"

    def setUp(self):
        super().setUp()
        # Login is throttled per client; a shared bucket would make test order
        # significant.
        cache.clear()
        self.client = TenantAPIClient(self.tenant)
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@college.edu", password=self.password
        )
        self.load_seed_data()

    def load_seed_data(self):
        """
        Load the shipped permission fixtures.

        The role fixture hardcodes created_by=1 because a real deployment loads
        it straight after creating the first superuser. Tests roll back but do
        not reset sequences, so the acting user is rebound here.
        """
        call_command(
            "loaddata",
            "main_module.json",
            "permission_category.json",
            "permissions.json",
            verbosity=0,
        )

        roles = json.loads(Path("src/user/fixtures/user_role.json").read_text())
        for row in roles:
            row["fields"]["created_by"] = self.admin.pk

        for obj in serializers.deserialize("json", json.dumps(roles)):
            obj.save()

    def make_user(self, username, role_codename=None):
        user = User.objects.create_user(
            username=username, email=f"{username}@college.edu", password=self.password
        )
        if role_codename:
            user.roles.add(UserRole.objects.get(codename=role_codename))
        return user

    def login(self, persona, password=None):
        response = self.client.post(
            f"{INTERNAL}/user-mod/account/login",
            {"persona": persona, "password": password or self.password},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.data

    def authenticate(self, persona, password=None):
        data = self.login(persona, password)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['tokens']['access']}")
        return data

    def authenticate_as_admin(self):
        return self.authenticate("admin")
