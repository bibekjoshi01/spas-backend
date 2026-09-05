import datetime

from django.core.exceptions import ValidationError
from django.utils import timezone

from src.academics.models import (
    AcademicCalendarEntry,
    BatchSemester,
    ClassMeeting,
    SubjectAllocation,
)
from src.performance.models import Assignment, AttendanceSession, InternalExam
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

    def schedule(self, **extra):
        return self.client.post(
            f"{PERFORMANCE}/class-schedule",
            {
                "allocation": self.allocation,
                "date": DAY.isoformat(),
                "kind": "MAKEUP",
                "reason": "Practical class",
                **extra,
            },
            format="json",
        )

    def test_holiday_requires_reason_and_retry_preserves_audit(self):
        self.holiday()
        response = self.attendance()
        assert response.status_code == 400, response.data
        assert "makeupReason" in response.json()
        assert not AttendanceSession.objects.exists()
        response = self.attendance(makeupReason="Practical catch-up")
        assert response.status_code == 201, response.data
        session = AttendanceSession.objects.get()
        assert session.makeup_reason == "Practical catch-up"
        assert session.created_by == self.teacher_user
        assert session.history.first().history_user == self.teacher_user
        assert self.attendance().status_code == 201
        session.refresh_from_db()
        assert session.makeup_reason == "Practical catch-up"
        assert AttendanceSession.objects.count() == 1
        assert session.updated_by == self.teacher_user

    def test_later_calendar_edit_does_not_invalidate_held_class(self):
        assert self.attendance().status_code == 201
        session = AttendanceSession.objects.get()
        self.holiday()
        assert self.attendance().status_code == 201
        session.refresh_from_db()
        assert session.makeup_reason == ""
        assert session.records.count() == 3

    def test_model_also_requires_a_reason(self):
        self.holiday()
        with self.assertRaises(ValidationError):
            AttendanceSession.objects.create(
                allocation_id=self.allocation, date=DAY, created_by=self.teacher_user
            )

    def test_makeup_schedule_supplies_reason_and_cannot_be_cancelled_after_attendance(self):
        self.holiday()
        planned = self.schedule()
        assert planned.status_code == 201, planned.data
        assert self.attendance().status_code == 201
        assert AttendanceSession.objects.get().makeup_reason == "Practical class"
        response = self.client.patch(
            f"{PERFORMANCE}/class-schedule/{planned.data['id']}",
            {"kind": "CANCELLED"},
            format="json",
        )
        assert response.status_code == 400, response.data
        assert (
            self.client.delete(f"{PERFORMANCE}/class-schedule/{planned.data['id']}").status_code
            == 400
        )

    def test_cancelled_class_refuses_attendance_even_with_a_reason(self):
        assert self.schedule(kind="CANCELLED").status_code == 201
        response = self.attendance(makeupReason="Trying to bypass cancellation")
        assert response.status_code == 400
        assert "date" in response.json()
        assert not AttendanceSession.objects.exists()

    def test_schedule_is_scoped_for_reads_writes_and_archives(self):
        row = self.schedule()
        self.make_user("other-calendar-teacher", "TEACHER")
        self.authenticate("other-calendar-teacher")
        assert self.client.get(f"{PERFORMANCE}/class-schedule").json()["count"] == 0
        assert self.client.get(f"{PERFORMANCE}/class-schedule/{row.data['id']}").status_code == 404
        assert (
            self.client.patch(
                f"{PERFORMANCE}/class-schedule/{row.data['id']}",
                {"reason": "Guessed"},
                format="json",
            ).status_code
            == 404
        )
        assert (
            self.client.delete(f"{PERFORMANCE}/class-schedule/{row.data['id']}").status_code == 404
        )
        assert self.schedule().status_code == 400
        assert (
            self.client.get(
                f"{PERFORMANCE}/calendar/class", {"allocation": self.allocation, "date": DAY}
            ).status_code
            == 404
        )

    def test_schedule_rejects_dates_outside_semester_and_completed_writes(self):
        semester = BatchSemester.objects.get(pk=self.semester)
        semester.start_date = DAY + datetime.timedelta(days=1)
        semester.save()
        assert self.schedule().status_code == 400
        semester.start_date = None
        semester.status = "COMPLETED"
        semester.save()
        assert self.schedule().status_code == 400

    def test_calendar_context_and_range_validate_input(self):
        self.holiday()
        url = f"{PERFORMANCE}/calendar/class"
        body = self.client.get(url, {"allocation": self.allocation, "date": DAY}).json()
        assert body["requiresReason"] and not body["isExpected"]
        assert body["holidayTitles"] == ["College holiday"]
        for params in (
            {"date": "bad"},
            {"date_from": "2026-09-05", "date_to": "2026-09-01"},
            {"date_from": "2020-01-01", "date_to": "2026-01-01"},
        ):
            assert (
                self.client.get(url, {"allocation": self.allocation, **params}).status_code == 400
            )

    def test_holiday_suppresses_daily_attendance_reminder_but_makeup_restores_it(self):
        today = timezone.localdate()
        self.holiday(today)
        url = f"{PERFORMANCE}/analytics/overview"
        body = self.client.get(url).json()
        assert body["todaysClasses"] == []
        assert self.schedule(date=today.isoformat()).status_code == 201
        body = self.client.get(url).json()
        assert len(body["todaysClasses"]) == 1

    def test_timetable_and_semester_boundaries_control_expected_days(self):
        allocation = SubjectAllocation.objects.get(pk=self.allocation)
        ClassMeeting.objects.create(allocation=allocation, weekday=1, created_by=self.admin)
        body = self.client.get(
            f"{PERFORMANCE}/calendar/class", {"allocation": self.allocation, "date": DAY}
        ).json()
        assert body["label"] == "Not timetabled"
        assert self.attendance().status_code == 400

    def test_derived_calendar_dates_follow_class_scope(self):
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
        assert {item["kind"] for item in milestones} == {"EXAM", "DEADLINE"}
        self.make_user("other-agenda-teacher", "TEACHER")
        self.authenticate("other-agenda-teacher")
        body = self.client.get(url, {"system": "AD", "year": DAY.year}).json()
        assert body["months"][8]["days"][3]["milestones"] == []

    def test_student_sees_only_derived_dates_for_enrolled_classes(self):
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
        assert body["months"][8]["days"][3]["milestones"][0]["kind"] == "EXAM"
