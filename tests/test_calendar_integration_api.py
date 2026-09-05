import datetime

from django.core.exceptions import ValidationError
from django.utils import timezone

from src.academics.models import (
    AcademicCalendarConfiguration,
    AcademicCalendarEntry,
    ClassMeeting,
    SubjectAllocation,
)
from src.performance.models import Assignment, AttendanceSession, ClassScheduleChange, InternalExam
from src.students.models import Student, StudentPortalConfiguration
from tests.test_performance_api import ACADEMICS, PERFORMANCE, WorkflowTestCase

DAY = datetime.date(2026, 9, 4)  # Friday; independent of the host's current weekday.


class CalendarIntegrationTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.enrollments = self.enroll_roster()

    def holiday(self, date=DAY):
        return AcademicCalendarEntry.objects.create(
            date=date, kind="HOLIDAY", title="College holiday", created_by=self.admin
        )

    def attendance(self, **extra):
        return self.client.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": DAY.isoformat(),
                "entries": [{"enrollment": pk, "status": "PRESENT"} for pk in self.enrollments],
                **extra,
            },
            format="json",
        )

    def test_holiday_blocks_even_with_makeup_reason(self):
        self.holiday()
        for extra in ({}, {"makeupReason": "Catch-up class"}):
            response = self.attendance(**extra)
            assert response.status_code == 400, response.data
            assert "date" in response.json()
        assert not AttendanceSession.objects.exists()

    def test_configured_weekends_control_attendance(self):
        config = AcademicCalendarConfiguration.current()
        config.created_by = self.admin
        config.weekend_days = [5]
        config.save()
        assert self.attendance().status_code == 400
        config.weekend_days = [6]
        config.save()
        assert self.attendance().status_code == 201
        assert self.attendance(date="2026-09-05").status_code == 400

    def test_later_holiday_preserves_history_but_blocks_mutations(self):
        assert self.attendance().status_code == 201
        session = AttendanceSession.objects.get()
        self.holiday()
        assert self.attendance().status_code == 400
        assert session.records.count() == 3
        assert self.client.get(f"{PERFORMANCE}/attendance-sessions/{session.pk}").status_code == 200

    def test_model_blocks_holidays_even_with_a_reason(self):
        self.holiday()
        with self.assertRaises(ValidationError):
            AttendanceSession.objects.create(
                allocation_id=self.allocation,
                date=DAY,
                makeup_reason="Catch-up",
                created_by=self.teacher_user,
            )

    def test_legacy_makeup_does_not_override_holiday(self):
        self.holiday()
        ClassScheduleChange.objects.create(
            allocation_id=self.allocation,
            date=DAY,
            kind="MAKEUP",
            reason="Previously scheduled",
            created_by=self.teacher_user,
        )
        assert self.attendance().status_code == 400

    def test_timetable_and_legacy_cancellation_do_not_block_open_day(self):
        allocation = SubjectAllocation.objects.get(pk=self.allocation)
        ClassMeeting.objects.create(allocation=allocation, weekday=1, created_by=self.admin)
        ClassScheduleChange.objects.create(
            allocation=allocation,
            date=DAY,
            kind="CANCELLED",
            reason="Previously cancelled",
            created_by=self.teacher_user,
        )
        assert self.attendance().status_code == 201
        session = AttendanceSession.objects.get()
        assert session.makeup_reason == ""
        assert session.history.first().history_user == self.teacher_user
        assert self.attendance().status_code == 201
        assert AttendanceSession.objects.count() == 1

    def test_event_and_inactive_holiday_do_not_close_day(self):
        event = self.holiday()
        event.kind = "EVENT"
        event.save()
        inactive = AcademicCalendarEntry.objects.create(
            date=DAY,
            kind="HOLIDAY",
            title="Inactive holiday",
            is_active=False,
            created_by=self.admin,
        )
        assert self.attendance().status_code == 201
        inactive.is_active = True
        inactive.is_archived = True
        inactive.save()
        assert self.attendance().status_code == 201

    def test_calendar_is_scoped_and_matches_academic_calendar(self):
        self.holiday()
        url = f"{PERFORMANCE}/calendar/class"
        params = {"allocation": self.allocation, "system": "BS", "anchor": DAY}
        response = self.client.get(url, params)
        assert response.status_code == 200, response.data
        assert response.json()["year"] == 2083
        academic = self.client.get(
            f"{ACADEMICS}/calendar/year", {"system": "BS", "year": 2083}
        ).json()
        assert response.json() == academic
        self.make_user("other-calendar-teacher", "TEACHER")
        self.authenticate("other-calendar-teacher")
        assert self.client.get(url, params).status_code == 404
        assert self.attendance().status_code == 400

    def test_calendar_context_and_range_validate_input(self):
        self.holiday()
        url = f"{PERFORMANCE}/calendar/class"
        body = self.client.get(url, {"allocation": self.allocation, "date": DAY}).json()
        assert not body["isExpected"]
        assert body["holidayTitles"] == ["College holiday"]
        for params in (
            {"date": "bad"},
            {"date_from": "2026-09-05", "date_to": "2026-09-01"},
            {"date_from": "2020-01-01", "date_to": "2026-01-01"},
            {"system": "BS", "anchor": "1800-01-01"},
            {"system": "BS", "year": 9999},
        ):
            assert (
                self.client.get(url, {"allocation": self.allocation, **params}).status_code == 400
            )

    def test_holiday_suppresses_daily_attendance_reminder(self):
        self.holiday(timezone.localdate())
        body = self.client.get(f"{PERFORMANCE}/analytics/overview").json()
        assert body["todaysClasses"] == []

    def test_exams_and_assignments_do_not_appear_in_calendar(self):
        InternalExam.objects.create(
            allocation_id=self.allocation,
            title="Unit test",
            exam_date=DAY,
            full_marks=20,
            pass_marks=8,
            created_by=self.teacher_user,
        )
        Assignment.objects.create(
            allocation_id=self.allocation,
            title="Coursework",
            assigned_date=DAY,
            due_date=DAY,
            created_by=self.teacher_user,
        )
        url = f"{ACADEMICS}/calendar/year"
        body = self.client.get(url, {"system": "AD", "year": DAY.year}).json()
        milestones = body["months"][8]["days"][3]["milestones"]
        assert milestones == []
        self.make_user("other-agenda-teacher", "TEACHER")
        self.authenticate("other-agenda-teacher")
        body = self.client.get(url, {"system": "AD", "year": DAY.year}).json()
        assert body["months"][8]["days"][3]["milestones"] == []

    def test_student_calendar_does_not_show_exams(self):
        InternalExam.objects.create(
            allocation_id=self.allocation,
            title="Unit test",
            exam_date=DAY,
            full_marks=20,
            pass_marks=8,
            created_by=self.teacher_user,
        )
        StudentPortalConfiguration.objects.create(login_enabled=True, created_by=self.admin)
        user = Student.objects.get(pk=self.students[0]).user
        user.must_change_password = False
        user.set_password(self.password)
        user.save()
        self.authenticate(user.username)
        body = self.client.get(
            f"{ACADEMICS}/student-portal/calendar/year", {"system": "AD", "year": DAY.year}
        ).json()
        assert body["months"][8]["days"][3]["milestones"] == []
