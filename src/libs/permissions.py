from typing import Any, ClassVar

from rest_framework.permissions import SAFE_METHODS, BasePermission

from src.user.models import Permission, User


def get_permissions_for_user(user: User | None) -> list[str]:
    """
    Every permission codename the user may act on.

    A superuser holds all of them, which is what the frontend needs in order to
    render the full menu without special-casing.
    """
    if user is None or user.is_anonymous or not user.is_active:
        return []

    queryset = Permission.objects.filter(is_active=True)

    if not user.is_superuser:
        queryset = queryset.filter(userrole__in=user.roles.all())

    return sorted(set(queryset.values_list("codename", flat=True)))


def get_role_permissions(request: Any) -> list[str]:
    return get_permissions_for_user(getattr(request, "user", None))


def validate_permissions(request: Any, user_permissions_dict: dict[str, object]) -> bool:
    if request.user.is_anonymous:
        return False

    if not request.user.is_active:
        return False

    if request.user.is_superuser:
        return True

    role_permissions = get_role_permissions(request)

    method = request.method
    if method in SAFE_METHODS:
        method = "SAFE_METHODS"

    method_permission = user_permissions_dict.get(method)
    return bool(method_permission and method_permission in role_permissions)


class ModelPermission(BasePermission):
    """
    Base for resource permissions.

    Subclasses set `permission_map`, keyed by HTTP method with "SAFE_METHODS"
    standing in for every read. One codename per operation, so a role grants
    exactly the API calls it should.
    """

    permission_map: ClassVar[dict[str, object]] = {}

    def has_permission(self, request: Any, view: Any) -> bool:
        return validate_permissions(request, self.permission_map)


class TeacherScopedQuerysetMixin:
    """
    Narrow a list to the requesting teacher's own allocations.

    The rule, stated once: if you may manage the resource you see all of it; if
    you may only view it, you see what is allocated to you. Superusers always
    see everything.
    """

    teacher_scope_path: str = "allocation__teacher__user"
    manage_permission: str = ""

    def get_queryset(self):
        queryset = super().get_queryset()
        user = getattr(self.request, "user", None)

        if user is None or user.is_anonymous or user.is_superuser:
            return queryset

        if self.manage_permission and self.manage_permission in get_permissions_for_user(user):
            return queryset

        return queryset.filter(**{self.teacher_scope_path: user})
