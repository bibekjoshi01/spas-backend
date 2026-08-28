from django.db import models
from django.utils.translation import gettext_lazy as _

from src.base.constants import BaseEnum

# Longest program supported today. Extending this enum is additive — no data
# migration is needed to support programs longer than eight semesters.
MAX_SEMESTERS = 8


class SemesterChoices(models.IntegerChoices):
    SEM_1 = 1, _("1st Semester")
    SEM_2 = 2, _("2nd Semester")
    SEM_3 = 3, _("3rd Semester")
    SEM_4 = 4, _("4th Semester")
    SEM_5 = 5, _("5th Semester")
    SEM_6 = 6, _("6th Semester")
    SEM_7 = 7, _("7th Semester")
    SEM_8 = 8, _("8th Semester")


class SemesterStatus(BaseEnum):
    """Lifecycle of one semester of one batch."""

    UPCOMING = "UPCOMING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
