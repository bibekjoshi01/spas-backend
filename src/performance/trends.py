"""
Attendance over time, rather than attendance to date.

Every other figure in the system is a lifetime aggregate, which cannot separate
a student who stopped attending a month ago from one who has been climbing back
ever since — both read as the same percentage. These read the same records by
week, so the question a teacher actually asks has an answer.

Two figures do the work, and they say different things:

*   The **running** percentage is the one eligibility is measured on: attended
    over held, counted from the start of the class up to that week. It is what
    the student's standing actually is, and it moves slowly.
*   The **recent** percentage is the trailing few weeks on their own. It moves
    immediately, so it is what says whether the running figure is on its way up
    or down before the running figure has got there.

A student sitting at 55% who has attended 90% of the last month is recovering;
the same 55% with 20% recently is still falling. That difference is the whole
point, and it is invisible in a lifetime aggregate.
"""

from collections import defaultdict
from datetime import date

from django.db.models import Count, Q
from django.db.models.functions import TruncWeek

from .constants import AttendanceStatus
from .models import AttendanceRecord, AttendanceSession

ATTENDED_STATUSES = (AttendanceStatus.PRESENT.value, AttendanceStatus.LATE.value)

#: Weeks of history a sparkline shows. Long enough to see a shape, short enough
#: that a semester's worth of dots stays legible at the size these are drawn.
WINDOW_WEEKS = 8

#: The trailing window that counts as "lately". A month is long enough to
#: survive one missed class and short enough to notice a change of habit.
RECENT_WEEKS = 4

#: How far recent has to sit from overall before it is called a direction.
#: Below this, the two figures are the same story told twice.
DIRECTION_MARGIN = 5.0

RISING, FALLING, STEADY = "RISING", "FALLING", "STEADY"


def _percentage(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def _direction(overall: float, recent: float | None) -> str | None:
    if recent is None:
        return None
    if recent - overall > DIRECTION_MARGIN:
        return RISING
    if overall - recent > DIRECTION_MARGIN:
        return FALLING
    return STEADY


def _empty_trend() -> dict:
    """What a class with no sessions yet reports: nothing, honestly."""
    return {
        "points": [],
        "overall_percentage": None,
        "recent_percentage": None,
        "direction": None,
        "recent_weeks": RECENT_WEEKS,
    }


def _build(weeks: list[date], held_by_week: dict, attended_by_week: dict) -> dict:
    """
    One trend from one series of weekly counts.

    Weeks with no class held are left out rather than plotted as zero. A public
    holiday is not a week of nobody turning up, and drawing it as one would put
    a cliff in the line that never happened.
    """
    if not weeks:
        return _empty_trend()

    points = []
    running_held = running_attended = 0
    for week in weeks:
        held = held_by_week.get(week, 0)
        if not held:
            continue
        running_held += held
        running_attended += attended_by_week.get(week, 0)
        points.append(
            {
                "week": week,
                "held": held,
                "attended": attended_by_week.get(week, 0),
                # The standing as it stood that week, which is what the
                # eligibility bar is drawn against.
                "percentage": _percentage(running_attended, running_held),
            }
        )

    if not points:
        return _empty_trend()

    recent = points[-RECENT_WEEKS:]
    recent_held = sum(point["held"] for point in recent)
    recent_attended = sum(point["attended"] for point in recent)
    recent_percentage = _percentage(recent_attended, recent_held) if recent_held else None
    overall = points[-1]["percentage"]

    return {
        # Only the tail is drawn; the running figure behind it still counts
        # every class ever held, so the line starts where the standing really is.
        "points": points[-WINDOW_WEEKS:],
        "overall_percentage": overall,
        "recent_percentage": recent_percentage,
        "direction": _direction(overall, recent_percentage),
        "recent_weeks": RECENT_WEEKS,
    }


def _sessions_by_week(allocation_ids: list[int]) -> dict[int, dict[date, int]]:
    """
    Classes held per allocation per week.

    This is the denominator every attendance figure in the system uses: a
    student not marked in a session still had a class they could have attended,
    so the divisor is sessions held, never records written.
    """
    rows = (
        AttendanceSession.objects.filter(allocation_id__in=allocation_ids, is_archived=False)
        .annotate(week=TruncWeek("date"))
        .values("allocation_id", "week")
        .annotate(held=Count("id"))
    )
    by_allocation: dict[int, dict[date, int]] = defaultdict(dict)
    for row in rows:
        by_allocation[row["allocation_id"]][row["week"]] = row["held"]
    return by_allocation


def class_attendance_trends(allocation_ids: list[int]) -> dict[int, dict]:
    """How each class as a whole has been attending, by allocation id."""
    if not allocation_ids:
        return {}

    held_by_allocation = _sessions_by_week(allocation_ids)

    rows = (
        AttendanceRecord.objects.filter(
            session__allocation_id__in=allocation_ids,
            is_archived=False,
            session__is_archived=False,
        )
        .annotate(week=TruncWeek("session__date"))
        .values("session__allocation_id", "week")
        .annotate(
            marked=Count("id"),
            attended=Count("id", filter=Q(status__in=ATTENDED_STATUSES)),
        )
    )
    marked_by_allocation: dict[int, dict[date, int]] = defaultdict(dict)
    attended_by_allocation: dict[int, dict[date, int]] = defaultdict(dict)
    for row in rows:
        allocation_id, week = row["session__allocation_id"], row["week"]
        marked_by_allocation[allocation_id][week] = row["marked"]
        attended_by_allocation[allocation_id][week] = row["attended"]

    trends = {}
    for allocation_id in allocation_ids:
        held = held_by_allocation.get(allocation_id, {})
        # A class-wide figure divides by the marks that could have been made,
        # not by the sessions, so a bigger roster does not read as worse
        # attendance.
        marked = marked_by_allocation.get(allocation_id, {})
        weeks = sorted(held)
        trends[allocation_id] = _build(weeks, marked, attended_by_allocation.get(allocation_id, {}))
    return trends


def student_attendance_trends(allocation_id: int, enrollment_ids: list[int]) -> dict[int, dict]:
    """
    How each of these students has been attending one class, by enrollment id.

    Two queries however many students are asked about, so this stays usable
    behind a roster or an at-risk list rather than only a single detail screen.
    """
    if not enrollment_ids:
        return {}

    held_by_week = _sessions_by_week([allocation_id]).get(allocation_id, {})
    weeks = sorted(held_by_week)

    rows = (
        AttendanceRecord.objects.filter(
            enrollment_id__in=enrollment_ids,
            is_archived=False,
            session__is_archived=False,
            session__allocation_id=allocation_id,
        )
        .annotate(week=TruncWeek("session__date"))
        .values("enrollment_id", "week")
        .annotate(attended=Count("id", filter=Q(status__in=ATTENDED_STATUSES)))
    )
    attended_by_enrollment: dict[int, dict[date, int]] = defaultdict(dict)
    for row in rows:
        attended_by_enrollment[row["enrollment_id"]][row["week"]] = row["attended"]

    return {
        enrollment_id: _build(weeks, held_by_week, attended_by_enrollment.get(enrollment_id, {}))
        for enrollment_id in enrollment_ids
    }
