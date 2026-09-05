import datetime
from typing import ClassVar

from rest_framework import generics, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from src.academics.calendar import to_bs_string
from src.academics.models import SubjectAllocation
from src.academics.teaching_calendar import TeachingCalendar
from src.academics.views import build_calendar_year, resolve_calendar_request
from src.libs.permissions import ModelPermission, scope_to_allocation_owner


class ClassDatePermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = {"SAFE_METHODS": "view_attendance"}


class DateQuery(serializers.Serializer):
    allocation = serializers.IntegerField(min_value=1)
    date = serializers.DateField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    anchor = serializers.DateField(required=False)
    system = serializers.ChoiceField(choices=("BS", "AD"), required=False)
    year = serializers.IntegerField(required=False)


class ClassCalendarView(generics.GenericAPIView):
    permission_classes = (ClassDatePermission,)
    serializer_class = DateQuery

    def get(self, request):
        query = DateQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        allocation = generics.get_object_or_404(
            scope_to_allocation_owner(
                SubjectAllocation.objects.filter(is_archived=False), request.user, path="teacher"
            ),
            pk=params["allocation"],
        )
        if "date" in params:
            date = params["date"]
            return Response(TeachingCalendar(date, date).day(allocation, date))
        if "system" in params:
            if "anchor" in params and "year" not in params:
                try:
                    params["year"] = (
                        int(to_bs_string(params["anchor"]).split("-")[0])
                        if params["system"] == "BS"
                        else params["anchor"].year
                    )
                except (ValueError, OverflowError) as error:
                    raise ValidationError(
                        {"anchor": "Date is outside the supported calendar."}
                    ) from error
            return Response(build_calendar_year(*resolve_calendar_request(params)))
        start, end = params.get("date_from"), params.get("date_to")
        if not start or not end:
            raise ValidationError({"date_from": "Choose a date range."})
        if end < start or (end - start).days > 366:
            raise ValidationError({"date_to": "Choose an ordered range of at most 367 days."})
        calendar = TeachingCalendar(start, end)
        return Response(
            {
                "days": [
                    calendar.day(allocation, start + datetime.timedelta(days=i))
                    for i in range((end - start).days + 1)
                ]
            }
        )
