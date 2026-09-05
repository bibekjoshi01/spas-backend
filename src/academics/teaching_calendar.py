"""College closures shared by attendance and the academic calendar."""

from .models import AcademicCalendarConfiguration, AcademicCalendarEntry


class TeachingCalendar:
    def __init__(self, start, end, allocation_ids=()):
        self.weekends = set(AcademicCalendarConfiguration.current().weekend_days or [])
        self.holidays = {}
        for date, title in (
            AcademicCalendarEntry.objects.filter(
                is_archived=False, is_active=True, kind="HOLIDAY", date__range=(start, end)
            )
            .order_by("title")
            .values_list("date", "title")
        ):
            self.holidays.setdefault(date, []).append(title)

    def day(self, allocation, date):
        holidays = self.holidays.get(date, [])
        weekend = date.isoweekday() in self.weekends
        return {
            "date": date,
            "label": ", ".join(holidays) if holidays else "Weekend" if weekend else "Teaching day",
            "is_weekend": weekend,
            "holiday_titles": holidays,
            "is_expected": not weekend and not holidays,
        }

    def attendance_error(self, date):
        if self.holidays.get(date):
            return "Attendance is unavailable on holidays: " + ", ".join(self.holidays[date]) + "."
        if date.isoweekday() in self.weekends:
            return "Attendance is unavailable on weekends."
        return None
