# ruff: noqa: RUF012

from django.db import migrations


def grant_management_class_performance_view(apps, schema_editor):
    Permission = apps.get_model("user", "Permission")
    UserRole = apps.get_model("user", "UserRole")
    permission = Permission.objects.filter(codename="view_class_performance").first()
    if permission is None:
        return
    for role in UserRole.objects.filter(
        codename__in=("DEPARTMENT-HEAD", "PROGRAM-COORDINATOR")
    ):
        role.permissions.add(permission)


def revoke_management_class_performance_view(apps, schema_editor):
    Permission = apps.get_model("user", "Permission")
    UserRole = apps.get_model("user", "UserRole")
    permission = Permission.objects.filter(codename="view_class_performance").first()
    if permission is None:
        return
    for role in UserRole.objects.filter(
        codename__in=("DEPARTMENT-HEAD", "PROGRAM-COORDINATOR")
    ):
        role.permissions.remove(permission)


class Migration(migrations.Migration):
    dependencies = [("user", "0009_historicaluser_alternate_phone_no_and_more")]

    operations = [
        migrations.RunPython(
            grant_management_class_performance_view,
            revoke_management_class_performance_view,
        )
    ]
