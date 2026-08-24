"""The teacher's daily workflow: roster, attendance, marks, assignments."""

from rest_framework import status

from src.performance.models import AttendanceRecord, InternalExamMark
from src.students.models import SemesterEnrollment, SubjectEnrollment
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
        teacher = self.post(
            f"{ACADEMICS}/teachers",
            {
                "user": self.teacher_user.pk,
                "department": self.department,
                "designation": "LECTURER",
            },
        )["id"]
        self.allocation = self.post(
            f"{ACADEMICS}/allocations",
            {"batchSemester": self.semester, "subject": subject, "teacher": teacher},
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
        self.enroll_roster()  # switches to the allocated teacher
        response = self.client.get(f"{PERFORMANCE}/roster?allocation={self.allocation}")

        assert response.status_code == status.HTTP_200_OK
        assert [row["rollNumber"] for row in response.json()] == ["01", "02", "03"]


class AttendanceTests(WorkflowTestCase):
    def test_one_call_records_a_class_and_the_whole_roster(self):
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
        assert AttendanceRecord.objects.first().status == "PRESENT"

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

    def test_marks_above_full_marks_are_refused(self):
        enrollments = self.enroll_roster()
        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {"allocation": self.allocation, "title": "First Term", "fullMarks": 20},
        )["id"]

        response = self.client.post(
            f"{PERFORMANCE}/internal-exams/{exam}/marks",
            {"entries": [{"enrollment": enrollments[0], "marksObtained": "25"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_absent_student_cannot_be_given_marks(self):
        enrollments = self.enroll_roster()
        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {"allocation": self.allocation, "title": "First Term", "fullMarks": 20},
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
        other_teacher = self.post(
            f"{ACADEMICS}/teachers", {"user": other_user.pk, "department": self.department}
        )["id"]
        other_subject = self.post(
            f"{ACADEMICS}/subjects",
            {"program": self.program, "semester": 3, "code": "CSC202", "name": "DBMS"},
        )["id"]
        self.post(
            f"{ACADEMICS}/allocations",
            {
                "batchSemester": self.semester,
                "subject": other_subject,
                "teacher": other_teacher,
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

    def test_an_admin_without_an_allocation_has_no_classes(self):
        """
        "My classes" means allocated to me.

        A superuser who teaches nothing sees nothing here — oversight lives in
        the academics allocation listing, not on the teaching screens.
        """
        self.enroll_roster(then_teach=False)

        assert self.client.get(f"{PERFORMANCE}/analytics/classes").json() == []
        assert self.client.get(f"{PERFORMANCE}/attendance-sessions").data["count"] == 0

        overview = self.client.get(f"{PERFORMANCE}/analytics/overview").json()
        assert overview["stats"]["totalClasses"] == 0

    def test_an_admin_still_sees_every_class_in_the_allocation_listing(self):
        self.enroll_roster(then_teach=False)

        response = self.client.get(f"{ACADEMICS}/allocations")
        assert response.data["count"] == 1

    def test_a_teacher_cannot_record_against_another_teachers_class(self):
        enrollments = self.enroll_roster(then_teach=False)

        other_user = self.make_user("teacher3", "TEACHER")
        self.post(f"{ACADEMICS}/teachers", {"user": other_user.pk, "department": self.department})

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
        self.post(f"{ACADEMICS}/teachers", {"user": other_user.pk, "department": self.department})

        self.client.credentials()
        self.authenticate(other_user.username)

        response = self.client.get(f"{PERFORMANCE}/roster?allocation={self.allocation}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
