from typing import ClassVar

from src.libs.permissions import ModelPermission


def _map(resource: str) -> dict[str, object]:
    return {
        "SAFE_METHODS": f"view_{resource}",
        "POST": f"add_{resource}",
        "PATCH": f"edit_{resource}",
        "DELETE": f"delete_{resource}",
    }


class StudentPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("student")


class SemesterEnrollmentPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("semester_enrollment")


class SubjectEnrollmentPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = _map("subject_enrollment")
