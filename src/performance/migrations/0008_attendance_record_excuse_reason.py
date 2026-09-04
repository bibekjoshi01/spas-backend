from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("performance", "0007_alter_assignment_allocation_and_more")]

    operations = [
        migrations.AddField(
            model_name="attendancerecord",
            name="excuse_reason",
            field=models.CharField(
                blank=True,
                help_text="Optional context recorded when this attendance is excused.",
                max_length=500,
                verbose_name="excuse reason",
            ),
        ),
        migrations.AddField(
            model_name="historicalattendancerecord",
            name="excuse_reason",
            field=models.CharField(
                blank=True,
                help_text="Optional context recorded when this attendance is excused.",
                max_length=500,
                verbose_name="excuse reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancerecord",
            constraint=models.CheckConstraint(
                condition=models.Q(("status", "EXCUSED"), ("excuse_reason", ""), _connector="OR"),
                name="attendance_excuse_reason_only_when_excused",
                violation_error_message="An excuse reason can only be recorded for excused attendance.",
            ),
        ),
    ]
