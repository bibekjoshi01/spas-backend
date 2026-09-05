"""Read-only calendar dates derived from authorized academic records."""

from src.libs.permissions import get_permissions_for_user
from src.libs.scoping import management_scope, scope_by_authority


def calendar_agenda(user, start, end):
    from src.performance.models import Assignment, ClassScheduleChange, InternalExam
    from src.students.models import SemesterEnrollment, SubjectEnrollment

    from .models import BatchSemester, SubjectAllocation

    student = user.roles.filter(codename="STUDENT").exists()
    permissions = set(get_permissions_for_user(user))
    allocations = SubjectAllocation.objects.filter(is_archived=False)
    semesters = BatchSemester.objects.filter(is_archived=False)
    if student:
        allocations = allocations.filter(
            enrollments__in=SubjectEnrollment.objects.filter(
                student__user=user,
                is_archived=False,
                is_active=True,
            )
        ).distinct()
        semesters = semesters.filter(
            pk__in=SemesterEnrollment.objects.filter(
                student__user=user,
                is_archived=False,
            ).values("batch_semester_id")
        )
    elif not management_scope(user).is_empty:
        allocations = scope_by_authority(
            allocations,
            user,
            department_path="subject__program__department_id",
            program_path="subject__program_id",
        )
        semesters = scope_by_authority(
            semesters,
            user,
            department_path="batch__program__department_id",
            program_path="batch__program_id",
        )
    else:
        allocations = allocations.filter(teacher=user)
        semesters = semesters.filter(pk__in=allocations.values("batch_semester_id"))

    result = {}

    def add(date, key, kind, title):
        if date and start <= date <= end:
            result.setdefault(date, []).append({"key": key, "kind": kind, "title": title})

    for semester in (
        semesters.filter(start_date__range=(start, end))
        | semesters.filter(end_date__range=(start, end))
    ).select_related("batch__program"):
        label = (
            f"{semester.batch.program.code} {semester.batch.year} · Semester {semester.semester}"
        )
        add(semester.start_date, f"semester-{semester.pk}-start", "SEMESTER", f"{label} starts")
        add(semester.end_date, f"semester-{semester.pk}-end", "SEMESTER", f"{label} ends")
    if student or user.is_superuser or "view_internal_exam" in permissions:
        for exam in InternalExam.objects.filter(
            allocation__in=allocations,
            is_archived=False,
            is_active=True,
            exam_date__range=(start, end),
        ).select_related("allocation__subject"):
            add(
                exam.exam_date,
                f"exam-{exam.pk}",
                "EXAM",
                f"{exam.allocation.subject.code} · {exam.title}",
            )
    if student or user.is_superuser or "view_assignment" in permissions:
        for assignment in Assignment.objects.filter(
            allocation__in=allocations,
            is_archived=False,
            is_active=True,
            due_date__range=(start, end),
        ).select_related("allocation__subject"):
            add(
                assignment.due_date,
                f"assignment-{assignment.pk}",
                "DEADLINE",
                f"{assignment.allocation.subject.code} · {assignment.title} due",
            )
    if student or user.is_superuser or "view_attendance" in permissions:
        for change in ClassScheduleChange.objects.filter(
            allocation__in=allocations, is_archived=False, is_active=True, date__range=(start, end)
        ).select_related("allocation__subject"):
            add(
                change.date,
                f"schedule-{change.pk}",
                change.kind,
                f"{change.allocation.subject.code} · {change.get_kind_display()}",
            )
    return result
