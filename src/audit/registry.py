"""
What can be audited, and who may see each trail.

Every domain model already writes history through `HistoricalRecords()`. This
module is the one place that says which of those histories are readable, what to
call them, and — the part that matters — which rows of each a given caller is
allowed to see.

Authority is resolved against the *live* queryset rather than re-implemented
over the historical table. The scoping rules are already written and tested for
the live models, and a second implementation over `Historical*` would drift from
them the first time a rule changed. The trade-off is deliberate: a row outside
the caller's scope has no readable history, which is the answer authority should
give anyway.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db.models import QuerySet

from src.academics.models import (
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
)
from src.libs.permissions import scope_to_allocation_owner
from src.libs.scoping import management_scope, scope_by_authority
from src.performance.models import (
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    ClassPerformanceRating,
    InternalExam,
    InternalExamMark,
)
from src.students.models import SemesterEnrollment, Student, SubjectEnrollment


@dataclass(frozen=True)
class AuditResource:
    """One auditable model, and everything the API needs to serve its trail."""

    slug: str
    model: type
    label: str
    #: Permission codename that admits a caller to this trail at all.
    permission: str
    #: Narrows a queryset of the live model to what this caller may read.
    scope: Callable[[QuerySet, object], QuerySet]
    #: Renders one live row as the line a reader identifies it by.
    describe: Callable[[object], str]
    #: `select_related` applied before describing, so labels cost no extra queries.
    related: tuple[str, ...] = ()


# Scope helpers
# ------------------------------------------------------------------------------------


def _by_authority(department_path: str, program_path: str):
    def scope(queryset, user):
        return scope_by_authority(
            queryset, user, department_path=department_path, program_path=program_path
        )

    return scope


def _allocation_scope(department_path: str, program_path: str, owner_path: str):
    """
    Management sees its hierarchy; a teacher sees only their own classes.

    Mirrors how the live listings answer the same question, so a teacher can
    audit their own register without ever seeing a colleague's.
    """

    def scope(queryset, user):
        if not management_scope(user).is_empty:
            return scope_by_authority(
                queryset, user, department_path=department_path, program_path=program_path
            )
        return scope_to_allocation_owner(queryset, user, path=owner_path)

    return scope


# Description helpers
# ------------------------------------------------------------------------------------


def _student_name(student) -> str:
    return f"{student.full_name} ({student.roll_number})"


def _class_label(allocation) -> str:
    semester = allocation.batch_semester
    return (
        f"{allocation.subject.code} · {semester.batch.program.code} "
        f"{semester.batch.year} · Semester {semester.semester}"
    )


# The registry
# ------------------------------------------------------------------------------------

RESOURCES: tuple[AuditResource, ...] = (
    AuditResource(
        slug="department",
        model=Department,
        label="Department",
        permission="view_department",
        scope=_by_authority("id", "programs__id"),
        describe=lambda row: f"{row.code} — {row.name}",
    ),
    AuditResource(
        slug="program",
        model=Program,
        label="Program",
        permission="view_program",
        scope=_by_authority("department_id", "id"),
        describe=lambda row: f"{row.code} — {row.name}",
    ),
    AuditResource(
        slug="batch",
        model=Batch,
        label="Batch",
        permission="view_batch",
        scope=_by_authority("program__department_id", "program_id"),
        related=("program",),
        describe=lambda row: f"{row.program.code} {row.year}",
    ),
    AuditResource(
        slug="batch-semester",
        model=BatchSemester,
        label="Batch semester",
        permission="view_batch_semester",
        scope=_by_authority("batch__program__department_id", "batch__program_id"),
        related=("batch__program",),
        describe=lambda row: f"{row.batch.program.code} {row.batch.year} · Semester {row.semester}",
    ),
    AuditResource(
        slug="subject",
        model=Subject,
        label="Subject",
        permission="view_subject",
        scope=_by_authority("program__department_id", "program_id"),
        related=("program",),
        describe=lambda row: f"{row.code} — {row.name}",
    ),
    AuditResource(
        slug="subject-allocation",
        model=SubjectAllocation,
        label="Class allocation",
        permission="view_subject_allocation",
        scope=_allocation_scope(
            "subject__program__department_id", "subject__program_id", "teacher"
        ),
        related=("subject", "batch_semester__batch__program"),
        describe=_class_label,
    ),
    AuditResource(
        slug="student",
        model=Student,
        label="Student",
        permission="view_student",
        scope=_by_authority("batch__program__department_id", "batch__program_id"),
        related=("batch__program",),
        describe=_student_name,
    ),
    AuditResource(
        slug="semester-enrollment",
        model=SemesterEnrollment,
        label="Semester enrollment",
        permission="view_semester_enrollment",
        scope=_by_authority(
            "batch_semester__batch__program__department_id", "batch_semester__batch__program_id"
        ),
        related=("student", "batch_semester__batch__program"),
        describe=lambda row: (
            f"{_student_name(row.student)} · Semester {row.batch_semester.semester}"
        ),
    ),
    AuditResource(
        slug="subject-enrollment",
        model=SubjectEnrollment,
        label="Class registration",
        permission="view_subject_enrollment",
        scope=_allocation_scope(
            "allocation__subject__program__department_id",
            "allocation__subject__program_id",
            "allocation__teacher",
        ),
        related=("student", "allocation__subject", "allocation__batch_semester__batch__program"),
        describe=lambda row: f"{_student_name(row.student)} · {row.allocation.subject.code}",
    ),
    AuditResource(
        slug="attendance-session",
        model=AttendanceSession,
        label="Attendance session",
        permission="view_attendance",
        scope=_allocation_scope(
            "allocation__subject__program__department_id",
            "allocation__subject__program_id",
            "allocation__teacher",
        ),
        related=("allocation__subject", "allocation__batch_semester__batch__program"),
        describe=lambda row: f"{_class_label(row.allocation)} · {row.date} (period {row.period})",
    ),
    AuditResource(
        slug="attendance-record",
        model=AttendanceRecord,
        label="Attendance mark",
        permission="view_attendance",
        scope=_allocation_scope(
            "session__allocation__subject__program__department_id",
            "session__allocation__subject__program_id",
            "session__allocation__teacher",
        ),
        related=("enrollment__student", "session__allocation__subject"),
        describe=lambda row: (
            f"{_student_name(row.enrollment.student)} · "
            f"{row.session.allocation.subject.code} · {row.session.date}"
        ),
    ),
    AuditResource(
        slug="internal-exam",
        model=InternalExam,
        label="Internal exam",
        permission="view_internal_exam",
        scope=_allocation_scope(
            "allocation__subject__program__department_id",
            "allocation__subject__program_id",
            "allocation__teacher",
        ),
        related=("allocation__subject", "allocation__batch_semester__batch__program"),
        describe=lambda row: f"{row.title} · {_class_label(row.allocation)}",
    ),
    AuditResource(
        slug="internal-exam-mark",
        model=InternalExamMark,
        label="Internal exam mark",
        permission="view_internal_exam",
        scope=_allocation_scope(
            "exam__allocation__subject__program__department_id",
            "exam__allocation__subject__program_id",
            "exam__allocation__teacher",
        ),
        related=("enrollment__student", "exam__allocation__subject"),
        describe=lambda row: f"{_student_name(row.enrollment.student)} · {row.exam.title}",
    ),
    AuditResource(
        slug="assignment",
        model=Assignment,
        label="Assignment",
        permission="view_assignment",
        scope=_allocation_scope(
            "allocation__subject__program__department_id",
            "allocation__subject__program_id",
            "allocation__teacher",
        ),
        related=("allocation__subject", "allocation__batch_semester__batch__program"),
        describe=lambda row: f"{row.title} · {_class_label(row.allocation)}",
    ),
    AuditResource(
        slug="assignment-submission",
        model=AssignmentSubmission,
        label="Assignment submission",
        permission="view_assignment",
        scope=_allocation_scope(
            "assignment__allocation__subject__program__department_id",
            "assignment__allocation__subject__program_id",
            "assignment__allocation__teacher",
        ),
        related=("enrollment__student", "assignment__allocation__subject"),
        describe=lambda row: f"{_student_name(row.enrollment.student)} · {row.assignment.title}",
    ),
    AuditResource(
        slug="class-performance",
        model=ClassPerformanceRating,
        label="Class performance rating",
        permission="view_class_performance",
        scope=_allocation_scope(
            "allocation__subject__program__department_id",
            "allocation__subject__program_id",
            "allocation__teacher",
        ),
        related=("enrollment__student", "allocation__subject"),
        describe=lambda row: (
            f"{_student_name(row.enrollment.student)} · {row.allocation.subject.code}"
        ),
    ),
)

BY_SLUG: dict[str, AuditResource] = {resource.slug: resource for resource in RESOURCES}


def readable_resources(permissions: set[str]) -> list[AuditResource]:
    """The trails this caller may open, in registry order."""
    return [resource for resource in RESOURCES if resource.permission in permissions]
