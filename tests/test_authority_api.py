"""
Row-level authority: who can see whose records.

Permissions decide what a user may do; authority decides which rows they may
do it to. These check the second, because a coordinator holding add_subject is
not licence to edit another department's curriculum.
"""

from rest_framework import status

from src.academics.models import Department, Program
from src.user.models import User, UserRole
from tests.base import INTERNAL, TenantAPITestCase

ACADEMICS = f"{INTERNAL}/academics-mod"
STUDENTS = f"{INTERNAL}/students-mod"
PERFORMANCE = f"{INTERNAL}/performance-mod"


class AuthorityTestCase(TenantAPITestCase):
    """Two departments, each with its own head, coordinator and records."""

    @staticmethod
    def get_test_schema_name():
        return "test_authority"

    @staticmethod
    def get_test_tenant_domain():
        return "authority.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Authority College"
        tenant.subdomain = "authority"
        return tenant

    def setUp(self):
        super().setUp()
        self.authenticate_as_admin()

        self.science = self.build_department("Computer Science", "CSIT", "BSCCSIT")
        self.management = self.build_department("Management", "MGMT", "BBA")

    def build_department(self, name, code, program_code):
        department = Department.objects.create(name=name, code=code, created_by=self.admin)

        head_user = self.make_user(f"{code.lower()}head", "DEPARTMENT-HEAD")
        department.head = head_user
        department.save(update_fields=["head"])

        coordinator_user = self.make_user(f"{code.lower()}coord", "PROGRAM-COORDINATOR")
        program = Program.objects.create(
            department=department,
            name=f"{name} Programme",
            code=program_code,
            coordinator=coordinator_user,
            created_by=self.admin,
        )

        return {
            "department": department,
            "program": program,
            "head_user": head_user,
            "coordinator_user": coordinator_user,
        }

    def as_user(self, user):
        self.client.credentials()
        return self.authenticate(user.username)


