from typing import Any

from rest_framework.permissions import BasePermission


class IsPlatformUser(BasePermission):
    """
    Only an active platform administrator.

    Fails closed. Anything that is not a `PlatformUser` — an anonymous
    request, or a college user arriving through the project-wide DRF
    authentication defaults — lacks `is_platform_admin` and is refused.
    """

    def has_permission(self, request: Any, view: Any) -> bool:
        user = getattr(request, "user", None)

        return bool(
            user is not None
            and getattr(user, "is_active", False)
            and getattr(user, "is_platform_admin", False)
        )
