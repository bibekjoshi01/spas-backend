from typing import ClassVar

from src.libs.permissions import ModelPermission


def _map(resource: str) -> dict[str, object]:
    return {
        "SAFE_METHODS": f"view_{resource}",
        "POST": f"add_{resource}",
        "PATCH": f"edit_{resource}",
        "DELETE": f"delete_{resource}",
    }


class AttendancePermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("attendance")


class InternalExamPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("internal_exam")


class AssignmentPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("assignment")


class ClassPerformancePermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("class_performance")
