import django.db.models.deletion
from django.db import migrations, models


def create_student_users(apps, schema_editor):
    Student = apps.get_model("students", "Student")
    User = apps.get_model("user", "User")
    UserRole = apps.get_model("user", "UserRole")
    student_role = UserRole.objects.filter(codename="STUDENT").first()

    for student in Student.objects.filter(user_id=None).iterator():
        stem = f"student-{student.batch_id}-{student.roll_number}".lower().replace(" ", "-")
        stem = stem[:24]
        username = stem
        suffix = 1
        while User.objects.filter(username__iexact=username).exists():
            suffix += 1
            username = f"{stem[: 29 - len(str(suffix))]}-{suffix}"

        user = User.objects.create(
            username=username,
            email=student.email or f"{username}@student.local",
            password="!",
            first_name=student.first_name,
            middle_name=student.middle_name,
            last_name=student.last_name,
            full_name=" ".join(
                part
                for part in (student.first_name, student.middle_name, student.last_name)
                if part
            ),
            created_by_id=student.created_by_id,
        )
        if student_role:
            user.roles.add(student_role)
        student.user_id = user.id
        student.save(update_fields=["user"])


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0002_constraint_messages"),
        # The migration only needs the existing User/UserRole tables. Keeping
        # it independent of later role normalisation supports databases where
        # student accounts were migrated before that data migration existed.
        ("user", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="student_profile",
                to="user.user",
                verbose_name="user",
            ),
        ),
        migrations.RunPython(create_student_users, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="student",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="student_profile",
                to="user.user",
                verbose_name="user",
            ),
        ),
    ]
