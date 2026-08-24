"""Aggregate reads: attendance percentage, mark totals, dashboard overview."""

from rest_framework import status

from tests.test_performance_api import PERFORMANCE, WorkflowTestCase


class AnalyticsTests(WorkflowTestCase):
    @staticmethod
    def get_test_schema_name():
        return "test_analytics_api"

    @staticmethod
    def get_test_tenant_domain():
        return "analytics-api.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Analytics College"
        tenant.subdomain = "analytics"
        return tenant

    def read_as_teacher(self, path):
        """Analytics is a teaching surface, answered for the allocated teacher."""
        return self.client.get(path).json()

    def record_day(self, enrollments, date, statuses):
        self.post(
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

    def test_class_summary_reports_roster_and_attendance(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "PRESENT", "ABSENT"])
        self.record_day(enrollments, "2026-01-11", ["PRESENT", "ABSENT", "ABSENT"])

        rows = self.read_as_teacher(f"{PERFORMANCE}/analytics/classes")

        assert len(rows) == 1
        row = rows[0]
        assert row["studentCount"] == 3
        assert row["classesHeld"] == 2
        # 3 attended out of 3 students x 2 classes
        assert row["attendancePercentage"] == 50.0
        assert row["code"] == "CSC201"

    def test_late_counts_as_attended_and_excused_does_not(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["LATE", "EXCUSED", "ABSENT"])

        row = self.read_as_teacher(f"{PERFORMANCE}/analytics/classes")[0]
        assert row["attendancePercentage"] == round(1 / 3 * 100, 1)

    def test_per_student_summary_rolls_up_all_three_parameters(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "ABSENT", "PRESENT"])
        self.record_day(enrollments, "2026-01-11", ["PRESENT", "ABSENT", "ABSENT"])

        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {"allocation": self.allocation, "title": "First Term", "fullMarks": 20},
        )["id"]
        self.post(
            f"{PERFORMANCE}/internal-exams/{exam}/marks",
            {"entries": [{"enrollment": enrollments[0], "marksObtained": "17"}]},
        )

        assignment = self.post(
            f"{PERFORMANCE}/assignments",
            {
                "allocation": self.allocation,
                "title": "Linked lists",
                "assignedDate": "2026-01-05",
            },
        )["id"]
        self.post(
            f"{PERFORMANCE}/assignments/{assignment}/submissions",
            {
                "entries": [
                    {"enrollment": enrollments[0], "status": "DONE"},
                    {"enrollment": enrollments[1], "status": "NOT_DONE"},
                ]
            },
        )

        rows = self.client.get(f"{PERFORMANCE}/analytics/classes/{self.allocation}/students").json()

        first = next(row for row in rows if row["rollNumber"] == "01")
        assert first["attendance"] == {"held": 2, "attended": 2, "percentage": 100.0}
        assert first["internalMarks"] == {"obtained": 17.0, "total": 20}
        assert first["assignments"] == {"done": 1, "total": 1}

        second = next(row for row in rows if row["rollNumber"] == "02")
        assert second["attendance"]["percentage"] == 0.0
        assert second["internalMarks"]["obtained"] == 0

    def test_a_class_with_no_sessions_reports_zero_not_an_error(self):
        self.enroll_roster()
        row = self.read_as_teacher(f"{PERFORMANCE}/analytics/classes")[0]
        assert row["classesHeld"] == 0
        assert row["attendancePercentage"] == 0.0

    def test_overview_counts_todays_recording_and_flags_at_risk(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "ABSENT", "ABSENT"])

        body = self.read_as_teacher(f"{PERFORMANCE}/analytics/overview")

        assert body["stats"]["totalClasses"] == 1
        assert body["stats"]["totalStudents"] == 3
        # two of three students are under 75%
        assert body["stats"]["studentsBelowEligibility"] == 2
        assert body["pendingAttendanceCount"] == 1  # nothing recorded today
        assert len(body["recentActivity"]) == 0  # the session is dated in the past
        assert body["studentsNeedingAttention"][0]["attendancePercentage"] == 0.0

    def test_a_teacher_sees_only_their_own_classes_in_analytics(self):
        self.enroll_roster()

        self.client.credentials()
        self.authenticate(self.teacher_user.username)

        rows = self.read_as_teacher(f"{PERFORMANCE}/analytics/classes")
        assert len(rows) == 1

        other = self.make_user("teacher9", "TEACHER")
        self.client.credentials()
        self.authenticate(other.username)
        assert self.client.get(f"{PERFORMANCE}/analytics/classes").json() == []

    def test_analytics_requires_authentication(self):
        self.client.credentials()
        response = self.client.get(f"{PERFORMANCE}/analytics/overview")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
