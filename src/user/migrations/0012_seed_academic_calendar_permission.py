# ruff: noqa: RUF012

from django.db import migrations

CATEGORY_CODENAME = "ACADEMIC_CALENDAR_MANAGEMENT"
PERMISSION_CODENAME = "view_academic_calendar"
READING_ROLES = ("DEPARTMENT-HEAD", "PROGRAM-COORDINATOR", "TEACHER")


def seed_academic_calendar_permission(apps, schema_editor):
    """
    Give existing tenants the calendar read permission.

    The fixtures cover a schema created from now on; a college already running
    has loaded them once and will not load them again, so the row has to be
    made here or the calendar would 403 for everyone but a superuser.
    """
    MainModule = apps.get_model("user", "MainModule")
    PermissionCategory = apps.get_model("user", "PermissionCategory")
    Permission = apps.get_model("user", "Permission")
    UserRole = apps.get_model("user", "UserRole")

    academics = MainModule.objects.filter(codename="ACADEMICS").first()
    if academics is None:
        # A schema without the shipped modules has never been seeded at all;
        # its first fixture load will carry the calendar rows with it.
        return

    category, _ = PermissionCategory.objects.get_or_create(
        codename=CATEGORY_CODENAME,
        defaults={
            "name": "Academic Calendar Management",
            "main_module": academics,
            "is_active": True,
        },
    )
    permission, _ = Permission.objects.get_or_create(
        codename=PERMISSION_CODENAME,
        defaults={
            "name": "View Academic Calendar",
            "permission_category": category,
            "is_active": True,
        },
    )
    for role in UserRole.objects.filter(codename__in=READING_ROLES):
        role.permissions.add(permission)


def drop_academic_calendar_permission(apps, schema_editor):
    Permission = apps.get_model("user", "Permission")
    PermissionCategory = apps.get_model("user", "PermissionCategory")
    Permission.objects.filter(codename=PERMISSION_CODENAME).delete()
    PermissionCategory.objects.filter(codename=CATEGORY_CODENAME).delete()


class Migration(migrations.Migration):
    dependencies = [("user", "0011_user_must_change_password")]

    operations = [
        migrations.RunPython(
            seed_academic_calendar_permission,
            drop_academic_calendar_permission,
        )
    ]
