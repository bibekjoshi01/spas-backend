from django.db import models
from django.utils.translation import gettext_lazy as _

from src.base.constants import BaseEnum

# Longest program supported today. Extending this enum is additive — no data
# migration is needed to support programs longer than eight semesters.
MAX_SEMESTERS = 8


class BatchStatus(BaseEnum):
    """
    Lifecycle of one intake cohort.

    The layer between "this semester finished" and "this student graduated" had
    no state of its own, so nothing could say a cohort was done — and every
    picker in the system had to keep offering it. The words mirror
    SemesterStatus deliberately: a reader should not have to learn two
    vocabularies for the same idea at two levels.
    """

    UPCOMING = "UPCOMING"
    RUNNING = "RUNNING"
    GRADUATED = "GRADUATED"


class Weekday(models.IntegerChoices):
    """
    Days of the teaching week, numbered as `date.isoweekday()`.

    Matching the standard library means today's day is `localdate().isoweekday()`
    with no lookup table in between. Presentation orders these Sunday-first,
    which is the working week for the colleges this serves — Saturday is the
    weekly holiday, so it is last rather than absent.
    """

    MONDAY = 1, _("Monday")
    TUESDAY = 2, _("Tuesday")
    WEDNESDAY = 3, _("Wednesday")
    THURSDAY = 4, _("Thursday")
    FRIDAY = 5, _("Friday")
    SATURDAY = 6, _("Saturday")
    SUNDAY = 7, _("Sunday")


class CalendarSystem(BaseEnum):
    """
    Which calendar the academic year is being read in.

    Storage is always the Gregorian date; this only says which grid the reader
    is looking at, because a Bikram Sambat month neither starts nor ends where
    a Gregorian one does.
    """

    BS = "BS"
    AD = "AD"


class CalendarEntryKind(BaseEnum):
    """
    What a marked date means.

    A holiday closes the campus and will later exclude the date from teaching;
    an event is something happening on a working day. Keeping them apart now
    means the scheduling work later does not have to guess from the wording.
    """

    HOLIDAY = "HOLIDAY"
    EVENT = "EVENT"


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
