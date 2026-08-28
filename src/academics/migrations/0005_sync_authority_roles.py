from django.db import migrations


def sync_authority_roles(apps, schema_editor):
    Department = apps.get_model("academics", "Department")
    Program = apps.get_model("academics", "Program")
    Permission = apps.get_model("user", "Permission")
    UserRole = apps.get_model("user", "UserRole")

    head_role = UserRole.objects.filter(codename="DEPARTMENT-HEAD").first()
    coordinator_role = UserRole.objects.filter(codename="PROGRAM-COORDINATOR").first()

    if head_role:
        for user_id in Department.objects.exclude(head_id=None).values_list(
            "head_id", flat=True
        ).distinct():
            head_role.users.add(user_id)

    if coordinator_role:
        for user_id in Program.objects.exclude(coordinator_id=None).values_list(
            "coordinator_id", flat=True
        ).distinct():
            coordinator_role.users.add(user_id)

    batch_write_permissions = Permission.objects.filter(
        codename__in=("add_batch", "edit_batch", "delete_batch")
    )
    for role in UserRole.objects.filter(
        codename__in=("DEPARTMENT-HEAD", "PROGRAM-COORDINATOR")
    ):
        role.permissions.remove(*batch_write_permissions)


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0004_replace_teacher_with_user"),
        ("user", "0007_normalize_account_roles"),
    ]

    operations = [migrations.RunPython(sync_authority_roles, migrations.RunPython.noop)]
