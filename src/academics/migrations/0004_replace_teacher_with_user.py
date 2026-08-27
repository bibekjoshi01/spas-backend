import django.db.models.deletion
from django.db import migrations, models


def copy_teacher_users(apps, schema_editor):
    Department = apps.get_model("academics", "Department")
    Program = apps.get_model("academics", "Program")
    SubjectAllocation = apps.get_model("academics", "SubjectAllocation")
    Teacher = apps.get_model("academics", "Teacher")

    user_by_teacher = dict(Teacher.objects.values_list("id", "user_id"))

    for department in Department.objects.exclude(head_id=None):
        department.head_user_id = user_by_teacher[department.head_id]
        department.save(update_fields=["head_user"])

    for program in Program.objects.exclude(coordinator_id=None):
        program.coordinator_user_id = user_by_teacher[program.coordinator_id]
        program.save(update_fields=["coordinator_user"])

    for allocation in SubjectAllocation.objects.all():
        allocation.teacher_user_id = user_by_teacher[allocation.teacher_id]
        allocation.save(update_fields=["teacher_user"])


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0003_department_head"),
        # Only the User table is required for the new foreign keys. Depending
        # on a later data-normalisation migration makes upgrades inconsistent
        # when this academics migration was already applied first.
        ("user", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="department",
            name="head_user",
            field=models.ForeignKey(
                blank=True,
                help_text="Whose authority covers everything under this department.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="headed_departments",
                to="user.user",
                verbose_name="head of department",
            ),
        ),
        migrations.AddField(
            model_name="program",
            name="coordinator_user",
            field=models.ForeignKey(
                blank=True,
                help_text="Teacher who allocates subjects and manages enrollment for this program.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="coordinated_programs",
                to="user.user",
                verbose_name="coordinator",
            ),
        ),
        migrations.AddField(
            model_name="subjectallocation",
            name="teacher_user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="allocations",
                to="user.user",
                verbose_name="teacher",
            ),
        ),
        migrations.RunPython(copy_teacher_users, migrations.RunPython.noop),
        migrations.RemoveField(model_name="department", name="head"),
        migrations.RemoveField(model_name="program", name="coordinator"),
        migrations.RemoveField(model_name="subjectallocation", name="teacher"),
        migrations.RenameField(model_name="department", old_name="head_user", new_name="head"),
        migrations.RenameField(
            model_name="program", old_name="coordinator_user", new_name="coordinator"
        ),
        migrations.RenameField(
            model_name="subjectallocation", old_name="teacher_user", new_name="teacher"
        ),
        migrations.AlterField(
            model_name="subjectallocation",
            name="teacher",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="allocations",
                to="user.user",
                verbose_name="teacher",
            ),
        ),
        migrations.DeleteModel(name="Teacher"),
    ]
