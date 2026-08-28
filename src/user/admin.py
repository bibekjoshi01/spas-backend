from typing import Any, cast

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import User

admin.site.unregister(Group)


# ------------------------------
# ROLE ADMIN
# ------------------------------
# @admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "codename", "created_at")
    search_fields = ("name", "codename")
    ordering = ("name",)


# ------------------------------
# USER ADMIN
# ------------------------------
# @admin.register(User)
class UserAdmin(BaseUserAdmin):
    model = User

    # --- Display ---
    list_display = (
        "username",
        "email",
        "full_name",
        "phone_no",
        "alternate_phone_no",
        "auth_provider",
        "is_active",
        "is_staff",
        "date_joined",
        "photo_preview",
    )

    list_filter = (
        "is_active",
        "auth_provider",
        "roles",
    )

    search_fields = (
        "username",
        "email",
        "first_name",
        "middle_name",
        "last_name",
        "phone_no",
    )

    ordering = ("-id",)
    list_per_page = 20

    readonly_fields = (
        "date_joined",
        "updated_at",
        "archived_at",
        "last_login",
        "photo_preview",
    )

    filter_horizontal = ("roles",)

    # --- FIELDSETS (Edit User Page) ---
    fieldsets = (
        (_("Login Details"), {"fields": ("username", "email", "password")}),
        (
            _("Personal Info"),
            {
                "fields": (
                    "first_name",
                    "middle_name",
                    "last_name",
                    "phone_no",
                    "photo",
                    "photo_preview",
                )
            },
        ),
        (
            _("System Status"),
            {
                "fields": (
                    "is_active",
                    "is_archived",
                    "is_staff",
                    "is_superuser",
                    "roles",
                    "auth_provider",
                )
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": ("date_joined", "updated_at", "archived_at", "last_login"),
            },
        ),
        (_("Created By"), {"fields": ("created_by",)}),
    )

    # --- FIELDSETS (Add User Page) ---
    add_fieldsets = (
        (
            _("Create User"),
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "phone_no",
                    "photo",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "roles",
                ),
            },
        ),
    )

    # --- Methods ---
    def full_name(self, obj):
        return obj.get_full_name

    cast(Any, full_name).short_description = "Full Name"

    def photo_preview(self, obj):
        if not obj.photo:
            return "No Photo"
        return format_html(
            '<img src="{}" width="60" height="60" style="border-radius: 6px; object-fit: cover;" />',
            obj.photo.url,
        )

    cast(Any, photo_preview).short_description = "Preview"
