"""
Schema-level guarantees for SPAS.

These exercise the cases that are easy to get wrong in a semester system: the
same student meeting the same subject twice, a batch moving forward, and the
constraints that stop bad rows reaching the database on a bulk write path.
"""

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django_tenants.test.cases import TenantTestCase

from src.academics.constants import SemesterStatus
from src.academics.models import (
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
)
from src.performance.constants import AttendanceStatus
from src.performance.models import (
    AttendanceRecord,
    AttendanceSession,
    InternalExam,
    InternalExamMark,
)
from src.students.constants import SemesterEnrollmentStatus
from src.students.models import SemesterEnrollment, Student, SubjectEnrollment
from src.user.models import User


class SPASSchemaTestCase(TenantTestCase):
    """Base fixture: one college, one program, one teacher, two batches."""

    @staticmethod
    def get_test_schema_name():
        return "test_spas"

    @staticmethod
    def get_test_tenant_domain():
        return "spas.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Test College"
        tenant.subdomain = "test-spas"
        return tenant

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="coordinator", email="coordinator@test.com", password="pass1234"
        )
        self.department = Department.objects.create(
            name="Computer Science", code="CSIT", created_by=self.user
        )
        self.program = Program.objects.create(
            department=self.department,
            name="B.Sc. CSIT",
            code="BSCCSIT",
            total_semesters=8,
            created_by=self.user,
        )
        self.teacher = self.user
        self.batch_2079 = Batch.objects.create(
            program=self.program, year=2079, created_by=self.user
        )
        self.batch_2080 = Batch.objects.create(
            program=self.program, year=2080, created_by=self.user
        )

    # helpers -----------------------------------------------------------

    def make_semester(self, batch, number, status=SemesterStatus.COMPLETED.value):
        return BatchSemester.objects.create(
            batch=batch, semester=number, status=status, created_by=self.user
        )

    def make_subject(self, code, name, semester):
        return Subject.objects.create(
            program=self.program,
            code=code,
            name=name,
            semester=semester,
            created_by=self.user,
        )

    def make_allocation(self, batch_semester, subject):
        return SubjectAllocation.objects.create(
            batch_semester=batch_semester,
            subject=subject,
            teacher=self.teacher,
            created_by=self.user,
        )

    def make_student(self, roll, batch=None):
        student_user = User.objects.create_user(
            username=f"student-{(batch or self.batch_2079).id}-{roll}",
            email=f"student-{(batch or self.batch_2079).id}-{roll}@test.edu",
            password=None,
            include_system_role=False,
        )
        return Student.objects.create(
            user=student_user,
            batch=batch or self.batch_2079,
            roll_number=roll,
            first_name="Ram",
            last_name="Thapa",
            created_by=self.user,
        )

    def mark_attendance(self, allocation, enrollment, date, status):
        session = AttendanceSession.objects.create(
            allocation=allocation, date=date, created_by=self.user
        )
        return AttendanceRecord.objects.create(
            session=session, enrollment=enrollment, status=status, created_by=self.user
        )


class SameSubjectAcrossSemestersTests(SPASSchemaTestCase):
    """The case the design has to survive: one student meets one subject twice."""

    def test_retaking_the_same_subject_with_a_junior_batch(self):
        dsa = self.make_subject("CSC201", "Data Structures", 3)
        student = self.make_student("21")

        # First attempt: batch 2079, semester 3.
        sem_2079_3 = self.make_semester(self.batch_2079, 3)
        alloc_2079 = self.make_allocation(sem_2079_3, dsa)
        first = SubjectEnrollment.objects.create(
            student=student, allocation=alloc_2079, created_by=self.user
        )
        self.mark_attendance(
            alloc_2079, first, datetime.date(2024, 1, 10), AttendanceStatus.ABSENT.value
        )

        # Failed it. Retakes with batch 2080 the following year — same subject,
        # same student, a different semester of a different batch.
        sem_2080_3 = self.make_semester(self.batch_2080, 3, SemesterStatus.RUNNING.value)
        alloc_2080 = self.make_allocation(sem_2080_3, dsa)
        second = SubjectEnrollment.objects.create(
            student=student,
            allocation=alloc_2080,
            is_retake=True,
            created_by=self.user,
        )
        self.mark_attendance(
            alloc_2080, second, datetime.date(2025, 1, 10), AttendanceStatus.PRESENT.value
        )

        # Two independent enrollments, two independent attendance histories.
        assert SubjectEnrollment.objects.filter(student=student).count() == 2
        assert first.attendance_records.count() == 1
        assert second.attendance_records.count() == 1
        assert first.attendance_records.first().status == AttendanceStatus.ABSENT.value
        assert second.attendance_records.first().status == AttendanceStatus.PRESENT.value

        # And the attempts stay distinguishable for analysis.
        assert second.is_retake is True
        assert first.allocation.batch_semester.batch != second.allocation.batch_semester.batch

    def test_student_row_is_untouched_by_progression(self):
        student = self.make_student("21")
        original = Student.objects.get(pk=student.pk)

        for number in (1, 2, 3):
            semester = self.make_semester(self.batch_2079, number)
            SemesterEnrollment.objects.create(
                student=student,
                batch_semester=semester,
                status=SemesterEnrollmentStatus.COMPLETED.value,
                created_by=self.user,
            )

        student.refresh_from_db()
        assert student.roll_number == original.roll_number
        assert student.batch_id == original.batch_id
        assert student.semester_enrollments.count() == 3

    def test_same_subject_taught_to_two_batches_at_once(self):
        dsa = self.make_subject("CSC201", "Data Structures", 3)
        alloc_a = self.make_allocation(self.make_semester(self.batch_2079, 3), dsa)
        alloc_b = self.make_allocation(self.make_semester(self.batch_2080, 3), dsa)

        student_a = self.make_student("21", self.batch_2079)
        student_b = self.make_student("07", self.batch_2080)

        SubjectEnrollment.objects.create(
            student=student_a, allocation=alloc_a, created_by=self.user
        )
        SubjectEnrollment.objects.create(
            student=student_b, allocation=alloc_b, created_by=self.user
        )

        assert alloc_a.enrollments.count() == 1
        assert alloc_b.enrollments.count() == 1

    def test_curriculum_revision_can_move_a_subject_between_semesters(self):
        """Both curricula run at once during a transition year."""
        old = self.make_subject("CSC201", "Data Structures", 3)
        new = self.make_subject("CSC201", "Data Structures", 4)

        self.make_allocation(self.make_semester(self.batch_2079, 3), old)
        self.make_allocation(self.make_semester(self.batch_2080, 4), new)

        assert Subject.objects.filter(code="CSC201").count() == 2


