"""The teacher's daily workflow: roster, attendance, marks, assignments."""

from rest_framework import status

from src.academics.constants import SemesterStatus
from src.academics.models import BatchSemester
from src.performance.models import (
    AttendanceRecord,
    ClassPerformanceRating,
    InternalExamMark,
    PerformanceWeightConfiguration,
)
from src.students.models import SemesterEnrollment, Student, SubjectEnrollment
from src.user.models import Permission, User
from tests.base import INTERNAL, TenantAPITestCase

ACADEMICS = f"{INTERNAL}/academics-mod"
STUDENTS = f"{INTERNAL}/students-mod"
PERFORMANCE = f"{INTERNAL}/performance-mod"


class WorkflowTestCase(TenantAPITestCase):
    @staticmethod
    def get_test_schema_name():
        return "test_performance_api"

    @staticmethod
    def get_test_tenant_domain():
        return "performance-api.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Performance API College"
        tenant.subdomain = "performance-api"
        return tenant

    def post(self, url, payload, expected=status.HTTP_201_CREATED):
        response = self.client.post(url, payload, format="json")
        assert response.status_code == expected, response.data
        return response.data

    def setUp(self):
        super().setUp()
        self.authenticate_as_admin()

        self.department = self.post(
            f"{ACADEMICS}/departments", {"name": "Computer Science", "code": "CSIT"}
        )["id"]
        program = self.post(
            f"{ACADEMICS}/programs",
            {"department": self.department, "name": "B.Sc. CSIT", "code": "BSCCSIT"},
        )["id"]
        batch = self.post(f"{ACADEMICS}/batches", {"program": program, "year": 2079})["id"]
        self.semester = self.post(
            f"{ACADEMICS}/batch-semesters",
            {"batch": batch, "semester": 3, "status": "RUNNING"},
        )["id"]
        subject = self.post(
            f"{ACADEMICS}/subjects",
            {"program": program, "semester": 3, "code": "CSC201", "name": "Data Structures"},
        )["id"]

        self.teacher_user = self.make_user("teacher1", "TEACHER")
        self.allocation = self.post(
            f"{ACADEMICS}/allocations",
            {
                "batchSemester": self.semester,
                "subject": subject,
                "teacher": self.teacher_user.pk,
            },
        )["id"]

        self.students = [
            self.post(
                f"{STUDENTS}/students",
                {
                    "batch": batch,
                    "rollNumber": f"{index:02d}",
                    "firstName": f"Student{index}",
                    "lastName": "Thapa",
                },
            )["id"]
            for index in (1, 2, 3)
        ]
        self.batch = batch
        self.program = program

    def as_teacher(self):
        """Act as the teacher the class was allocated to."""
        self.client.credentials()
        self.authenticate(self.teacher_user.username)

    def enroll_roster(self, then_teach=True):
        """Coordinator work, then hand over to the teacher who owns the class."""
        self.post(
            f"{STUDENTS}/semester-enrollments/bulk",
            {"batchSemester": self.semester, "students": self.students},
        )
        self.post(
            f"{STUDENTS}/subject-enrollments/bulk",
            {"allocation": self.allocation, "students": self.students},
        )
        enrollments = list(
            SubjectEnrollment.objects.filter(allocation=self.allocation)
            .order_by("student__roll_number")
            .values_list("id", flat=True)
        )

        if then_teach:
            self.as_teacher()

        return enrollments


