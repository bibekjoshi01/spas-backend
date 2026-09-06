from typing import ClassVar

from rest_framework.permissions import BasePermission

from src.libs.permissions import ModelPermission, is_staff_account

from .constants import StudentStatus
from .models import StudentPortalConfiguration


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


class StudentPortalPermission(BasePermission):
    """Admit only the linked student, never a caller-supplied student id."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and student_portal_access_error(user) is None)


class StudentPortalSettingsPermission(BasePermission):
    def has_permission(self, request, view):
        return is_staff_account(request.user) and request.user.is_superuser


def student_portal_access_error(user, *, allow_initial_password_change=False) -> str | None:
    """Return why a student account cannot use its portal, without trusting token claims."""
    if not user.is_active or user.is_archived:
        return "This account is disabled. Contact your college administrator."
    if not user.roles.filter(codename="STUDENT").exists():
        return "This account is not a student account."
    if not StudentPortalConfiguration.current().login_enabled:
        return "Student login is disabled for this college."

    student = getattr(user, "student_profile", None)
    if student is None or not student.is_active or student.is_archived:
        return "Student login is unavailable for this account. Contact your college administrator."
    if student.status != StudentStatus.STUDYING.value:
        return (
            "Student login is available only to currently studying students. "
            f"This student is {student.get_status_display().lower()}."
        )
    if user.must_change_password and not allow_initial_password_change:
        return "Change the temporary password before opening student records."
    return None