class ConstraintTests(SPASSchemaTestCase):
    """The database refuses these even when clean() is skipped on a bulk write."""

    def test_one_student_cannot_be_marked_twice_in_one_class(self):
        subject = self.make_subject("CSC101", "C Programming", 1)
        allocation = self.make_allocation(self.make_semester(self.batch_2079, 1), subject)
        enrollment = SubjectEnrollment.objects.create(
            student=self.make_student("21"), allocation=allocation, created_by=self.user
        )
        session = AttendanceSession.objects.create(
            allocation=allocation, date=datetime.date(2024, 1, 10), created_by=self.user
        )
        AttendanceRecord.objects.create(
            session=session,
            enrollment=enrollment,
            status=AttendanceStatus.PRESENT.value,
            created_by=self.user,
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.create(
                session=session,
                enrollment=enrollment,
                status=AttendanceStatus.ABSENT.value,
                created_by=self.user,
            )

    def test_recorded_history_protects_its_parent_rows_from_physical_deletion(self):
        subject = self.make_subject("CSC101", "C Programming", 1)
        semester = self.make_semester(self.batch_2079, 1)
        allocation = self.make_allocation(semester, subject)
        enrollment = SubjectEnrollment.objects.create(
            student=self.make_student("21"),
            allocation=allocation,
            created_by=self.user,
        )
        session = AttendanceSession.objects.create(
            allocation=allocation,
            date=datetime.date(2024, 1, 10),
            created_by=self.user,
        )
        AttendanceRecord.objects.create(
            session=session,
            enrollment=enrollment,
            status=AttendanceStatus.PRESENT.value,
            created_by=self.user,
        )

        with pytest.raises(ProtectedError):
            session.delete()
        with pytest.raises(ProtectedError):
            enrollment.delete()
        with pytest.raises(ProtectedError):
            allocation.delete()

    def test_a_batch_cannot_run_two_semesters_at_once(self):
        """BatchSemester.save() runs full_clean(), so this surfaces before the DB."""
        self.make_semester(self.batch_2079, 3, SemesterStatus.RUNNING.value)

        with pytest.raises(ValidationError), transaction.atomic():
            BatchSemester.objects.create(
                batch=self.batch_2079,
                semester=4,
                status=SemesterStatus.RUNNING.value,
                created_by=self.user,
            )

    def test_an_absent_student_cannot_carry_marks(self):
        subject = self.make_subject("CSC101", "C Programming", 1)
        allocation = self.make_allocation(self.make_semester(self.batch_2079, 1), subject)
        enrollment = SubjectEnrollment.objects.create(
            student=self.make_student("21"), allocation=allocation, created_by=self.user
        )
        exam = InternalExam.objects.create(
            allocation=allocation, title="First Term", full_marks=20, created_by=self.user
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            InternalExamMark.objects.create(
                exam=exam,
                enrollment=enrollment,
                marks_obtained=15,
                is_absent=True,
                created_by=self.user,
            )

    def test_marks_cannot_be_negative(self):
        subject = self.make_subject("CSC101", "C Programming", 1)
        allocation = self.make_allocation(self.make_semester(self.batch_2079, 1), subject)
        enrollment = SubjectEnrollment.objects.create(
            student=self.make_student("21"), allocation=allocation, created_by=self.user
        )
        exam = InternalExam.objects.create(
            allocation=allocation, title="First Term", full_marks=20, created_by=self.user
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            InternalExamMark.objects.create(
                exam=exam, enrollment=enrollment, marks_obtained=-1, created_by=self.user
            )

    def test_allocation_rejects_a_subject_from_another_semester(self):
        subject = self.make_subject("CSC401", "Operating Systems", 4)
        semester = self.make_semester(self.batch_2079, 3)

        with pytest.raises(ValidationError):
            SubjectAllocation.objects.create(
                batch_semester=semester,
                subject=subject,
                teacher=self.teacher,
                created_by=self.user,
            )

    def test_roll_number_is_unique_within_a_batch_but_not_across(self):
        self.make_student("21", self.batch_2079)
        self.make_student("21", self.batch_2080)  # different batch — fine

        with pytest.raises(IntegrityError), transaction.atomic():
            self.make_student("21", self.batch_2079)