class DepartmentHeadAuthorityTests(AuthorityTestCase):
    def test_reassigning_a_department_moves_role_and_scope(self):
        replacement = User.objects.create_user(
            username="newhead",
            email="newhead@test.edu",
            password=self.password,
            created_by=self.admin,
        )
        previous = self.science["head_user"]

        response = self.client.patch(
            f"{ACADEMICS}/departments/{self.science['department'].id}",
            {"head": replacement.id},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert replacement.roles.filter(codename="DEPARTMENT-HEAD").exists()
        assert not previous.roles.filter(codename="DEPARTMENT-HEAD").exists()
        self.as_user(replacement)
        assert self.client.get(f"{ACADEMICS}/programs").data["count"] == 1

    def test_a_head_sees_only_their_own_department(self):
        self.as_user(self.science["head_user"])

        response = self.client.get(f"{ACADEMICS}/departments?limit=0")
        codes = [row["code"] for row in response.data["results"]]

        assert codes == ["CSIT"]

    def test_a_head_sees_only_their_own_programs(self):
        self.as_user(self.science["head_user"])

        response = self.client.get(f"{ACADEMICS}/programs?limit=0")
        codes = [row["code"] for row in response.data["results"]]

        assert codes == ["BSCCSIT"]

    def test_a_head_cannot_read_another_departments_program_directly(self):
        self.as_user(self.science["head_user"])
        other = self.management["program"]

        response = self.client.get(f"{ACADEMICS}/programs/{other.id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_head_cannot_edit_another_departments_program(self):
        self.as_user(self.science["head_user"])
        other = self.management["program"]

        response = self.client.patch(
            f"{ACADEMICS}/programs/{other.id}", {"name": "Hijacked"}, format="json"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        other.refresh_from_db()
        assert other.name != "Hijacked"

    def test_a_head_cannot_archive_another_departments_program(self):
        self.as_user(self.science["head_user"])
        other = self.management["program"]

        response = self.client.delete(f"{ACADEMICS}/programs/{other.id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        other.refresh_from_db()
        assert other.is_archived is False


class CoordinatorAuthorityTests(AuthorityTestCase):
    def test_a_coordinator_cannot_write_into_another_program_by_id(self):
        management_batch = self.client.post(
            f"{ACADEMICS}/batches",
            {"program": self.management["program"].id, "year": 2080},
            format="json",
        ).data["id"]
        self.as_user(self.science["coordinator_user"])

        subject = self.client.post(
            f"{ACADEMICS}/subjects",
            {
                "program": self.management["program"].id,
                "semester": 1,
                "code": "LEAK101",
                "name": "Out of scope",
            },
            format="json",
        )
        student = self.client.post(
            f"{STUDENTS}/students",
            {
                "batch": management_batch,
                "rollNumber": "77",
                "firstName": "Out",
                "lastName": "Of Scope",
            },
            format="json",
        )

        assert subject.status_code == status.HTTP_400_BAD_REQUEST
        assert student.status_code == status.HTTP_400_BAD_REQUEST
        assert "program" in str(subject.data).lower()
        assert "program" in str(student.data).lower()

    def test_reassigning_a_program_moves_role_and_scope(self):
        replacement = User.objects.create_user(
            username="newcoord",
            email="newcoord@test.edu",
            password=self.password,
            created_by=self.admin,
        )
        previous = self.science["coordinator_user"]
        self.as_user(self.science["head_user"])

        response = self.client.patch(
            f"{ACADEMICS}/programs/{self.science['program'].id}",
            {"coordinator": replacement.id},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert replacement.roles.filter(codename="PROGRAM-COORDINATOR").exists()
        assert not previous.roles.filter(codename="PROGRAM-COORDINATOR").exists()
        self.as_user(replacement)
        assert self.client.get(f"{ACADEMICS}/programs").data["count"] == 1

    def test_a_head_can_read_assignment_candidates_without_account_access(self):
        self.as_user(self.science["head_user"])

        candidates = self.client.get(f"{ACADEMICS}/programs/assignment-candidates")
        accounts = self.client.get(f"{INTERNAL}/user-mod/users")

        assert candidates.status_code == status.HTTP_200_OK
        assert accounts.status_code == status.HTTP_403_FORBIDDEN

    def test_only_a_superuser_can_write_batches(self):
        for user in (self.science["head_user"], self.science["coordinator_user"]):
            self.as_user(user)
            response = self.client.post(
                f"{ACADEMICS}/batches",
                {"program": self.science["program"].id, "year": 2081},
                format="json",
            )
            assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_coordinator_sees_only_their_own_program(self):
        self.as_user(self.science["coordinator_user"])

        response = self.client.get(f"{ACADEMICS}/programs?limit=0")
        codes = [row["code"] for row in response.data["results"]]

        assert codes == ["BSCCSIT"]

    def test_a_coordinator_cannot_create_a_program(self):
        self.as_user(self.science["coordinator_user"])

        response = self.client.post(
            f"{ACADEMICS}/programs",
            {"department": self.science["department"].id, "name": "New", "code": "NEW"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_head_can_create_a_program(self):
        self.as_user(self.science["head_user"])

        response = self.client.post(
            f"{ACADEMICS}/programs",
            {"department": self.science["department"].id, "name": "New", "code": "NEW"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_a_coordinators_students_stop_at_their_program(self):
        science_batch = self.client.post(
            f"{ACADEMICS}/batches",
            {"program": self.science["program"].id, "year": 2079},
            format="json",
        )
        assert science_batch.status_code == status.HTTP_201_CREATED

        self.authenticate_as_admin()
        management_batch = self.client.post(
            f"{ACADEMICS}/batches",
            {"program": self.management["program"].id, "year": 2079},
            format="json",
        ).data

        for batch, roll in ((science_batch.data["id"], "01"), (management_batch["id"], "99")):
            self.client.post(
                f"{STUDENTS}/students",
                {"batch": batch, "rollNumber": roll, "firstName": "A", "lastName": "B"},
                format="json",
            )

        self.as_user(self.science["coordinator_user"])
        # Asserted on the rendered body, where keys are camelCase.
        response = self.client.get(f"{STUDENTS}/students?limit=0")
        rolls = [row["rollNumber"] for row in response.json()["results"]]

        assert rolls == ["01"]


class NoAuthorityTests(AuthorityTestCase):
    def test_a_management_role_without_authority_sees_nothing(self):
        """Fails closed: holding the role is not the same as heading anything."""
        stranger = self.make_user("stranger", "DEPARTMENT-HEAD")
        self.as_user(stranger)

        assert self.client.get(f"{ACADEMICS}/departments").data["count"] == 0
        assert self.client.get(f"{ACADEMICS}/programs").data["count"] == 0
        assert self.client.get(f"{STUDENTS}/students").data["count"] == 0

    def test_a_superuser_is_not_scoped(self):
        response = self.client.get(f"{ACADEMICS}/departments?limit=0")
        codes = {row["code"] for row in response.data["results"]}

        assert codes == {"CSIT", "MGMT"}


class TeacherIsolationTests(AuthorityTestCase):
    """A teacher has no business on a management screen at all."""

    def setUp(self):
        super().setUp()
        self.teacher_user = self.make_user("plainteacher", "TEACHER")

    def test_a_teacher_is_refused_every_management_endpoint(self):
        self.as_user(self.teacher_user)

        for path in (
            f"{ACADEMICS}/departments",
            f"{ACADEMICS}/programs",
            f"{ACADEMICS}/batches",
            f"{ACADEMICS}/batch-semesters",
            f"{ACADEMICS}/subjects",
            f"{ACADEMICS}/allocations",
            f"{STUDENTS}/students",
            f"{STUDENTS}/semester-enrollments",
            f"{INTERNAL}/user-mod/users",
            f"{INTERNAL}/user-mod/roles",
        ):
            response = self.client.get(path)
            assert response.status_code == status.HTTP_403_FORBIDDEN, path

    def test_a_teacher_cannot_create_management_records(self):
        self.as_user(self.teacher_user)

        response = self.client.post(
            f"{ACADEMICS}/departments", {"name": "Rogue", "code": "RGE"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_teacher_keeps_their_own_workspace(self):
        data = self.as_user(self.teacher_user)

        assert "add_attendance" in data["permissions"]
        assert self.client.get(f"{PERFORMANCE}/analytics/classes").status_code == 200
        assert self.client.get(f"{PERFORMANCE}/analytics/overview").status_code == 200

    def test_a_management_role_has_no_workspace_verbs(self):
        data = self.as_user(self.science["head_user"])

        assert "add_attendance" not in data["permissions"]
        assert "add_internal_exam" not in data["permissions"]
        assert "add_assignment" not in data["permissions"]
        # Read access stays, so an oversight report can be built on it later.
        assert "view_attendance" in data["permissions"]


class RoleCatalogueTests(AuthorityTestCase):
    def test_internal_roles_are_not_offered_for_assignment(self):
        response = self.client.get(f"{INTERNAL}/user-mod/roles?assignable=true&limit=0")
        codenames = {row["codename"] for row in response.data["results"]}

        assert codenames == {"TEACHER", "PROGRAM-COORDINATOR", "DEPARTMENT-HEAD"}

    def test_every_signed_in_account_carries_the_system_role(self):
        user = User.objects.get(username="csithead")
        assert user.roles.filter(codename="SYSTEM-USER").exists()

        response = self.client.post(
            f"{INTERNAL}/user-mod/users",
            {"username": "fresh", "email": "fresh@x.edu", "password": "Fresh!2345"},
            format="json",
        )
        created = User.objects.get(pk=response.data["id"])
        assert created.roles.filter(
            codename=UserRole.objects.get(codename="SYSTEM-USER").codename
        ).exists()
