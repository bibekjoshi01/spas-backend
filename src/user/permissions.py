from typing import ClassVar

from src.libs.permissions import ModelPermission


class UserPermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = {
        "SAFE_METHODS": "view_user",
        "POST": "add_user",
        "PATCH": "edit_user",
        "DELETE": "delete_user",
    }


class UserRolePermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = {
        "SAFE_METHODS": "view_user_role",
        "POST": "add_user_role",
        "PATCH": "edit_user_role",
        "DELETE": "delete_user_role",
    }
