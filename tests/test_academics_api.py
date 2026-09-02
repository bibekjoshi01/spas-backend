"""Academics endpoints, permission gating and teacher-scoped visibility."""

from rest_framework import status

from src.academics.models import Batch, Department, Program, Subject, SubjectAllocation
from src.performance.models import AttendanceSession
from src.user.models import User
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
        return user, user.pk


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

    def test_an_unused_allocation_can_be_changed(self):
        ids = self.seed_structure()
        _, original_teacher = self.make_teacher("teacher1", ids["department"])
        _, replacement_teacher = self.make_teacher("teacher2", ids["department"])
        replacement_subject = self.post(
            f"{BASE}/subjects",
            {
                "program": ids["program"],
                "semester": 3,
                "code": "CSC202",
                "name": "Database Systems",
            },
        )
        allocation = self.post(
            f"{BASE}/allocations",
            {
                "batchSemester": ids["semester"],
                "subject": ids["subject"],
                "teacher": original_teacher,
            },
        )

        response = self.client.patch(
            f"{BASE}/allocations/{allocation}",
            {"teacher": replacement_teacher, "subject": replacement_subject},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        row = self.client.get(f"{BASE}/allocations/{allocation}").data
        assert row["teacher"]["id"] == replacement_teacher
        assert row["subject"]["id"] == replacement_subject

    def test_allocation_class_time_is_returned_and_must_be_a_valid_range(self):
        ids = self.seed_structure()
        _, teacher = self.make_teacher("scheduled-teacher", ids["department"])
        allocation = self.post(
            f"{BASE}/allocations",
            {
                "batchSemester": ids["semester"],
                "subject": ids["subject"],
                "teacher": teacher,
                "startTime": "10:15",
                "endTime": "11:00",
            },
        )

        row = self.client.get(f"{BASE}/allocations/{allocation}").data
        assert row["start_time"] == "10:15:00"
        assert row["end_time"] == "11:00:00"

        response = self.client.patch(
            f"{BASE}/allocations/{allocation}",
            {"startTime": "12:00", "endTime": "11:00"},
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

    def test_allocations_of_an_archived_batch_leave_active_listings(self):
        """
        The safety net, for rows archived before the leaf rule existed.

        The API now refuses to archive a batch that still carries a semester, so
        this archives the row directly to reproduce a legacy orphan and asserts
        the listing still hides what hangs off it.
        """
        ids = self.seed_structure()
        _, teacher = self.make_teacher("teacher1", ids["department"])
        self.post(
            f"{BASE}/allocations",
            {
                "batchSemester": ids["semester"],
                "subject": ids["subject"],
                "teacher": teacher,
            },
        )

        Batch.objects.filter(pk=ids["batch"]).update(is_archived=True)

        assert self.client.get(f"{BASE}/allocations").data["count"] == 0
        assert self.client.get(f"{BASE}/batch-semesters").data["count"] == 0

    def test_a_legacy_invalid_allocation_can_still_be_archived(self):
        ids = self.seed_structure()
        _, teacher = self.make_teacher("teacher1", ids["department"])
        allocation = self.post(
            f"{BASE}/allocations",
            {
                "batchSemester": ids["semester"],
                "subject": ids["subject"],
                "teacher": teacher,
            },
        )
        wrong_subject = self.post(
            f"{BASE}/subjects",
            {
                "program": ids["program"],
                "semester": 4,
                "code": "CSC401",
                "name": "Legacy mismatch",
            },
        )
        SubjectAllocation.objects.filter(pk=allocation).update(subject_id=wrong_subject)

        response = self.client.delete(f"{BASE}/allocations/{allocation}")

        assert response.status_code == status.HTTP_200_OK
        assert SubjectAllocation.objects.get(pk=allocation).is_archived is True

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


class DeactivationTests(AcademicsAPITestCase):
    """Switching a row off retires it from every picker, and from writes."""

    def deactivate(self, collection, row_id):
        response = self.client.patch(
            f"{BASE}/{collection}/{row_id}", {"isActive": False}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.data

    def test_an_inactive_row_leaves_the_active_listing_but_keeps_its_own(self):
        ids = self.seed_structure()
        self.deactivate("programs", ids["program"])

        assert self.client.get(f"{BASE}/programs?is_active=true").data["count"] == 0
        # Still listed unfiltered, or there would be no way to switch it back on.
        assert self.client.get(f"{BASE}/programs").data["count"] == 1

    def test_a_program_cannot_be_created_under_an_inactive_department(self):
        ids = self.seed_structure()
        self.deactivate("departments", ids["department"])

        response = self.client.post(
            f"{BASE}/programs",
            {"department": ids["department"], "name": "B.Sc. Physics", "code": "BSCPHY"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "inactive" in str(response.data["department"]).lower()

    def test_a_subject_and_batch_cannot_be_created_under_an_inactive_program(self):
        ids = self.seed_structure()
        self.deactivate("programs", ids["program"])

        subject = self.client.post(
            f"{BASE}/subjects",
            {"program": ids["program"], "semester": 3, "code": "CSC202", "name": "Algorithms"},
            format="json",
        )
        batch = self.client.post(
            f"{BASE}/batches", {"program": ids["program"], "year": 2080}, format="json"
        )

        assert subject.status_code == status.HTTP_400_BAD_REQUEST
        assert batch.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_inactive_subject_cannot_be_allocated(self):
        ids = self.seed_structure()
        _, teacher = self.make_teacher("teacher1", ids["department"])
        self.deactivate("subjects", ids["subject"])

        response = self.client.post(
            f"{BASE}/allocations",
            {"batchSemester": ids["semester"], "subject": ids["subject"], "teacher": teacher},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_inactive_batch_cannot_receive_a_new_semester(self):
        ids = self.seed_structure()
        self.deactivate("batches", ids["batch"])

        response = self.client.post(
            f"{BASE}/batch-semesters",
            {"batch": ids["batch"], "semester": 4, "status": "UPCOMING"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "inactive" in str(response.data["batch"]).lower()

    def test_an_inactive_semester_or_teacher_cannot_receive_an_allocation(self):
        ids = self.seed_structure()
        _, teacher_id = self.make_teacher("teacher1", ids["department"])
        self.deactivate("batch-semesters", ids["semester"])

        inactive_semester = self.client.post(
            f"{BASE}/allocations",
            {
                "batchSemester": ids["semester"],
                "subject": ids["subject"],
                "teacher": teacher_id,
            },
            format="json",
        )
        teacher = User.objects.get(pk=teacher_id)
        teacher.is_active = False
        teacher.save(update_fields=["is_active"])
        self.client.patch(
            f"{BASE}/batch-semesters/{ids['semester']}",
            {"isActive": True},
            format="json",
        )
        inactive_teacher = self.client.post(
            f"{BASE}/allocations",
            {
                "batchSemester": ids["semester"],
                "subject": ids["subject"],
                "teacher": teacher_id,
            },
            format="json",
        )

        assert inactive_semester.status_code == status.HTTP_400_BAD_REQUEST
        assert inactive_teacher.status_code == status.HTTP_400_BAD_REQUEST

    def test_reactivating_restores_the_row_to_the_pickers(self):
        ids = self.seed_structure()
        self.deactivate("subjects", ids["subject"])
        response = self.client.patch(
            f"{BASE}/subjects/{ids['subject']}", {"isActive": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert self.client.get(f"{BASE}/subjects?is_active=true").data["count"] == 1

    def test_an_existing_row_under_an_inactive_parent_stays_editable(self):
        """
        Retiring a department must not strand the programs already under it.

        The edit form resubmits the department it loaded, so the unchanged
        inactive department has to be accepted or the program becomes unsavable.
        """
        ids = self.seed_structure()
        self.deactivate("departments", ids["department"])

        response = self.client.patch(
            f"{BASE}/programs/{ids['program']}",
            {"name": "B.Sc. CSIT (revised)", "department": ids["department"]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

    def test_a_program_cannot_be_moved_into_an_inactive_department(self):
        ids = self.seed_structure()
        retired = self.post(f"{BASE}/departments", {"name": "Management", "code": "MGMT"})
        self.deactivate("departments", retired)

        response = self.client.patch(
            f"{BASE}/programs/{ids['program']}", {"department": retired}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "inactive" in str(response.data["department"]).lower()


class ArchiveGuardTests(AcademicsAPITestCase):
    """Archiving is removal, so it is offered only where nothing depends on it."""

    def test_a_department_with_programs_cannot_be_archived(self):
        ids = self.seed_structure()

        response = self.client.delete(f"{BASE}/departments/{ids['department']}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        message = str(response.data)
        assert "1 program" in message
        # The message has to name the way out, or the reader is simply stuck.
        assert "deactivate" in message.lower()
        assert Department.objects.get(pk=ids["department"]).is_archived is False

    def test_a_program_reports_every_kind_of_blocker_at_once(self):
        ids = self.seed_structure()
        self.post(
            f"{BASE}/subjects",
            {"program": ids["program"], "semester": 4, "code": "CSC301", "name": "Networks"},
        )

        response = self.client.delete(f"{BASE}/programs/{ids['program']}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Two subjects and the batch, so the reader fixes both in one pass.
        assert "2 subjects" in str(response.data)
        assert "1 batch" in str(response.data)

    def test_an_allocation_carrying_attendance_cannot_be_archived(self):
        ids = self.seed_structure()
        _, teacher = self.make_teacher("teacher1", ids["department"])
        allocation = self.post(
            f"{BASE}/allocations",
            {"batchSemester": ids["semester"], "subject": ids["subject"], "teacher": teacher},
        )
        AttendanceSession.objects.create(
            allocation_id=allocation,
            date="2025-01-06",
            period=1,
            created_by=self.admin,
        )

        response = self.client.delete(f"{BASE}/allocations/{allocation}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "attendance session" in str(response.data)

    def test_a_leaf_still_archives(self):
        """The rule narrows archiving, it does not remove it."""
        ids = self.seed_structure()

        response = self.client.delete(f"{BASE}/subjects/{ids['subject']}")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert Subject.objects.get(pk=ids["subject"]).is_archived is True

    def test_clearing_the_children_unblocks_the_parent(self):
        """Bottom-up archiving is the supported path, so it has to work."""
        ids = self.seed_structure()

        assert self.client.delete(f"{BASE}/subjects/{ids['subject']}").status_code == 200
        assert self.client.delete(f"{BASE}/batch-semesters/{ids['semester']}").status_code == 200
        assert self.client.delete(f"{BASE}/batches/{ids['batch']}").status_code == 200
        assert self.client.delete(f"{BASE}/programs/{ids['program']}").status_code == 200

        response = self.client.delete(f"{BASE}/departments/{ids['department']}")
        assert response.status_code == status.HTTP_200_OK, response.data

    def test_children_of_an_archived_parent_leave_every_listing(self):
        """The net under the guard, for orphans that predate it."""
        ids = self.seed_structure()
        Department.objects.filter(pk=ids["department"]).update(is_archived=True)

        for collection in ("programs", "batches", "batch-semesters", "subjects"):
            assert self.client.get(f"{BASE}/{collection}").data["count"] == 0, collection

    def test_an_orphaned_row_is_not_addressable_either(self):
        """Hidden from the listing but editable by id would be the worse bug."""
        ids = self.seed_structure()
        Department.objects.filter(pk=ids["department"]).update(is_archived=True)

        response = self.client.patch(
            f"{BASE}/programs/{ids['program']}", {"name": "Renamed"}, format="json"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


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
