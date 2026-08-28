# ruff: noqa: RUF012
import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def archive_legacy_password_reset_requests(apps, schema_editor):
    request_model = apps.get_model("user", "UserForgetPasswordRequest")
    request_model.objects.all().update(is_archived=True)


class Migration(migrations.Migration):
    dependencies = [("user", "0005_alter_userrole_options_and_more")]

    operations = [
        migrations.AddField(
            model_name="userforgetpasswordrequest",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="userforgetpasswordrequest",
            name="code_hash",
            field=models.CharField(default="", max_length=128),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="userforgetpasswordrequest",
            name="expires_at",
            field=models.DateTimeField(default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="userforgetpasswordrequest",
            name="verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userforgetpasswordrequest",
            name="consumed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userforgetpasswordrequest",
            name="failed_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="userforgetpasswordrequest",
            name="requested_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.RunPython(
            archive_legacy_password_reset_requests,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(model_name="userforgetpasswordrequest", name="otp"),
        migrations.RemoveField(model_name="userforgetpasswordrequest", name="token"),
        migrations.AlterField(
            model_name="userforgetpasswordrequest",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name="userforgetpasswordrequest",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name="userforgetpasswordrequest",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="password_reset_requests",
                to="user.user",
            ),
        ),
        migrations.AddIndex(
            model_name="userforgetpasswordrequest",
            index=models.Index(
                fields=["user", "created_at"], name="user_pwdreset_user_created_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="userforgetpasswordrequest",
            index=models.Index(fields=["expires_at"], name="user_pwdreset_expires_idx"),
        ),
    ]