class EnrollmentTests(WorkflowTestCase):
    def test_student_keeps_primary_and_alternate_phone_on_linked_identity(self):
        student_id = self.post(
            f"{STUDENTS}/students",
            {
                "batch": self.batch,
                "rollNumber": "04",
                "firstName": "Contact",
                "lastName": "Student",
                "phoneNo": "9800000001",
                "alternatePhoneNo": "9800000002",
            },
        )["id"]

        student = Student.objects.select_related("user").get(pk=student_id)
        assert student.phone_no == "9800000001"
        assert student.alternate_phone_no == "9800000002"
        assert student.user.phone_no == student.phone_no
        assert student.user.alternate_phone_no == student.alternate_phone_no

    def test_all_zero_roll_number_is_rejected(self):
        response = self.client.post(
            f"{STUDENTS}/students",
            {
                "batch": self.batch,
                "rollNumber": "00",
                "firstName": "Invalid",
                "lastName": "Student",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["roll_number"] == ["Roll number must be greater than zero."]

    def test_duplicate_roll_number_returns_a_field_error_without_an_orphan_user(self):
        user_count = User.objects.count()

        response = self.client.post(
            f"{STUDENTS}/students",
            {
                "batch": self.batch,
                "rollNumber": "01",
                "firstName": "Duplicate",
                "lastName": "Student",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["roll_number"] == [
            "That roll number is already registered in this batch."
        ]
        assert User.objects.count() == user_count

    def test_student_registration_creates_a_hidden_student_account(self):
        student = Student.objects.select_related("user").get(pk=self.students[0])

        assert student.user is not None
        assert set(student.user.roles.values_list("codename", flat=True)) == {"STUDENT"}
        assert not student.user.has_usable_password()

        accounts = self.client.get(f"{INTERNAL}/user-mod/users?limit=0").json()["results"]
        assert student.user.username not in {row["username"] for row in accounts}

    def test_bulk_promotion_creates_one_enrollment_each(self):
        response = self.post(
            f"{STUDENTS}/semester-enrollments/bulk",
            {"batchSemester": self.semester, "students": self.students},
        )
        assert response["created"] == 3
        assert SemesterEnrollment.objects.count() == 3

    def test_bulk_promotion_is_safe_to_repeat(self):
        payload = {"batchSemester": self.semester, "students": self.students}
        self.post(f"{STUDENTS}/semester-enrollments/bulk", payload)
        response = self.post(f"{STUDENTS}/semester-enrollments/bulk", payload)

        assert response["created"] == 0
        assert response["skipped"] == 3
        assert SemesterEnrollment.objects.count() == 3

    def test_bulk_enrollment_rejects_a_student_from_another_program(self):
        other_program = self.post(
            f"{ACADEMICS}/programs",
            {"department": self.department, "name": "BCA", "code": "BCA"},
        )["id"]
        other_batch = self.post(f"{ACADEMICS}/batches", {"program": other_program, "year": 2079})[
            "id"
        ]
        outsider = self.post(
            f"{STUDENTS}/students",
            {"batch": other_batch, "rollNumber": "99", "firstName": "Out", "lastName": "Sider"},
        )["id"]

        response = self.client.post(
            f"{STUDENTS}/subject-enrollments/bulk",
            {"allocation": self.allocation, "students": [outsider]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_roster_lists_the_students_to_mark(self):
        Student.objects.filter(pk=self.students[0]).update(phone_no="9800000001")
        self.enroll_roster()  # switches to the allocated teacher
        response = self.client.get(f"{PERFORMANCE}/roster?allocation={self.allocation}")

        assert response.status_code == status.HTTP_200_OK
        assert [row["rollNumber"] for row in response.json()] == ["01", "02", "03"]
        assert response.json()[0]["phoneNo"] == "9800000001"

    def test_existing_subject_enrollments_are_visible_to_admin_for_selection(self):
        self.enroll_roster(then_teach=False)
        url = f"{STUDENTS}/subject-enrollments?allocation={self.allocation}&limit=0"

        admin_rows = self.client.get(url).json()["results"]
        assert {row["student"]["id"] for row in admin_rows} == set(self.students)

        self.as_teacher()
        assert self.client.get(url).status_code == status.HTTP_403_FORBIDDEN


class AttendanceTests(WorkflowTestCase):
    def test_updating_attendance_requires_edit_permission_not_only_add(self):
        enrollments = self.enroll_roster()
        payload = {
            "allocation": self.allocation,
            "date": "2026-01-10",
            "entries": [{"enrollment": enrollments[0], "status": "PRESENT"}],
        }
        self.post(f"{PERFORMANCE}/attendance-sessions", payload)

        teacher_role = self.teacher_user.roles.get(codename="TEACHER")
        teacher_role.permissions.remove(Permission.objects.get(codename="edit_attendance"))
        payload["entries"][0]["status"] = "ABSENT"

        response = self.client.post(f"{PERFORMANCE}/attendance-sessions", payload, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert AttendanceRecord.objects.get().status == "PRESENT"

    def test_attendance_date_must_be_within_configured_semester_dates(self):
        enrollments = self.enroll_roster(then_teach=False)
        semester = self.teacher_user.allocations.get(pk=self.allocation).batch_semester
        semester.start_date = "2026-01-05"
        semester.end_date = "2026-01-31"
        semester.save()
        self.as_teacher()

        for date, message in (
            ("2026-01-04", "Attendance cannot be recorded before the semester starts."),
            ("2026-02-01", "Attendance cannot be recorded after the semester ends."),
        ):
            response = self.client.post(
                f"{PERFORMANCE}/attendance-sessions",
                {
                    "allocation": self.allocation,
                    "date": date,
                    "entries": [{"enrollment": enrollments[0], "status": "PRESENT"}],
                },
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.data["date"] == [message]

    def test_one_call_records_a_class_and_the_whole_roster(self):
        Student.objects.filter(pk=self.students[0]).update(phone_no="9800000001")
        enrollments = self.enroll_roster()

        response = self.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-10",
                "entries": [
                    {"enrollment": enrollments[0], "status": "PRESENT"},
                    {"enrollment": enrollments[1], "status": "ABSENT"},
                    {"enrollment": enrollments[2], "status": "LATE"},
                ],
            },
        )
        assert response["marked"] == 3
        assert AttendanceRecord.objects.count() == 3
        detail = self.client.get(f"{PERFORMANCE}/attendance-sessions/{response['id']}").json()
        assert detail["records"][0]["phoneNo"] == "9800000001"

    def test_resubmitting_the_same_date_corrects_rather_than_duplicates(self):
        enrollments = self.enroll_roster()
        payload = {
            "allocation": self.allocation,
            "date": "2026-01-10",
            "entries": [{"enrollment": enrollments[0], "status": "ABSENT"}],
        }
        self.post(f"{PERFORMANCE}/attendance-sessions", payload)

        payload["entries"][0]["status"] = "PRESENT"
        self.post(f"{PERFORMANCE}/attendance-sessions", payload)

        assert AttendanceRecord.objects.count() == 1
        record = AttendanceRecord.objects.first()
        assert record.status == "PRESENT"
        assert record.updated_by_id == self.teacher_user.id
        assert record.history.count() == 2
        assert record.history.first().history_user_id == self.teacher_user.id

    def test_a_student_not_on_the_roster_is_refused(self):
        self.enroll_roster()
        response = self.client.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-10",
                "entries": [{"enrollment": 9999, "status": "PRESENT"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_same_student_cannot_appear_twice_in_one_submission(self):
        enrollments = self.enroll_roster()
        response = self.client.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-10",
                "entries": [
                    {"enrollment": enrollments[0], "status": "PRESENT"},
                    {"enrollment": enrollments[0], "status": "ABSENT"},
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_future_class_is_refused(self):
        enrollments = self.enroll_roster()
        response = self.client.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2099-01-10",
                "entries": [{"enrollment": enrollments[0], "status": "PRESENT"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_list_summarises_how_many_were_present(self):
        enrollments = self.enroll_roster()
        self.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-10",
                "entries": [
                    {"enrollment": enrollments[0], "status": "PRESENT"},
                    {"enrollment": enrollments[1], "status": "ABSENT"},
                ],
            },
        )
        row = self.client.get(f"{PERFORMANCE}/attendance-sessions").json()["results"][0]
        assert row["markedCount"] == 2
        assert row["presentCount"] == 1


class MarksAndAssignmentTests(WorkflowTestCase):
    def test_entering_exam_marks_requires_edit_exam_permission(self):
        enrollments = self.enroll_roster()
        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {
                "allocation": self.allocation,
                "title": "Permission boundary",
                "fullMarks": 20,
                "passMarks": 8,
            },
        )["id"]
        teacher_role = self.teacher_user.roles.get(codename="TEACHER")
        teacher_role.permissions.remove(Permission.objects.get(codename="edit_internal_exam"))

        response = self.client.post(
            f"{PERFORMANCE}/internal-exams/{exam}/marks",
            {"entries": [{"enrollment": enrollments[0], "marksObtained": "12"}]},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not InternalExamMark.objects.exists()

    def test_superuser_can_manage_performance_weights_and_total_is_enforced(self):
        url = f"{PERFORMANCE}/settings/performance-weights"

        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["attendance_weight"] == 20
        assert response.data["class_performance_weight"] == 10
        assert response.data["assignment_weight"] == 30
        assert response.data["assessment_weight"] == 40

        invalid = self.client.put(
            url,
            {
                "attendanceWeight": 25,
                "classPerformanceWeight": 25,
                "assignmentWeight": 25,
                "assessmentWeight": 20,
            },
            format="json",
        )
        assert invalid.status_code == status.HTTP_400_BAD_REQUEST

        updated = self.client.put(
            url,
            {
                "attendanceWeight": 25,
                "classPerformanceWeight": 15,
                "assignmentWeight": 25,
                "assessmentWeight": 35,
            },
            format="json",
        )
        assert updated.status_code == status.HTTP_200_OK
        assert updated.data["assessment_weight"] == 35

        # Every screen that draws an eligibility badge needs the policy, so a
        # teacher may read it. Changing it stays administration.
        self.as_teacher()
        assert self.client.get(url).status_code == status.HTTP_200_OK
        assert (
            self.client.put(url, {"attendanceWeight": 100}, format="json").status_code
            == status.HTTP_403_FORBIDDEN
        )

    def test_attendance_eligibility_threshold_is_tenant_configurable_and_bounded(self):
        url = f"{PERFORMANCE}/settings/performance-weights"

        # Decimals cross the wire as strings here, as they do for exam marks.
        assert self.client.get(url).data["attendance_eligibility_threshold"] == "75.00"

        for rejected in ("101", "-1"):
            response = self.client.put(
                url, {"attendanceEligibilityThreshold": rejected}, format="json"
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, rejected
            assert "attendance_eligibility_threshold" in response.data

        accepted = self.client.put(url, {"attendanceEligibilityThreshold": "80.00"}, format="json")
        assert accepted.status_code == status.HTTP_200_OK
        assert accepted.data["attendance_eligibility_threshold"] == "80.00"
        # Weights are untouched by a threshold-only write.
        assert accepted.data["attendance_weight"] == 20

        assert PerformanceWeightConfiguration.current().eligibility_threshold == 80.0

    def test_reading_the_policy_does_not_create_an_audited_row(self):
        assert not PerformanceWeightConfiguration.objects.exists()

        response = self.client.get(f"{PERFORMANCE}/settings/performance-weights")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["attendance_eligibility_threshold"] == "75.00"
        assert not PerformanceWeightConfiguration.objects.exists()

    def test_assessment_requires_pass_marks(self):
        self.enroll_roster()
        response = self.client.post(
            f"{PERFORMANCE}/internal-exams",
            {
                "allocation": self.allocation,
                "title": "No pass line",
                "fullMarks": 20,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["pass_marks"] == ["This field is required."]

    def test_duplicate_assessment_title_returns_validation_not_a_server_error(self):
        self.enroll_roster()
        payload = {
            "allocation": self.allocation,
            "title": "First Term",
            "fullMarks": 20,
            "passMarks": 8,
        }
        self.post(f"{PERFORMANCE}/internal-exams", payload)

        response = self.client.post(f"{PERFORMANCE}/internal-exams", payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["__all__"] == ["This class already has an exam with that title."]

    def test_marks_are_saved_and_read_back_with_names(self):
        enrollments = self.enroll_roster()
        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {
                "allocation": self.allocation,
                "title": "First Term",
                "examType": "FIRST_TERM",
                "fullMarks": 20,
                "passMarks": 8,
            },
        )["id"]

        response = self.post(
            f"{PERFORMANCE}/internal-exams/{exam}/marks",
            {
                "entries": [
                    {"enrollment": enrollments[0], "marksObtained": "18.50"},
                    {"enrollment": enrollments[1], "isAbsent": True},
                ]
            },
        )
        assert response["saved"] == 2

        read_back = self.client.get(f"{PERFORMANCE}/internal-exams/{exam}/marks").json()
        assert {row["rollNumber"] for row in read_back} == {"01", "02"}
        assert InternalExamMark.objects.filter(is_absent=True).count() == 1
        summary = self.client.get(f"{PERFORMANCE}/internal-exams").json()["results"][0]
        assert summary["markedCount"] == 2
        assert summary["passedCount"] == 1
        assert summary["absentCount"] == 1
        assert summary["averageMarks"] == "18.50"

    def test_marks_above_full_marks_are_refused(self):
        enrollments = self.enroll_roster()
        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {
                "allocation": self.allocation,
                "title": "First Term",
                "fullMarks": 20,
                "passMarks": 8,
            },
        )["id"]

        response = self.client.post(
            f"{PERFORMANCE}/internal-exams/{exam}/marks",
            {"entries": [{"enrollment": enrollments[0], "marksObtained": "25"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class ClassPerformanceTests(WorkflowTestCase):
    def test_teacher_can_rate_update_and_clear_students_with_history(self):
        enrollments = self.enroll_roster()
        url = f"{PERFORMANCE}/class-performance"

        response = self.client.post(
            url,
            {
                "allocation": self.allocation,
                "entries": [
                    {"enrollment": enrollments[0], "score": 8, "remarks": "Consistent"},
                    {"enrollment": enrollments[1], "score": 6},
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["saved"] == 2

        self.client.post(
            url,
            {
                "allocation": self.allocation,
                "entries": [
                    {"enrollment": enrollments[0], "score": 9, "remarks": "Improved"},
                    {"enrollment": enrollments[1], "score": None},
                ],
            },
            format="json",
        )

        rating = ClassPerformanceRating.objects.get(enrollment_id=enrollments[0])
        assert rating.score == 9
        assert rating.updated_by_id == self.teacher_user.id
        assert rating.history.count() == 2
        assert ClassPerformanceRating.objects.filter(is_archived=False).count() == 1

        roster = self.client.get(f"{url}?allocation={self.allocation}").json()
        assert [row["score"] for row in roster] == [9, None, None]

    def test_rating_must_be_between_one_and_ten(self):
        enrollments = self.enroll_roster()
        for score in (0, 11):
            response = self.client.post(
                f"{PERFORMANCE}/class-performance",
                {
                    "allocation": self.allocation,
                    "entries": [{"enrollment": enrollments[0], "score": score}],
                },
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_completed_semester_ratings_are_read_only(self):
        enrollments = self.enroll_roster()
        semester = BatchSemester.objects.get(pk=self.semester)
        semester.status = SemesterStatus.COMPLETED.value
        semester.save()

        assert (
            self.client.get(
                f"{PERFORMANCE}/class-performance?allocation={self.allocation}"
            ).status_code
            == status.HTTP_200_OK
        )
        response = self.client.post(
            f"{PERFORMANCE}/class-performance",
            {
                "allocation": self.allocation,
                "entries": [{"enrollment": enrollments[0], "score": 7}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_other_teacher_cannot_read_or_write_class_ratings(self):
        enrollments = self.enroll_roster(then_teach=False)
        other = self.make_user("performance-outsider", "TEACHER")
        self.client.credentials()
        self.authenticate(other.username)

        assert (
            self.client.get(
                f"{PERFORMANCE}/class-performance?allocation={self.allocation}"
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )
        response = self.client.post(
            f"{PERFORMANCE}/class-performance",
            {
                "allocation": self.allocation,
                "entries": [{"enrollment": enrollments[0], "score": 7}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class MarksAndAssignmentContinuationTests(WorkflowTestCase):
    def test_an_absent_student_cannot_be_given_marks(self):
        enrollments = self.enroll_roster()
        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {
                "allocation": self.allocation,
                "title": "First Term",
                "fullMarks": 20,
                "passMarks": 8,
            },
        )["id"]

        response = self.client.post(
            f"{PERFORMANCE}/internal-exams/{exam}/marks",
            {"entries": [{"enrollment": enrollments[0], "marksObtained": "10", "isAbsent": True}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_assignment_statuses_are_saved_for_the_class(self):
        enrollments = self.enroll_roster()
        assignment = self.post(
            f"{PERFORMANCE}/assignments",
            {
                "allocation": self.allocation,
                "title": "Linked lists",
                "assignedDate": "2026-01-05",
                "dueDate": "2026-01-12",
            },
        )["id"]

        response = self.post(
            f"{PERFORMANCE}/assignments/{assignment}/submissions",
            {
                "entries": [
                    {"enrollment": enrollments[0], "status": "DONE"},
                    {"enrollment": enrollments[1], "status": "PARTIAL", "remarks": "half"},
                    {"enrollment": enrollments[2], "status": "NOT_DONE"},
                ]
            },
        )
        assert response["saved"] == 3

        row = self.client.get(f"{PERFORMANCE}/assignments").json()["results"][0]
        assert row["doneCount"] == 1
        assert row["evaluatedCount"] == 3

    def test_a_due_date_before_the_assigned_date_is_refused(self):
        response = self.client.post(
            f"{PERFORMANCE}/assignments",
            {
                "allocation": self.allocation,
                "title": "Backwards",
                "assignedDate": "2026-01-12",
                "dueDate": "2026-01-05",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TeacherScopeTests(WorkflowTestCase):
    def test_completed_class_remains_visible_but_rejects_new_performance_records(self):
        enrollments = self.enroll_roster()
        self.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-10",
                "entries": [{"enrollment": enrollments[0], "status": "PRESENT"}],
            },
        )

        semester = BatchSemester.objects.get(pk=self.semester)
        semester.status = SemesterStatus.COMPLETED.value
        semester.save()

        classes = self.client.get(f"{PERFORMANCE}/analytics/classes").json()
        assert classes[0]["semesterStatus"] == "COMPLETED"
        assert self.client.get(f"{PERFORMANCE}/attendance-sessions").data["count"] == 1

        response = self.client.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-11",
                "entries": [{"enrollment": enrollments[0], "status": "PRESENT"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["allocation"] == [
            "This semester is completed. Historical class records are read-only."
        ]

        for endpoint, payload in (
            (
                "internal-exams",
                {
                    "allocation": self.allocation,
                    "title": "Late exam",
                    "fullMarks": 20,
                    "passMarks": 8,
                },
            ),
            (
                "assignments",
                {
                    "allocation": self.allocation,
                    "title": "Late assignment",
                    "assignedDate": "2026-01-11",
                },
            ),
        ):
            response = self.client.post(f"{PERFORMANCE}/{endpoint}", payload, format="json")
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "allocation" in response.data

    def test_a_teacher_sees_only_their_own_classes(self):
        enrollments = self.enroll_roster()
        self.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-10",
                "entries": [{"enrollment": enrollments[0], "status": "PRESENT"}],
            },
        )

        # Back to the coordinator to build a second teacher a class of their own.
        self.client.credentials()
        self.authenticate_as_admin()

        other_user = self.make_user("teacher2", "TEACHER")
        other_subject = self.post(
            f"{ACADEMICS}/subjects",
            {"program": self.program, "semester": 3, "code": "CSC202", "name": "DBMS"},
        )["id"]
        self.post(
            f"{ACADEMICS}/allocations",
            {
                "batchSemester": self.semester,
                "subject": other_subject,
                "teacher": other_user.pk,
            },
        )

        self.client.credentials()
        self.authenticate(other_user.username)

        # teacher2 has no sessions of their own, and cannot see teacher1's.
        assert self.client.get(f"{PERFORMANCE}/attendance-sessions").data["count"] == 0

    def test_a_teacher_can_mark_their_own_class(self):
        enrollments = self.enroll_roster()

        response = self.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-11",
                "entries": [{"enrollment": enrollments[0], "status": "PRESENT"}],
            },
        )
        assert response["marked"] == 1

    def test_a_teacher_cannot_enrol_students(self):
        self.as_teacher()

        response = self.client.post(
            f"{STUDENTS}/subject-enrollments/bulk",
            {"allocation": self.allocation, "students": self.students},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_dashboard_has_scoped_oversight_but_no_teaching_workspace(self):
        """
        "My classes" stays allocation-owned, while the universal dashboard
        provides authorized administrative oversight.
        """
        self.enroll_roster(then_teach=False)

        assert self.client.get(f"{PERFORMANCE}/analytics/classes").json() == []
        assert self.client.get(f"{PERFORMANCE}/attendance-sessions").data["count"] == 0

        overview = self.client.get(f"{PERFORMANCE}/analytics/overview").json()
        assert overview["stats"]["totalClasses"] == 1

    def test_an_admin_still_sees_every_class_in_the_allocation_listing(self):
        self.enroll_roster(then_teach=False)

        response = self.client.get(f"{ACADEMICS}/allocations")
        assert response.data["count"] == 1

    def test_a_teacher_cannot_record_against_another_teachers_class(self):
        enrollments = self.enroll_roster(then_teach=False)

        other_user = self.make_user("teacher3", "TEACHER")
        self.client.credentials()
        self.authenticate(other_user.username)

        response = self.client.post(
            f"{PERFORMANCE}/attendance-sessions",
            {
                "allocation": self.allocation,
                "date": "2026-01-10",
                "entries": [{"enrollment": enrollments[0], "status": "PRESENT"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "allocation" in response.data

    def test_a_teacher_cannot_read_another_teachers_roster(self):
        self.enroll_roster(then_teach=False)

        other_user = self.make_user("teacher4", "TEACHER")
        self.client.credentials()
        self.authenticate(other_user.username)

        response = self.client.get(f"{PERFORMANCE}/roster?allocation={self.allocation}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
