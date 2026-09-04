# ruff: noqa: RUF012

import uuid

import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models
from django.utils.text import slugify


def provision_existing_student_credentials(apps, schema_editor):
    Student = apps.get_model("students", "Student")
    User = apps.get_model("user", "User")
    used = {value.lower() for value in User.objects.values_list("username", flat=True)}
    for student in Student.objects.select_related("user").order_by("id").iterator():
        first_name_slug = slugify(student.first_name) or "student"
        roll_number_slug = slugify(student.roll_number) or "roll"
        first_name_limit = max(1, 29 - len(roll_number_slug))
        base = f"{first_name_slug[:first_name_limit]}-{roll_number_slug}"[:30]
        username = base
        suffix = 1
        while username.lower() in used and username.lower() != student.user.username.lower():
            suffix += 1
            username = f"{base[: 29 - len(str(suffix))]}-{suffix}"
        used.discard(student.user.username.lower())
        used.add(username.lower())
        User.objects.filter(pk=student.user_id).update(
            username=username,
            password=make_password(student.roll_number),
            must_change_password=True,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0007_alter_semesterenrollment_options_and_more"),
        ("user", "0011_user_must_change_password"),
    ]

    operations = [
        migrations.CreateModel(
            name="HistoricalStudentPortalConfiguration",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True, blank=True, db_index=True, verbose_name="ID"
                    ),
                ),
                (
                    "uuid",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        help_text="Represents unique uuid.",
                        verbose_name="uuid",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name="created date",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(blank=True, editable=False, verbose_name="date updated"),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Designates whether this object should be treated as active. Unselect this instead of deleting instances.",
                        verbose_name="active",
                    ),
                ),
                (
                    "is_archived",
                    models.BooleanField(
                        default=False,
                        help_text="Designates whether this object should be treated as deleted. Unselect this instead of deleting instances.",
                        verbose_name="archived",
                    ),
                ),
                ("singleton_key", models.BooleanField(db_index=True, default=True, editable=False)),
                ("login_enabled", models.BooleanField(default=False)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                (
                    "history_type",
                    models.CharField(
                        choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        help_text="Last user to modify this row; empty until the first update.",
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "historical student portal configuration",
                "verbose_name_plural": "historical student portal configuration",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="StudentPortalConfiguration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        help_text="Represents unique uuid.",
                        unique=True,
                        verbose_name="uuid",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name="created date",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="date updated")),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Designates whether this object should be treated as active. Unselect this instead of deleting instances.",
                        verbose_name="active",
                    ),
                ),
                (
                    "is_archived",
                    models.BooleanField(
                        default=False,
                        help_text="Designates whether this object should be treated as deleted. Unselect this instead of deleting instances.",
                        verbose_name="archived",
                    ),
                ),
                ("singleton_key", models.BooleanField(default=True, editable=False, unique=True)),
                ("login_enabled", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Last user to modify this row; empty until the first update.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "student portal configuration",
                "verbose_name_plural": "student portal configuration",
            },
        ),
        migrations.RunPython(provision_existing_student_credentials, migrations.RunPython.noop),
    ]
