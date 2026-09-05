"""Read-only calendar dates derived from authorized academic records."""

from src.libs.scoping import management_scope, scope_by_authority


def calendar_agenda(user, start, end):
    from src.students.models import SemesterEnrollment, SubjectEnrollment

    from .models import BatchSemester, SubjectAllocation

    student = user.roles.filter(codename="STUDENT").exists()
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
    return result
