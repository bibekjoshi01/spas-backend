"""Academics endpoints, permission gating and teacher-scoped visibility."""

from rest_framework import status

from src.academics.models import Department, Program, Subject
from tests.base import INTERNAL, TenantAPITestCase

BASE = f"{INTERNAL}/academics-mod"


class AcademicsAPITestCase(TenantAPITestCase):
    @staticmethod
    def get_test_schema_name():
        return "test_academics_api"

    @staticmethod
    def get_test_tenant_domain():
        return "academics-api.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Academics API College"
        tenant.subdomain = "academics-api"
        return tenant

    def seed_structure(self):
        """Department -> program -> batch -> semester -> subject -> allocation."""
        self.authenticate_as_admin()

        department = self.post(f"{BASE}/departments", {"name": "Computer Science", "code": "CSIT"})
        program = self.post(
            f"{BASE}/programs",
            {
                "department": department,
                "name": "B.Sc. CSIT",
                "code": "BSCCSIT",
                "totalSemesters": 8,
            },
        )
        batch = self.post(f"{BASE}/batches", {"program": program, "year": 2079})
        semester = self.post(
            f"{BASE}/batch-semesters",
            {"batch": batch, "semester": 3, "status": "RUNNING"},
        )
        subject = self.post(
            f"{BASE}/subjects",
            {"program": program, "semester": 3, "code": "CSC201", "name": "Data Structures"},
        )
        return {
            "department": department,
            "program": program,
            "batch": batch,
            "semester": semester,
            "subject": subject,
        }

    def post(self, url, payload):
        response = self.client.post(url, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED, response.data
        return response.data["id"]

    def make_teacher(self, username, department_id):
        user = self.make_user(username, "TEACHER")
        return user, self.post(
            f"{BASE}/teachers",
            {"user": user.pk, "department": department_id, "designation": "LECTURER"},
        )


class StructureTests(AcademicsAPITestCase):
    def test_a_coordinator_can_build_the_whole_structure(self):
        ids = self.seed_structure()
        assert Department.objects.count() == 1
        assert Program.objects.get(pk=ids["program"]).department_id == ids["department"]
        assert Subject.objects.get(pk=ids["subject"]).semester == 3

    def test_list_rows_carry_nested_names_for_the_table(self):
        """Asserted on the rendered body, which is what the frontend receives."""
        ids = self.seed_structure()
        response = self.client.get(f"{BASE}/batches")

        assert response.status_code == status.HTTP_200_OK
        row = response.json()["results"][0]
        assert row["program"]["code"] == "BSCCSIT"
        assert row["studentCount"] == 0  # camelCase on the wire
        assert row["id"] == ids["batch"]

    def test_a_subject_cannot_be_allocated_to_the_wrong_semester(self):
        ids = self.seed_structure()
        wrong = self.post(
            f"{BASE}/subjects",
            {"program": ids["program"], "semester": 5, "code": "CSC501", "name": "AI"},
        )
        _, teacher = self.make_teacher("teacher1", ids["department"])

        response = self.client.post(
            f"{BASE}/allocations",
            {"batchSemester": ids["semester"], "subject": wrong, "teacher": teacher},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_batch_cannot_run_two_semesters_at_once(self):
        ids = self.seed_structure()
        response = self.client.post(
            f"{BASE}/batch-semesters",
            {"batch": ids["batch"], "semester": 4, "status": "RUNNING"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_archives_and_removes_the_row_from_listings(self):
        ids = self.seed_structure()

        response = self.client.delete(f"{BASE}/subjects/{ids['subject']}")
        assert response.status_code == status.HTTP_200_OK

        assert Subject.objects.get(pk=ids["subject"]).is_archived is True
        listing = self.client.get(f"{BASE}/subjects")
        assert listing.data["count"] == 0

    def test_archiving_frees_the_code_for_reuse(self):
        ids = self.seed_structure()
        self.client.delete(f"{BASE}/subjects/{ids['subject']}")

        response = self.client.post(
            f"{BASE}/subjects",
            {
                "program": ids["program"],
                "semester": 3,
                "code": "CSC201",
                "name": "Data Structures (revised)",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_filters_and_search_are_available_on_listings(self):
        ids = self.seed_structure()
        self.post(
            f"{BASE}/subjects",
            {"program": ids["program"], "semester": 4, "code": "CSC301", "name": "Networks"},
        )

        assert self.client.get(f"{BASE}/subjects?semester=3").data["count"] == 1
        assert self.client.get(f"{BASE}/subjects?search=Networks").data["count"] == 1


class TeacherVisibilityTests(AcademicsAPITestCase):
    def test_a_teacher_cannot_read_the_allocation_listing(self):
        ids = self.seed_structure()
        _, teacher_a = self.make_teacher("teacher1", ids["department"])
        user_b, teacher_b = self.make_teacher("teacher2", ids["department"])

        second_subject = self.post(
            f"{BASE}/subjects",
            {"program": ids["program"], "semester": 3, "code": "CSC202", "name": "DBMS"},
        )
        self.post(
            f"{BASE}/allocations",
            {"batchSemester": ids["semester"], "subject": ids["subject"], "teacher": teacher_a},
        )
        self.post(
            f"{BASE}/allocations",
            {"batchSemester": ids["semester"], "subject": second_subject, "teacher": teacher_b},
        )

        # The admin, who may allocate, sees both.
        assert self.client.get(f"{BASE}/allocations").data["count"] == 2

        # A teacher has no management access at all; their classes reach them
        # through the workspace, not through the allocation listing.
        self.client.credentials()
        self.authenticate(user_b.username)

        assert self.client.get(f"{BASE}/allocations").status_code == status.HTTP_403_FORBIDDEN

    def test_a_teacher_cannot_create_an_allocation(self):
        ids = self.seed_structure()
        user, teacher = self.make_teacher("teacher1", ids["department"])

        self.client.credentials()
        self.authenticate(user.username)

        response = self.client.post(
            f"{BASE}/allocations",
            {"batchSemester": ids["semester"], "subject": ids["subject"], "teacher": teacher},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_teacher_has_no_access_to_the_curriculum(self):
        """
        Teaching and managing are separate surfaces.

        A teacher records attendance and marks; they do not read or edit the
        curriculum, so the academics module is closed to them entirely.
        """
        ids = self.seed_structure()
        user, _ = self.make_teacher("teacher1", ids["department"])

        self.client.credentials()
        self.authenticate(user.username)

        assert self.client.get(f"{BASE}/subjects").status_code == status.HTTP_403_FORBIDDEN
        assert (
            self.client.post(
                f"{BASE}/subjects",
                {"program": ids["program"], "semester": 2, "code": "X", "name": "Y"},
                format="json",
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )

    def test_an_unauthenticated_caller_is_refused(self):
        self.client.credentials()
        assert self.client.get(f"{BASE}/departments").status_code == status.HTTP_401_UNAUTHORIZED
