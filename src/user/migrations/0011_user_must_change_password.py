# ruff: noqa: RUF012

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("user", "0010_grant_management_class_performance_view")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(
                default=False,
                help_text="Require a new password before the account can use protected features.",
                verbose_name="must change password",
            ),
        ),
        migrations.AddField(
            model_name="historicaluser",
            name="must_change_password",
            field=models.BooleanField(
                default=False,
                help_text="Require a new password before the account can use protected features.",
                verbose_name="must change password",
            ),
        ),
    ]
