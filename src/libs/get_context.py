"""Helpers for reaching the acting user from serializer context or a request."""

from typing import Any, cast

from src.user.models import User


def get_user_by_request(request: Any) -> User | None:
    """Return the authenticated user on a request, or None for anonymous."""
    user = getattr(request, "user", None)

    if user is None or user.is_anonymous:
        return None

    return cast("User", user)


def get_user_by_context(context: dict[str, Any]) -> User | None:
    """
    Return the acting user from serializer context.

    Serializers never touch `request` directly — they take the user from the
    context DRF already builds, so they stay usable outside a request cycle.
    """
    request = context.get("request")

    if request is None:
        return None

    return get_user_by_request(request)
