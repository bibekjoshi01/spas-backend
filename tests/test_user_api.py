"""Auth flow and permission gating for the user module."""

from rest_framework import status

from src.user.models import User, UserRole
from tests.base import INTERNAL, TenantAPITestCase

BASE = f"{INTERNAL}/user-mod"


class UserAPITestCase(TenantAPITestCase):
    @staticmethod
    def get_test_schema_name():
        return "test_user_api"

    @staticmethod
    def get_test_tenant_domain():
        return "user-api.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "User API College"
        tenant.subdomain = "user-api"
        return tenant


class AuthFlowTests(UserAPITestCase):
    def test_login_returns_tokens_profile_and_permissions(self):
        self.make_user("teacher1", "TEACHER")
        data = self.login("teacher1")

        assert set(data["tokens"]) == {"access", "refresh"}
        assert data["username"] == "teacher1"
        assert [role["codename"] for role in data["roles"]] == ["TEACHER"]
        # A teacher may record attendance but not create a program.
        assert "add_attendance" in data["permissions"]
        assert "add_program" not in data["permissions"]

    def test_login_by_email_also_works(self):
        self.make_user("teacher1", "TEACHER")
        data = self.login("teacher1@college.edu")
        assert data["username"] == "teacher1"

    def test_login_rejects_a_bad_password(self):
        self.make_user("teacher1", "TEACHER")
        response = self.client.post(
            f"{BASE}/account/login",
            {"persona": "teacher1", "password": "wrong"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_rejects_a_disabled_account(self):
        user = self.make_user("teacher1", "TEACHER")
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = self.client.post(
            f"{BASE}/account/login",
            {"persona": "teacher1", "password": self.password},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_superuser_holds_every_permission(self):
        data = self.authenticate("admin", self.password)
        assert "add_program" in data["permissions"]
        assert "delete_user" in data["permissions"]

    def test_me_returns_the_same_shape_as_login(self):
        self.make_user("head1", "DEPARTMENT-HEAD")
        login_data = self.authenticate("head1", self.password)

        response = self.client.get(f"{BASE}/account/me")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["permissions"] == login_data["permissions"]
        assert response.data["username"] == "head1"

    def test_me_requires_authentication(self):
        assert self.client.get(f"{BASE}/account/me").status_code == status.HTTP_401_UNAUTHORIZED

    def test_profile_update_recomposes_the_full_name(self):
        self.make_user("head1", "DEPARTMENT-HEAD")
        self.authenticate("head1", self.password)

        response = self.client.patch(
            f"{BASE}/account/me",
            {"firstName": "Sita", "lastName": "Sharma"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert User.objects.get(username="head1").full_name == "Sita Sharma"

    def test_change_password_then_sign_in_with_it(self):
        self.make_user("head1", "DEPARTMENT-HEAD")
        self.authenticate("head1", self.password)

        response = self.client.patch(f"{BASE}/account/me", {}, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = self.client.post(
            f"{BASE}/account/change-password",
            {"currentPassword": self.password, "newPassword": "BrandNew!2345"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        self.client.credentials()
        self.login("head1", "BrandNew!2345")

    def test_change_password_rejects_a_wrong_current_password(self):
        self.make_user("head1", "DEPARTMENT-HEAD")
        self.authenticate("head1", self.password)

        response = self.client.post(
            f"{BASE}/account/change-password",
            {"currentPassword": "nope", "newPassword": "BrandNew!2345"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_logout_blacklists_the_refresh_token(self):
        self.make_user("head1", "DEPARTMENT-HEAD")
        data = self.authenticate("head1", self.password)

        response = self.client.post(
            f"{BASE}/account/logout", {"refresh": data["tokens"]["refresh"]}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

        response = self.client.post(
            f"{BASE}/account/token/refresh",
            {"refresh": data["tokens"]["refresh"]},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class PermissionGatingTests(UserAPITestCase):
    def test_a_teacher_cannot_list_users(self):
        self.make_user("teacher1", "TEACHER")
        self.authenticate("teacher1", self.password)

        assert self.client.get(f"{BASE}/users").status_code == status.HTTP_403_FORBIDDEN

    def test_a_department_head_has_no_account_access(self):
        """
        Accounts and roles stay with the principal.

        A head adds teachers through the teacher endpoint, which creates the
        sign-in for them, so they never need rights that would expose every
        account in the college.
        """
        self.make_user("head1", "DEPARTMENT-HEAD")
        self.authenticate("head1")

        assert self.client.get(f"{BASE}/users").status_code == status.HTTP_403_FORBIDDEN
        assert self.client.get(f"{BASE}/roles").status_code == status.HTTP_403_FORBIDDEN

        response = self.client.post(
            f"{BASE}/users",
            {"username": "new1", "email": "new1@college.edu", "password": "NewPass!2345"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_superuser_can_create_a_user_with_roles(self):
        self.authenticate("admin", self.password)
        teacher_role = UserRole.objects.get(codename="TEACHER")

        response = self.client.post(
            f"{BASE}/users",
            {
                "username": "new1",
                "email": "new1@college.edu",
                "password": "NewPass!2345",
                "firstName": "Hari",
                "lastName": "Bhatta",
                "roles": [teacher_role.pk],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["message"] == "User created successfully."

        created = User.objects.get(username="new1")
        assert created.full_name == "Hari Bhatta"

        # SYSTEM-USER is attached to every internal account, not chosen.
        assert set(created.roles.values_list("codename", flat=True)) == {
            "TEACHER",
            "SYSTEM-USER",
        }

    def test_creating_a_user_rejects_a_weak_password(self):
        self.authenticate("admin", self.password)
        response = self.client.post(
            f"{BASE}/users",
            {"username": "new1", "email": "new1@college.edu", "password": "123"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data

    def test_creating_a_user_rejects_a_duplicate_email(self):
        self.authenticate("admin", self.password)
        self.make_user("taken")

        response = self.client.post(
            f"{BASE}/users",
            {"username": "other", "email": "taken@college.edu", "password": "NewPass!2345"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_delete_archives_rather_than_removes(self):
        self.authenticate("admin", self.password)
        victim = self.make_user("leaver")

        response = self.client.delete(f"{BASE}/users/{victim.pk}")
        assert response.status_code == status.HTTP_200_OK

        victim.refresh_from_db()
        assert victim.is_archived is True
        assert victim.is_active is False
        assert self.client.get(f"{BASE}/users/{victim.pk}").status_code == status.HTTP_404_NOT_FOUND

    def test_a_user_cannot_archive_themselves(self):
        self.authenticate("admin", self.password)
        response = self.client.delete(f"{BASE}/users/{self.admin.pk}")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_is_not_offered(self):
        self.authenticate("admin", self.password)
        response = self.client.put(f"{BASE}/users/{self.admin.pk}", {}, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_roles_and_permissions_are_readable_for_a_role_screen(self):
        self.authenticate("admin", self.password)

        roles = self.client.get(f"{BASE}/roles")
        assert roles.status_code == status.HTTP_200_OK
        codenames = {row["codename"] for row in roles.data["results"]}
        assert {"TEACHER", "DEPARTMENT-HEAD", "PROGRAM-COORDINATOR"} <= codenames

        catalogue = self.client.get(f"{BASE}/permissions")
        assert catalogue.status_code == status.HTTP_200_OK
        categories = {row["codename"] for row in catalogue.data}
        assert "ATTENDANCE_MANAGEMENT" in categories

    def test_internal_roles_survive_an_edit_that_omits_them(self):
        """The picker never offers them, so a save must not strip them."""
        self.authenticate_as_admin()
        teacher_role = UserRole.objects.get(codename="TEACHER")
        head_role = UserRole.objects.get(codename="DEPARTMENT-HEAD")

        response = self.client.post(
            f"{BASE}/users",
            {
                "username": "new1",
                "email": "new1@college.edu",
                "password": "NewPass!2345",
                "roles": [teacher_role.pk],
            },
            format="json",
        )
        created = User.objects.get(pk=response.data["id"])

        self.client.patch(f"{BASE}/users/{created.pk}", {"roles": [head_role.pk]}, format="json")

        assert set(created.roles.values_list("codename", flat=True)) == {
            "DEPARTMENT-HEAD",
            "SYSTEM-USER",
        }

    def test_the_role_picker_can_ask_for_assignable_roles_only(self):
        self.authenticate_as_admin()

        response = self.client.get(f"{BASE}/roles?assignable=true&limit=0")
        codenames = {row["codename"] for row in response.data["results"]}

        assert codenames == {"TEACHER", "PROGRAM-COORDINATOR", "DEPARTMENT-HEAD"}
        assert "SYSTEM-USER" not in codenames
        assert "STUDENT" not in codenames

    def test_public_user_role_is_gone(self):
        assert not UserRole.objects.filter(codename="PUBLIC-USER").exists()
