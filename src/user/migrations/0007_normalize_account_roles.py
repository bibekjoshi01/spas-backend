from django.db import migrations


def normalize_roles(apps, schema_editor):
    Permission = apps.get_model("user", "Permission")
    PermissionCategory = apps.get_model("user", "PermissionCategory")
    User = apps.get_model("user", "User")
    UserRole = apps.get_model("user", "UserRole")

    Permission.objects.filter(
        codename__in=("view_teacher", "add_teacher", "edit_teacher", "delete_teacher")
    ).delete()
    PermissionCategory.objects.filter(codename="TEACHER_MANAGEMENT").delete()

    system_role = UserRole.objects.filter(codename="SYSTEM-USER").first()
    if system_role:
        student_ids = User.objects.filter(roles__codename="STUDENT").values_list("id", flat=True)
        for user in User.objects.exclude(id__in=student_ids):
            user.roles.add(system_role)


class Migration(migrations.Migration):
    dependencies = [("user", "0006_secure_password_reset_requests")]

    operations = [migrations.RunPython(normalize_roles, migrations.RunPython.noop)]
