from typing import ClassVar

from src.libs.permissions import ModelPermission


def _map(resource: str) -> dict[str, object]:
    return {
        "SAFE_METHODS": f"view_{resource}",
        "POST": f"add_{resource}",
        "PATCH": f"edit_{resource}",
        "DELETE": f"delete_{resource}",
    }


class DepartmentPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("department")


class ProgramPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("program")


class BatchPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = {
        "SAFE_METHODS": "view_batch",
        "POST": "__superuser_only__",
        "PATCH": "__superuser_only__",
        "DELETE": "__superuser_only__",
    }


class BatchSemesterPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("batch_semester")


class SubjectPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("subject")


class SubjectAllocationPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("subject_allocation")


class AcademicCalendarEntryPermission(ModelPermission):
    """
    Anyone signed in may read the calendar; only an administrator marks it.

    Term dates, holidays and events are what the rest of the college schedules
    around, so a head, a coordinator and a teacher all need to see them. Saying
    what the college closes for is administration.
    """

    permission_map: ClassVar[dict[str, object]] = {
        "SAFE_METHODS": "view_academic_calendar",
        "POST": "__superuser_only__",
        "PATCH": "__superuser_only__",
        "DELETE": "__superuser_only__",
    }
