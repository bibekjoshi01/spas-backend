"""The read-only audit trail: what it reports, and who it reports it to."""

from rest_framework import status

from src.performance.models import AttendanceRecord
from src.students.models import SubjectEnrollment
from tests.test_performance_api import (
    ACADEMICS,
    INTERNAL,
    PERFORMANCE,
    STUDENTS,
    WorkflowTestCase,
)

AUDIT = f"{INTERNAL}/audit-mod"


class AuditTrailTests(WorkflowTestCase):
    @staticmethod
    def get_test_schema_name():
        return "test_audit_api"

    @staticmethod
    def get_test_tenant_domain():
        return "audit-api.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Audit College"
        tenant.subdomain = "audit"
        return tenant

    def record_day(self, enrollments, date, statuses):
        """One held class, so there is a mark with a history to read."""
        return self.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": date,
                "entries": [
                    {"enrollment": enrollment, "status": statuses[index]}
                    for index, enrollment in enumerate(enrollments)
                ],
            },
        )

    def trail(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        response = self.client.get(f"{AUDIT}/trail?{query}")
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.json()

    # What it reports
    # --------------------------------------------------------------------------------

    def test_a_mark_carries_who_changed_it_and_from_what_to_what(self):
        """The question an affiliating university asks during an audit."""
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-12", ["PRESENT", "PRESENT", "PRESENT"])
        record = AttendanceRecord.objects.filter(enrollment=enrollments[0]).first()

        self.record_day(enrollments, "2026-01-12", ["ABSENT", "PRESENT", "PRESENT"])

        body = self.trail(resource="attendance-record", object=record.pk)
        latest = body["results"][0]

        assert latest["action"] == "UPDATED"
        assert latest["actor"]["fullName"]
        assert "Student1" in latest["objectLabel"]
        assert {
            "field": "status",
            "label": "Status",
            "from": "Present",
            "to": "Absent",
        } in latest["changes"]

    def test_a_create_reports_itself_rather_than_every_default(self):
        """
        Listing every field of a new row would bury the ones that matter.

        The entry says a record was created, by whom and when; the values are
        the record itself, which the reader already has.
        """
        body = self.trail(resource="subject-allocation", object=self.allocation)
        oldest = body["results"][-1]

        assert oldest["action"] == "CREATED"
        assert oldest["changes"] == []

    def test_bookkeeping_fields_are_never_reported_as_changes(self):
        """`updated_at` moves on every save and tells a reader nothing."""
        self.client.patch(
            f"{ACADEMICS}/allocations/{self.allocation}",
            {"teacher": self.teacher_user.pk, "isActive": True},
            format="json",
        )

        body = self.trail(resource="subject-allocation", object=self.allocation)
        reported = {change["field"] for row in body["results"] for change in row["changes"]}

        assert not reported & {"updated_at", "updated_by", "created_at", "created_by", "uuid"}

    def test_a_foreign_key_change_reads_as_a_name(self):
        self.client.credentials()
        self.authenticate_as_admin()
        replacement = self.make_user("teacher2", "TEACHER")
        self.client.patch(
            f"{ACADEMICS}/allocations/{self.allocation}",
            {"teacher": replacement.pk},
            format="json",
        )

        body = self.trail(resource="subject-allocation", object=self.allocation)
        change = next(
            change
            for row in body["results"]
            for change in row["changes"]
            if change["field"] == "teacher"
        )

        # A raw id would be unreadable in the one place readability matters.
        assert str(replacement.pk) != change["to"]
        assert change["to"]

    def test_entries_come_back_newest_first_and_paginate(self):
        self.client.credentials()
        self.authenticate_as_admin()
        for _ in range(3):
            self.client.patch(
                f"{ACADEMICS}/allocations/{self.allocation}",
                {"teacher": self.teacher_user.pk},
                format="json",
            )

        body = self.trail(resource="subject-allocation", limit=2)
        stamps = [row["at"] for row in body["results"]]

        assert body["count"] >= 2
        assert len(body["results"]) == 2
        assert stamps == sorted(stamps, reverse=True)

    # Filtering
    # --------------------------------------------------------------------------------

    def test_the_trail_can_be_narrowed_by_action_and_actor(self):
        self.client.credentials()
        self.authenticate_as_admin()
        self.client.patch(
            f"{ACADEMICS}/allocations/{self.allocation}",
            {"teacher": self.teacher_user.pk},
            format="json",
        )

        created = self.trail(resource="subject-allocation", action="CREATED")
        assert {row["action"] for row in created["results"]} == {"CREATED"}

        mine = self.trail(resource="subject-allocation", actor=self.admin.pk)
        assert all(row["actor"]["id"] == self.admin.pk for row in mine["results"])

    def test_a_bad_filter_is_a_field_error_not_a_crash(self):
        for params in ("action=SHOUTED", "actor=abc", "from=last-tuesday"):
            response = self.client.get(f"{AUDIT}/trail?resource=subject-allocation&{params}")
            assert response.status_code == status.HTTP_400_BAD_REQUEST, params

    def test_an_unknown_resource_is_a_404(self):
        response = self.client.get(f"{AUDIT}/trail?resource=nonsense")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    # Authority
    # --------------------------------------------------------------------------------

    def test_a_teacher_reads_only_the_history_of_their_own_classes(self):
        """The trail can never widen past the listing it belongs to."""
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-12", ["PRESENT", "PRESENT", "PRESENT"])
        mine = AttendanceRecord.objects.filter(enrollment=enrollments[0]).first()

        # A second teacher holds a class of their own, marked on the same day.
        self.client.credentials()
        self.authenticate_as_admin()
        other_teacher = self.make_user("teacher2", "TEACHER")
        other_subject = self.post(
            f"{ACADEMICS}/subjects",
            {
                "program": self.program,
                "semester": 3,
                "code": "CSC199",
                "name": "Someone else's class",
            },
        )["id"]
        other_allocation = self.post(
            f"{ACADEMICS}/allocations",
            {
                "batchSemester": self.semester,
                "subject": other_subject,
                "teacher": other_teacher.pk,
            },
        )["id"]
        self.post(
            f"{STUDENTS}/subject-enrollments/bulk",
            {"allocation": other_allocation, "students": self.students},
        )
        other_enrollments = list(
            SubjectEnrollment.objects.filter(allocation=other_allocation).values_list(
                "id", flat=True
            )
        )
        self.client.credentials()
        self.authenticate(other_teacher.username)
        self.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": other_allocation,
                "date": "2026-01-12",
                "entries": [
                    {"enrollment": enrollment, "status": "PRESENT"}
                    for enrollment in other_enrollments
                ],
            },
        )
        theirs = AttendanceRecord.objects.filter(enrollment_id__in=other_enrollments).first()

        self.client.credentials()
        self.authenticate(self.teacher_user.username)
        body = self.trail(resource="attendance-record")
        visible = {row["objectId"] for row in body["results"]}

        assert mine.pk in visible
        assert theirs.pk not in visible

        # Asking for it directly reads as absent rather than forbidden, so the
        # trail cannot be used to confirm a record exists elsewhere.
        response = self.client.get(f"{AUDIT}/trail?resource=attendance-record&object={theirs.pk}")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_trail_needs_the_permission_that_admits_the_records(self):
        self.client.credentials()
        self.authenticate(self.teacher_user.username)

        response = self.client.get(f"{AUDIT}/trail?resource=student")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_resource_picker_lists_only_what_the_caller_may_open(self):
        self.client.credentials()
        self.authenticate(self.teacher_user.username)
        teacher_slugs = {row["slug"] for row in self.client.get(f"{AUDIT}/resources").json()}

        self.client.credentials()
        self.authenticate_as_admin()
        admin_slugs = {row["slug"] for row in self.client.get(f"{AUDIT}/resources").json()}

        assert "attendance-record" in teacher_slugs
        assert "student" not in teacher_slugs
        assert teacher_slugs < admin_slugs

    def test_an_unauthenticated_caller_is_refused(self):
        self.client.credentials()

        response = self.client.get(f"{AUDIT}/trail?resource=subject-allocation")

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    # It stays read-only
    # --------------------------------------------------------------------------------

    def test_the_trail_cannot_be_written_to(self):
        """An audit trail a user can edit answers nothing during an audit."""
        for method in (self.client.post, self.client.patch, self.client.delete):
            response = method(f"{AUDIT}/trail?resource=subject-allocation")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
