"""Aggregate reads: attendance percentage, mark totals, dashboard overview."""

from django.utils import timezone
from rest_framework import status

from src.students.models import SemesterEnrollment, Student
from src.user.models import UserRole
from tests.test_performance_api import ACADEMICS, PERFORMANCE, STUDENTS, WorkflowTestCase


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

    def set_timetable(self, meetings):
        """Timetabling is a management action; the dashboard is read as the teacher."""
        self.client.credentials()
        self.authenticate_as_admin()
        response = self.client.patch(
            f"{ACADEMICS}/allocations/{self.allocation}",
            {"meetings": meetings},
            format="json",
        )
        self.client.credentials()
        self.authenticate(self.teacher_user.username)
        return response

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

    def test_teacher_classes_are_sorted_by_start_time_with_unscheduled_last(self):
        self.client.patch(
            f"{ACADEMICS}/allocations/{self.allocation}",
            {"startTime": "11:00", "endTime": "12:00", "teacher": self.teacher_user.pk},
            format="json",
        )
        earlier_subject = self.post(
            f"{ACADEMICS}/subjects",
            {
                "program": self.program,
                "semester": 3,
                "code": "CSC200",
                "name": "Algorithms",
            },
        )["id"]
        self.post(
            f"{ACADEMICS}/allocations",
            {
                "batchSemester": self.semester,
                "subject": earlier_subject,
                "teacher": self.teacher_user.pk,
                "startTime": "09:00",
                "endTime": "10:00",
            },
        )
        unscheduled_subject = self.post(
            f"{ACADEMICS}/subjects",
            {
                "program": self.program,
                "semester": 3,
                "code": "CSC299",
                "name": "Seminar",
            },
        )["id"]
        self.post(
            f"{ACADEMICS}/allocations",
            {
                "batchSemester": self.semester,
                "subject": unscheduled_subject,
                "teacher": self.teacher_user.pk,
            },
        )
        self.as_teacher()

        rows = self.client.get(f"{PERFORMANCE}/analytics/classes").json()

        assert [row["code"] for row in rows] == ["CSC200", "CSC201", "CSC299"]

    def test_late_counts_as_attended_and_excused_does_not(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["LATE", "EXCUSED", "ABSENT"])

        row = self.read_as_teacher(f"{PERFORMANCE}/analytics/classes")[0]
        assert row["attendancePercentage"] == round(1 / 3 * 100, 2)

    def test_per_student_summary_rolls_up_all_three_parameters(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "ABSENT", "PRESENT"])
        self.record_day(enrollments, "2026-01-11", ["PRESENT", "ABSENT", "ABSENT"])

        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {
                "allocation": self.allocation,
                "title": "First Term",
                "fullMarks": 20,
                "passMarks": 8,
            },
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
        assert first["email"] == ""
        assert first["phoneNo"] == ""
        assert first["alternatePhoneNo"] == ""
        assert first["attendance"] == {
            "held": 2,
            "attended": 2,
            "percentage": 100.0,
            "recent": [
                {"date": "2026-01-11", "period": 1, "status": "PRESENT"},
                {"date": "2026-01-10", "period": 1, "status": "PRESENT"},
            ],
        }
        assert first["internalMarks"] == {"obtained": 17.0, "total": 20}
        assert first["assignments"] == {"done": 1, "total": 1}
        assert first["performancePercentage"] == 93.33

        second = next(row for row in rows if row["rollNumber"] == "02")
        assert second["attendance"]["percentage"] == 0.0
        assert second["internalMarks"]["obtained"] == 0
        assert second["performancePercentage"] == 0.0

    def test_student_detail_is_limited_to_one_enrollment_and_owned_class(self):
        enrollments = self.enroll_roster()
        response = self.client.get(
            f"{PERFORMANCE}/analytics/classes/{self.allocation}/students/{enrollments[0]}"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["student"]["rollNumber"] == "01"
        assert response.json()["class"]["allocation"] == self.allocation
        assert response.json()["attendance"] == {
            "held": 0,
            "present": 0,
            "absent": 0,
            "excused": 0,
            "late": 0,
            "percentage": 0.0,
            # No class has been held, so there is no shape to report yet.
            "trend": {
                "points": [],
                "overallPercentage": None,
                "recentPercentage": None,
                "direction": None,
                "recentWeeks": 4,
            },
        }

        outsider = self.make_user("detail-outsider", "TEACHER")
        self.client.credentials()
        self.authenticate(outsider.username)
        response = self.client.get(
            f"{PERFORMANCE}/analytics/classes/{self.allocation}/students/{enrollments[0]}"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_class_with_no_sessions_reports_zero_not_an_error(self):
        self.enroll_roster()
        row = self.read_as_teacher(f"{PERFORMANCE}/analytics/classes")[0]
        assert row["classesHeld"] == 0
        assert row["attendancePercentage"] == 0.0

    def test_class_summary_can_exclude_historical_semesters_at_the_api_boundary(self):
        self.enroll_roster()

        response = self.client.get(f"{PERFORMANCE}/analytics/classes?semester_status=RUNNING")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 1

        response = self.client.get(f"{PERFORMANCE}/analytics/classes?semester_status=COMPLETED")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

        response = self.client.get(f"{PERFORMANCE}/analytics/classes?semester_status=INVALID")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

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

    def test_an_unscheduled_class_still_counts_as_meeting_today(self):
        """
        Every allocation looks like this before anyone fills the timetable in.

        Treating no schedule as "meets nothing" would empty the dashboard of a
        college that has not got round to scheduling, which is worse than the
        behaviour this replaces.
        """
        self.enroll_roster()

        body = self.read_as_teacher(f"{PERFORMANCE}/analytics/overview")

        assert body["stats"]["classesTotalToday"] == 1
        assert len(body["todaysClasses"]) == 1
        assert body["pendingAttendanceCount"] == 1

    def test_a_class_timetabled_for_another_day_is_not_chased_today(self):
        """The bug this feature exists to fix: a phantom queue that never empties."""
        self.enroll_roster()
        tomorrow = timezone.localdate().isoweekday() % 7 + 1

        response = self.set_timetable(
            [{"weekday": tomorrow, "startTime": "10:00", "endTime": "11:00"}]
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        body = self.read_as_teacher(f"{PERFORMANCE}/analytics/overview")

        assert body["stats"]["classesTotalToday"] == 0
        assert body["todaysClasses"] == []
        # The whole point: with nothing due, the queue reaches zero.
        assert body["pendingAttendanceCount"] == 0
        assert [row for row in body["workQueue"] if row["kind"] == "ATTENDANCE"] == []
        # The class itself has not gone anywhere.
        assert body["stats"]["totalClasses"] == 1

    def test_a_class_timetabled_for_today_is_still_chased(self):
        self.enroll_roster()
        today_weekday = timezone.localdate().isoweekday()

        self.set_timetable(
            [
                {"weekday": today_weekday, "startTime": "07:00", "endTime": "08:00"},
                {"weekday": today_weekday, "startTime": "14:00", "endTime": "15:00"},
            ]
        )

        body = self.read_as_teacher(f"{PERFORMANCE}/analytics/overview")

        assert body["stats"]["classesTotalToday"] == 1
        assert body["pendingAttendanceCount"] == 1
        # Two slots on one day, which is what the session period field allows for.
        assert len(body["todaysClasses"][0]["meetings"]) == 2

    def test_editing_an_allocation_without_meetings_leaves_the_timetable_alone(self):
        """A PATCH of only the teacher must not wipe the schedule."""
        self.enroll_roster()
        today_weekday = timezone.localdate().isoweekday()
        self.set_timetable([{"weekday": today_weekday, "startTime": "07:00", "endTime": "08:00"}])

        self.client.credentials()
        self.authenticate_as_admin()
        response = self.client.patch(
            f"{ACADEMICS}/allocations/{self.allocation}", {"isActive": True}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        self.client.credentials()
        self.authenticate(self.teacher_user.username)

        body = self.read_as_teacher(f"{PERFORMANCE}/analytics/overview")
        assert len(body["todaysClasses"][0]["meetings"]) == 1

    def test_sending_an_empty_timetable_clears_it(self):
        """The editor sends the whole week, so removing every slot has to stick."""
        self.enroll_roster()
        today_weekday = timezone.localdate().isoweekday()
        self.set_timetable([{"weekday": today_weekday, "startTime": "07:00", "endTime": "08:00"}])

        response = self.set_timetable([])
        assert response.status_code == status.HTTP_200_OK, response.data

        # Back to unscheduled, which reads as meeting every day.
        body = self.read_as_teacher(f"{PERFORMANCE}/analytics/overview")
        assert body["todaysClasses"][0]["meetings"] == []
        assert body["stats"]["classesTotalToday"] == 1

    def test_a_slot_needs_both_times_or_neither(self):
        self.enroll_roster()

        response = self.set_timetable([{"weekday": 1, "startTime": "10:00"}])
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = self.set_timetable([{"weekday": 1, "startTime": "11:00", "endTime": "10:00"}])
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_attendance_is_still_allowed_on_an_unscheduled_day(self):
        """Makeup classes happen; the timetable steers the dashboard, not the register."""
        enrollments = self.enroll_roster()
        tomorrow = timezone.localdate().isoweekday() % 7 + 1
        self.set_timetable([{"weekday": tomorrow, "startTime": "10:00", "endTime": "11:00"}])

        today = timezone.localdate().isoformat()
        self.record_day(enrollments, today, ["PRESENT", "PRESENT", "PRESENT"])

        body = self.read_as_teacher(f"{PERFORMANCE}/analytics/overview")
        assert body["stats"]["classesRecordedToday"] == 1

    # Trends
    # --------------------------------------------------------------------------------

    def weeks_of_attendance(self, enrollments, pattern):
        """
        One class a week, oldest first, so the weekly buckets are unambiguous.

        `pattern` is the first student's status each week; the other two attend
        throughout, which keeps the class figure steady while one student moves.
        """
        from datetime import timedelta

        from django.utils import timezone

        start = timezone.localdate() - timedelta(weeks=len(pattern))
        for index, status_ in enumerate(pattern):
            self.record_day(
                enrollments,
                (start + timedelta(weeks=index)).isoformat(),
                [status_, "PRESENT", "PRESENT"],
            )

    def test_a_recovering_student_reads_differently_from_a_falling_one(self):
        """
        The question a lifetime aggregate cannot answer.

        Both students below end on the same overall percentage; only the
        direction separates the one climbing back from the one still dropping.
        """
        enrollments = self.enroll_roster()
        # Away for the first half, back for the second.
        self.weeks_of_attendance(
            enrollments, ["ABSENT", "ABSENT", "ABSENT", "PRESENT", "PRESENT", "PRESENT"]
        )

        body = self.read_as_teacher(
            f"{PERFORMANCE}/analytics/classes/{self.allocation}/students/{enrollments[0]}"
        )
        trend = body["attendance"]["trend"]

        assert trend["direction"] == "RISING"
        assert trend["overallPercentage"] == 50.0
        # Lately they have been there every time, which the overall figure hides.
        assert trend["recentPercentage"] > trend["overallPercentage"]
        assert len(trend["points"]) == 6

    def test_a_student_who_has_stopped_coming_reads_as_falling(self):
        enrollments = self.enroll_roster()
        self.weeks_of_attendance(
            enrollments, ["PRESENT", "PRESENT", "PRESENT", "ABSENT", "ABSENT", "ABSENT"]
        )

        body = self.read_as_teacher(
            f"{PERFORMANCE}/analytics/classes/{self.allocation}/students/{enrollments[0]}"
        )
        trend = body["attendance"]["trend"]

        assert trend["direction"] == "FALLING"
        assert trend["overallPercentage"] == 50.0
        assert trend["recentPercentage"] < trend["overallPercentage"]

    def test_steady_attendance_is_not_reported_as_a_direction(self):
        enrollments = self.enroll_roster()
        self.weeks_of_attendance(enrollments, ["PRESENT"] * 5)

        body = self.read_as_teacher(
            f"{PERFORMANCE}/analytics/classes/{self.allocation}/students/{enrollments[0]}"
        )

        assert body["attendance"]["trend"]["direction"] == "STEADY"

    def test_the_plotted_line_is_the_standing_the_bar_is_drawn_against(self):
        """
        Each point is the running figure as it stood that week, not that week
        alone, so the line can be read against the eligibility threshold.
        """
        enrollments = self.enroll_roster()
        self.weeks_of_attendance(enrollments, ["PRESENT", "ABSENT", "ABSENT", "ABSENT"])

        body = self.read_as_teacher(
            f"{PERFORMANCE}/analytics/classes/{self.allocation}/students/{enrollments[0]}"
        )
        line = [point["percentage"] for point in body["attendance"]["trend"]["points"]]

        # 1/1, 1/2, 1/3, 1/4 — a running figure, monotonically falling here.
        assert line == [100.0, 50.0, 33.33, 25.0]

    def test_a_week_without_a_class_is_left_out_rather_than_plotted_as_zero(self):
        """A holiday is not a week of nobody turning up."""
        from datetime import timedelta

        from django.utils import timezone

        enrollments = self.enroll_roster()
        start = timezone.localdate() - timedelta(weeks=4)
        for offset in (0, 3):  # a fortnight's gap in the middle
            self.record_day(
                enrollments,
                (start + timedelta(weeks=offset)).isoformat(),
                ["PRESENT", "PRESENT", "PRESENT"],
            )

        body = self.read_as_teacher(
            f"{PERFORMANCE}/analytics/classes/{self.allocation}/students/{enrollments[0]}"
        )
        points = body["attendance"]["trend"]["points"]

        assert len(points) == 2
        assert all(point["held"] > 0 for point in points)

    def test_the_at_risk_list_says_which_way_each_student_is_going(self):
        """A flat list of names below the bar is not a list a teacher can act on."""
        enrollments = self.enroll_roster()
        # Five weeks away, three back: still well under the bar, but climbing.
        self.weeks_of_attendance(enrollments, ["ABSENT"] * 5 + ["PRESENT"] * 3)

        body = self.read_as_teacher(f"{PERFORMANCE}/analytics/overview")
        at_risk = body["studentsNeedingAttention"][0]

        assert at_risk["attendancePercentage"] == 37.5
        assert at_risk["trend"]["direction"] == "RISING"
        assert at_risk["trend"]["recentPercentage"] == 75.0

    def test_a_class_carries_its_own_trend(self):
        enrollments = self.enroll_roster()
        self.weeks_of_attendance(enrollments, ["PRESENT"] * 4)

        rows = self.read_as_teacher(f"{PERFORMANCE}/analytics/classes")

        assert rows[0]["trend"]["overallPercentage"] == 100.0
        assert len(rows[0]["trend"]["points"]) == 4

    def test_program_coordinator_gets_scoped_management_today_even_with_teacher_role(self):
        enrollments = self.enroll_roster()
        today = timezone.localdate().isoformat()
        self.record_day(enrollments, today, ["PRESENT", "ABSENT", "LATE"])

        self.client.credentials()
        self.authenticate_as_admin()
        coordinator = self.make_user("coordinator1", "PROGRAM-COORDINATOR")
        coordinator.roles.add(UserRole.objects.get(codename="TEACHER"))
        response = self.client.patch(
            f"{ACADEMICS}/programs/{self.program}",
            {"coordinator": coordinator.pk},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        self.client.credentials()
        self.authenticate(coordinator.username)
        body = self.client.get(f"{PERFORMANCE}/analytics/overview").json()

        assert body["experience"] == "MANAGEMENT"
        assert body["managementLevel"] == "PROGRAM"
        assert body["stats"]["totalClasses"] == 1
        assert body["todayAttendance"] == {
            "sessionsRecorded": 1,
            "classesRecorded": 1,
            "activeClasses": 1,
            "marked": 3,
            "present": 1,
            "absent": 1,
            "late": 1,
            "excused": 0,
            "attendancePercentage": 66.67,
            "classesToReview": [],
        }

    def test_attendance_attention_queue_is_management_scoped_and_includes_contact(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "ABSENT", "ABSENT"])
        Student.objects.filter(pk=self.students[1]).update(phone_no="9800000002")

        self.client.credentials()
        self.authenticate_as_admin()
        coordinator = self.make_user("queue-coordinator", "PROGRAM-COORDINATOR")
        response = self.client.patch(
            f"{ACADEMICS}/programs/{self.program}",
            {"coordinator": coordinator.pk},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        self.client.credentials()
        self.authenticate(coordinator.username)
        response = self.client.get(
            f"{PERFORMANCE}/analytics/attendance-attention?search=9800000002"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1
        row = response.json()["results"][0]
        assert row["phoneNo"] == "9800000002"
        assert row["attendancePercentage"] == 0.0
        assert row["allocation"] == self.allocation

        self.as_teacher()
        assert (
            self.client.get(f"{PERFORMANCE}/analytics/attendance-attention").status_code
            == status.HTTP_403_FORBIDDEN
        )

    def test_attention_queue_follows_the_configured_eligibility_threshold(self):
        """The bar is the college's, not a constant: moving it moves the queue."""
        enrollments = self.enroll_roster()
        # Two of three days attended is 66.67%: under the shipped 75, over a 50 bar.
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "PRESENT", "PRESENT"])
        self.record_day(enrollments, "2026-01-11", ["PRESENT", "PRESENT", "PRESENT"])
        self.record_day(enrollments, "2026-01-12", ["ABSENT", "ABSENT", "ABSENT"])

        self.client.credentials()
        self.authenticate_as_admin()
        queue = f"{PERFORMANCE}/analytics/attendance-attention"

        assert self.client.get(queue).json()["count"] == 3

        response = self.client.put(
            f"{PERFORMANCE}/settings/performance-weights",
            {"attendanceEligibilityThreshold": "50.00"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        assert self.client.get(queue).json()["count"] == 0

        # And a stricter college catches everyone again.
        self.client.put(
            f"{PERFORMANCE}/settings/performance-weights",
            {"attendanceEligibilityThreshold": "90.00"},
            format="json",
        )
        assert self.client.get(queue).json()["count"] == 3

    def test_management_student_report_is_complete_scoped_and_denies_teachers(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "ABSENT", "ABSENT"])

        self.client.credentials()
        self.authenticate_as_admin()
        coordinator = self.make_user("report-coordinator", "PROGRAM-COORDINATOR")
        response = self.client.patch(
            f"{ACADEMICS}/programs/{self.program}",
            {"coordinator": coordinator.pk},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        other_department = self.post(
            f"{ACADEMICS}/departments", {"name": "Management", "code": "MGT"}
        )["id"]
        other_program = self.post(
            f"{ACADEMICS}/programs",
            {"department": other_department, "name": "BBA", "code": "BBA"},
        )["id"]
        other_batch = self.post(f"{ACADEMICS}/batches", {"program": other_program, "year": 2080})[
            "id"
        ]
        other_student = self.post(
            f"{STUDENTS}/students",
            {
                "batch": other_batch,
                "rollNumber": "01",
                "firstName": "Other",
                "lastName": "Student",
            },
        )["id"]

        self.client.credentials()
        self.authenticate(coordinator.username)
        response = self.client.get(f"{PERFORMANCE}/analytics/students/{self.students[0]}/report")
        assert response.status_code == status.HTTP_200_OK, response.data
        body = response.json()
        assert body["student"]["id"] == self.students[0]
        assert body["student"]["programCode"] == "BSCCSIT"
        assert len(body["subjects"]) == 1
        assert body["subjects"][0]["attendance"]["percentage"] == 100.0

        assert (
            self.client.get(f"{PERFORMANCE}/analytics/students/{other_student}/report").status_code
            == status.HTTP_404_NOT_FOUND
        )

        self.as_teacher()
        assert (
            self.client.get(
                f"{PERFORMANCE}/analytics/students/{self.students[0]}/report"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )

    def test_batch_semester_report_adapts_weights_and_is_authority_scoped(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "ABSENT", "ABSENT"])
        # Legacy/roster-first data must still report even if progression was
        # never recorded for one otherwise valid cohort student.
        SemesterEnrollment.objects.filter(
            batch_semester_id=self.semester,
            student_id=self.students[2],
        ).delete()

        self.client.credentials()
        self.authenticate_as_admin()
        coordinator = self.make_user("cohort-coordinator", "PROGRAM-COORDINATOR")
        response = self.client.patch(
            f"{ACADEMICS}/programs/{self.program}",
            {"coordinator": coordinator.pk},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        other_department = self.post(f"{ACADEMICS}/departments", {"name": "Civil", "code": "CIV"})[
            "id"
        ]
        other_program = self.post(
            f"{ACADEMICS}/programs",
            {"department": other_department, "name": "Civil Engineering", "code": "BCE"},
        )["id"]
        other_batch = self.post(f"{ACADEMICS}/batches", {"program": other_program, "year": 2081})[
            "id"
        ]
        other_semester = self.post(
            f"{ACADEMICS}/batch-semesters",
            {"batch": other_batch, "semester": 1, "status": "RUNNING"},
        )["id"]

        self.client.credentials()
        self.authenticate(coordinator.username)
        response = self.client.get(
            f"{PERFORMANCE}/analytics/batch-semester-report",
            {"batch_semester": self.semester, "ordering": "risk"},
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        body = response.json()
        assert body["summary"] == {
            "students": 3,
            "withEvidence": 3,
            "needsAttention": 2,
            "averagePerformance": 33.33,
        }
        assert body["results"][0]["needsAttention"] is True
        assert body["results"][0]["overallPercentage"] == 0.0
        assert body["results"][2]["overallPercentage"] == 100.0

        assert (
            self.client.get(
                f"{PERFORMANCE}/analytics/batch-semester-report",
                {"batch_semester": other_semester},
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert (
            self.client.get(f"{PERFORMANCE}/analytics/batch-semester-report").status_code
            == status.HTTP_400_BAD_REQUEST
        )

        self.as_teacher()
        assert (
            self.client.get(
                f"{PERFORMANCE}/analytics/batch-semester-report",
                {"batch_semester": self.semester},
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )

    def test_management_can_read_class_report_only_inside_live_authority(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "ABSENT", "LATE"])

        self.client.credentials()
        self.authenticate_as_admin()
        coordinator = self.make_user("class-report-coordinator", "PROGRAM-COORDINATOR")
        response = self.client.patch(
            f"{ACADEMICS}/programs/{self.program}",
            {"coordinator": coordinator.pk},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        other_department = self.post(
            f"{ACADEMICS}/departments", {"name": "Architecture", "code": "ARCH"}
        )["id"]
        other_program = self.post(
            f"{ACADEMICS}/programs",
            {"department": other_department, "name": "Architecture", "code": "BARCH"},
        )["id"]
        other_batch = self.post(f"{ACADEMICS}/batches", {"program": other_program, "year": 2081})[
            "id"
        ]
        other_semester = self.post(
            f"{ACADEMICS}/batch-semesters",
            {"batch": other_batch, "semester": 1, "status": "RUNNING"},
        )["id"]
        other_subject = self.post(
            f"{ACADEMICS}/subjects",
            {
                "program": other_program,
                "semester": 1,
                "code": "ARC101",
                "name": "Design Studio",
            },
        )["id"]
        other_teacher = self.make_user("other-report-teacher", "TEACHER")
        other_allocation = self.post(
            f"{ACADEMICS}/allocations",
            {
                "batchSemester": other_semester,
                "subject": other_subject,
                "teacher": other_teacher.pk,
            },
        )["id"]

        self.client.credentials()
        self.authenticate(coordinator.username)
        response = self.client.get(f"{PERFORMANCE}/analytics/classes/{self.allocation}/students")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 3
        assert (
            self.client.get(
                f"{PERFORMANCE}/analytics/classes/{self.allocation}/students/{enrollments[0]}"
            ).status_code
            == status.HTTP_200_OK
        )
        assert (
            self.client.get(
                f"{PERFORMANCE}/analytics/classes/{other_allocation}/students"
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )

        response = self.client.get(
            f"{PERFORMANCE}/analytics/management-attendance-report",
            {"start_date": "2026-01-10", "end_date": "2026-01-10"},
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.json()["summary"] == {
            "sessions": 1,
            "marked": 3,
            "present": 1,
            "absent": 1,
            "late": 1,
            "excused": 0,
            "attendancePercentage": 66.67,
        }
        assert response.json()["results"][0]["subjectCode"] == "CSC201"

        response = self.client.get(
            f"{PERFORMANCE}/analytics/management-attendance-report",
            {
                "start_date": "2026-01-10",
                "end_date": "2026-01-10",
                "allocation": other_allocation,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 0

        self.as_teacher()
        assert (
            self.client.get(
                f"{PERFORMANCE}/analytics/management-attendance-report",
                {"start_date": "2026-01-10", "end_date": "2026-01-10"},
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )

    def test_management_attendance_report_validates_bounded_dates(self):
        response = self.client.get(f"{PERFORMANCE}/analytics/management-attendance-report")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = self.client.get(
            f"{PERFORMANCE}/analytics/management-attendance-report",
            {"start_date": "2026-01-11", "end_date": "2026-01-10"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_overview_work_queue_surfaces_only_actionable_active_class_work(self):
        enrollments = self.enroll_roster()
        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {
                "allocation": self.allocation,
                "title": "Unit Test",
                "fullMarks": 20,
                "passMarks": 8,
            },
        )["id"]
        self.post(
            f"{PERFORMANCE}/internal-exams/{exam}/marks",
            {"entries": [{"enrollment": enrollments[0], "marksObtained": "16"}]},
        )
        assignment = self.post(
            f"{PERFORMANCE}/assignments",
            {
                "allocation": self.allocation,
                "title": "Data structures exercise",
                "assignedDate": "2026-01-05",
            },
        )["id"]
        self.post(
            f"{PERFORMANCE}/assignments/{assignment}/submissions",
            {"entries": [{"enrollment": enrollments[0], "status": "DONE"}]},
        )

        body = self.client.get(f"{PERFORMANCE}/analytics/overview").json()
        queue = body["workQueue"]

        assert queue[0]["kind"] == "ATTENDANCE"
        by_kind = {item["kind"]: item for item in queue}
        assert set(by_kind) == {"ATTENDANCE", "ASSESSMENT", "ASSIGNMENT", "PERFORMANCE"}
        assert by_kind["ASSESSMENT"]["remaining"] == 2
        assert by_kind["ASSIGNMENT"]["remaining"] == 2
        assert by_kind["PERFORMANCE"]["remaining"] == 3
        assert {item["allocation"] for item in queue} == {self.allocation}

    def test_overview_counts_unique_students_across_active_classes(self):
        self.enroll_roster(then_teach=False)
        second_subject = self.post(
            f"{ACADEMICS}/subjects",
            {
                "program": self.program,
                "semester": 3,
                "code": "CSC202",
                "name": "Database Systems",
            },
        )["id"]
        second_allocation = self.post(
            f"{ACADEMICS}/allocations",
            {
                "batchSemester": self.semester,
                "subject": second_subject,
                "teacher": self.teacher_user.pk,
            },
        )["id"]
        self.post(
            f"{STUDENTS}/subject-enrollments/bulk",
            {"allocation": second_allocation, "students": self.students},
        )

        self.as_teacher()
        body = self.client.get(f"{PERFORMANCE}/analytics/overview").json()

        assert body["stats"]["totalClasses"] == 2
        assert body["stats"]["totalStudents"] == 3

    def test_overview_excludes_every_metric_from_completed_semesters(self):
        enrollments = self.enroll_roster()
        self.record_day(enrollments, "2026-01-10", ["PRESENT", "ABSENT", "ABSENT"])

        self.client.credentials()
        self.authenticate_as_admin()
        response = self.client.patch(
            f"{ACADEMICS}/batch-semesters/{self.semester}",
            {"status": "COMPLETED"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        self.as_teacher()
        body = self.client.get(f"{PERFORMANCE}/analytics/overview").json()

        assert body["stats"] == {
            "totalClasses": 0,
            "totalStudents": 0,
            "avgAttendancePercentage": 0.0,
            "studentsBelowEligibility": 0,
            "classesRecordedToday": 0,
            "classesTotalToday": 0,
        }
        assert body["pendingAttendanceCount"] == 0
        assert body["todaysClasses"] == []
        assert body["studentsNeedingAttention"] == []
        assert body["recentActivity"] == []

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
