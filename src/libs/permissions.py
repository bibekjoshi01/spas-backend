from typing import Any

from rest_framework.permissions import SAFE_METHODS

from src.user.models import Permission, User


def get_role_permissions(request: Any) -> list[str]:
    user: User = request.user
    if not user or user.is_anonymous:
        return []

    # roles is M2M (Role model)
    roles = user.roles.filter(is_system_managed__in=[True, False])

    # get all permissions directly using prefetch_related
    permissions = (
        Permission.objects.filter(userrole__in=roles).values_list("codename", flat=True).distinct()
    )

    return list(permissions)


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
