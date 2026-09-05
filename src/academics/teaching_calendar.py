"""Shared college closures and class-day rules. Never mutate recorded history."""

from .models import AcademicCalendarConfiguration, AcademicCalendarEntry


class TeachingCalendar:
    def __init__(self, start, end, allocation_ids=()):
        from src.performance.models import ClassScheduleChange

        self.weekends = set(AcademicCalendarConfiguration.current().weekend_days or [])
        self.holidays = {}
        for date, title in AcademicCalendarEntry.objects.filter(
            is_archived=False, is_active=True, kind="HOLIDAY", date__range=(start, end)
        ).values_list("date", "title"):
            self.holidays.setdefault(date, []).append(title)
        self.changes = {
            (change.allocation_id, change.date): change
            for change in ClassScheduleChange.objects.filter(
                allocation_id__in=allocation_ids,
                date__range=(start, end),
                is_archived=False,
                is_active=True,
            )
        }

    def day(self, allocation, date):
        semester = allocation.batch_semester
        outside = bool(
            (semester.start_date and date < semester.start_date)
            or (semester.end_date and date > semester.end_date)
        )
        meetings = [m for m in allocation.meetings.all() if not m.is_archived and m.is_active]
        timetabled = not meetings or any(m.weekday == date.isoweekday() for m in meetings)
        holiday_titles = self.holidays.get(date, [])
        weekend = date.isoweekday() in self.weekends
        change = self.changes.get((allocation.pk, date))
        cancelled = bool(change and change.kind == "CANCELLED")
        makeup = bool(change and change.kind == "MAKEUP")
        expected = (
            not outside
            and not cancelled
            and (makeup or (timetabled and not weekend and not holiday_titles))
        )
        label = (
            "Outside semester"
            if outside
            else "Class cancelled"
            if cancelled
            else "Makeup class"
            if makeup
            else ", ".join(holiday_titles)
            if holiday_titles
            else "Weekend"
            if weekend
            else "Not timetabled"
            if not timetabled
            else "Teaching day"
        )
        return {
            "date": date,
            "label": label,
            "is_weekend": weekend,
            "holiday_titles": holiday_titles,
            "outside_semester": outside,
            "is_expected": expected,
            "is_cancelled": cancelled,
            "is_makeup": makeup,
            "is_scheduled": bool(meetings),
            "requires_reason": not outside and not cancelled and not expected,
            "schedule_change": None
            if not change
            else {
                "id": change.pk,
                "kind": change.kind,
                "reason": change.reason,
            },
        }
