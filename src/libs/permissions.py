from typing import Any, ClassVar, TypeGuard

from rest_framework.permissions import SAFE_METHODS, BasePermission

from src.user.models import Permission, User


def is_staff_account(user: Any) -> TypeGuard[User]:
    """Student identity takes precedence over accidentally attached staff roles."""
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and not user.is_archived
        and not user.roles.filter(codename="STUDENT").exists()
    )


def get_permissions_for_user(user: User | None) -> list[str]:
    """
    Every permission codename the user may act on.

    A superuser holds all of them, which is what the frontend needs in order to
    render the full menu without special-casing.
    """
    if not is_staff_account(user):
        return []

    queryset = Permission.objects.filter(is_active=True)

    if not user.is_superuser:
        queryset = queryset.filter(
            userrole__in=user.roles.filter(is_active=True, is_archived=False)
        )

    return sorted(set(queryset.values_list("codename", flat=True)))


def get_role_permissions(request: Any) -> list[str]:
    return get_permissions_for_user(getattr(request, "user", None))


def validate_permissions(request: Any, user_permissions_dict: dict[str, object]) -> bool:
    if not is_staff_account(request.user):
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


OVERSIGHT_PERMISSION = "add_subject_allocation"


def scope_to_allocation_owner(
    queryset,
    user,
    path: str = "allocation__teacher",
    *,
    strict: bool = True,
):
    """
    Narrow a queryset to the classes allocated to the user.

    Being *allocated* a class and *holding a role* are different things: anyone
    can be given a class to teach, including the head of department, and the
    Teacher role only says what they may record on it. So the teaching surfaces
    — my classes, attendance, exams, assignments — are always the caller's own
    allocations, superuser included. "My classes" means mine.

    `strict=False` is for the oversight listing under Academics, where whoever
    may allocate classes needs to see all of them.
    """
    if user is None or user.is_anonymous:
        return queryset.none()

    if not strict and (user.is_superuser or OVERSIGHT_PERMISSION in get_permissions_for_user(user)):
        return queryset

    return queryset.filter(**{path: user})


class AllocationOwnerScopedQuerysetMixin:
    """
    Narrow a list to the requesting user's own allocations.

    Teaching screens are always the caller's own allocations. Set
    `strict_scope = False` on the oversight listing, where whoever may allocate
    classes needs to see all of them.
    """

    owner_scope_path: str = "allocation__teacher"
    strict_scope: bool = True

    def get_queryset(self):
        return scope_to_allocation_owner(
            super().get_queryset(),
            getattr(self.request, "user", None),
            self.owner_scope_path,
            strict=self.strict_scope,
        )
