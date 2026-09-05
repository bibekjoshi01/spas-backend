"""Regression cases found during the UI, permission and integrity QA audit."""

from src.academics.models import Program
from src.students.models import SemesterEnrollment, SubjectEnrollment
from src.user.models import UserRole
from tests.test_performance_api import ACADEMICS, PERFORMANCE, STUDENTS, WorkflowTestCase


class WebsiteQARegressionTests(WorkflowTestCase):
    def test_bulk_enrollment_handles_duplicate_ids_and_retries(self):
        for endpoint, target, model in (
            ("semester-enrollments", {"batchSemester": self.semester}, SemesterEnrollment),
            ("subject-enrollments", {"allocation": self.allocation}, SubjectEnrollment),
        ):
            payload = {**target, "students": [self.students[0], self.students[0]]}
            first = self.client.post(f"{STUDENTS}/{endpoint}/bulk", payload, format="json")
            assert first.status_code == 201, first.data
            assert first.data["created"] == 1
            retry = self.client.post(f"{STUDENTS}/{endpoint}/bulk", payload, format="json")
            assert retry.status_code == 201, retry.data
            assert retry.data["created"] == 0
            assert model.objects.count() == 1
            assert model.objects.get().history.first().history_user == self.admin

    def test_enrollment_foreign_keys_do_not_disclose_other_program_students(self):
        other_program = self.post(
            f"{ACADEMICS}/programs",
            {"department": self.department, "name": "Other", "code": "OTHER"},
        )["id"]
        other_batch = self.post(f"{ACADEMICS}/batches", {"program": other_program, "year": 2080})[
            "id"
        ]
        outsider = self.post(
            f"{STUDENTS}/students",
            {
                "batch": other_batch,
                "rollNumber": "SECRET-ROLL",
                "firstName": "Private",
                "lastName": "Student",
            },
        )["id"]
        coordinator = self.make_user("qa-coordinator", "PROGRAM-COORDINATOR")
        program = Program.objects.get(pk=self.program)
        program.coordinator = coordinator
        program.save()
        self.authenticate(coordinator.username)
        for endpoint, target in (
            ("semester-enrollments", {"batchSemester": self.semester}),
            ("subject-enrollments", {"allocation": self.allocation}),
        ):
            for suffix, student_key in (("/bulk", "students"), ("", "student")):
                response = self.client.post(
                    f"{STUDENTS}/{endpoint}{suffix}",
                    {**target, student_key: [outsider] if suffix else outsider},
                    format="json",
                )
                assert response.status_code == 400, response.data
                assert student_key in response.json()
                assert "SECRET-ROLL" not in response.content.decode()
                assert "Private" not in response.content.decode()
        assert not SemesterEnrollment.objects.exists()
        assert not SubjectEnrollment.objects.exists()

    def test_impossible_audit_dates_are_field_errors(self):
        for query in (
            {"from": "2026-02-30"},
            {"to": "2026-13-01"},
            {"from": "2026-09-05", "to": "2026-09-01"},
        ):
            response = self.client.get(
                "/api/v1/internal/audit-mod/trail", {"resource": "subject-allocation", **query}
            )
            assert response.status_code == 400, response.data

    def test_impossible_attendance_report_dates_are_validation_errors(self):
        response = self.client.get(
            f"{PERFORMANCE}/analytics/management-attendance-report",
            {"start_date": "2026-02-30", "end_date": "2026-03-01"},
        )
        assert response.status_code == 400, response.data

    def test_inactive_role_does_not_grant_permissions(self):
        self.as_teacher()
        role = UserRole.objects.get(codename="TEACHER")
        role.is_active = False
        role.save()
        response = self.client.get(f"{PERFORMANCE}/attendance-sessions")
        assert response.status_code == 403, response.data
        profile = self.client.get("/api/v1/internal/user-mod/account/me").json()
        assert "TEACHER" not in {role["codename"] for role in profile["roles"]}
        role.is_active = True
        role.is_archived = True
        role.save()
        assert self.client.get(f"{PERFORMANCE}/attendance-sessions").status_code == 403
        role.is_archived = False
        role.is_active = True
        role.save()
        assert self.client.get(f"{PERFORMANCE}/attendance-sessions").status_code == 200
