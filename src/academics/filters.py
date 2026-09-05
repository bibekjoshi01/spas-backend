from django import forms
from django_filters import rest_framework as filters

from .models import AcademicCalendarEntry


class CalendarEntryFilterForm(forms.Form):
    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "The end date must be on or after the start date.")
        return cleaned


class AcademicCalendarEntryFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="date", lookup_expr="lte")

    class Meta:
        model = AcademicCalendarEntry
        fields = ("kind", "is_active")
        form = CalendarEntryFilterForm
