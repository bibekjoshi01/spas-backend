from typing import ClassVar

from src.libs.permissions import ModelPermission, get_permissions_for_user


def _map(resource: str) -> dict[str, object]:
    return {
        "SAFE_METHODS": f"view_{resource}",
        "POST": f"add_{resource}",
        "PATCH": f"edit_{resource}",
        "DELETE": f"delete_{resource}",
    }


class AttendancePermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("attendance")

    def has_permission(self, request, view):
        if request.method != "POST":
            return super().has_permission(request, view)
        permissions = set(get_permissions_for_user(request.user))
        return request.user.is_superuser or bool(
            {"add_attendance", "edit_attendance"} & permissions
        )


class InternalExamPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("internal_exam")


class InternalExamMarkPermission(InternalExamPermission):
    permission_map: ClassVar[dict[str, object]] = {
        "SAFE_METHODS": "view_internal_exam",
        "POST": "edit_internal_exam",
    }


class AssignmentPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("assignment")


class AssignmentSubmissionPermission(AssignmentPermission):
    permission_map: ClassVar[dict[str, object]] = {
        "SAFE_METHODS": "view_assignment",
        "POST": "edit_assignment",
    }


class ClassPerformancePermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = {
        "SAFE_METHODS": "view_class_performance",
        "POST": "edit_class_performance",
    }
