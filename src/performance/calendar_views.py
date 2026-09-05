import datetime
from typing import ClassVar

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from src.academics.models import SubjectAllocation
from src.academics.serializers import AuditedModelSerializer
from src.academics.teaching_calendar import TeachingCalendar
from src.academics.views import BaseAcademicViewSet
from src.libs.permissions import (
    AllocationOwnerScopedQuerysetMixin,
    ModelPermission,
    scope_to_allocation_owner,
    validate_permissions,
)

from .models import AttendanceSession, ClassScheduleChange
from .serializers import OwnAllocationMixin, validate_allocation_is_writable


class SchedulePermission(ModelPermission):
    permission_map: ClassVar[dict[str, object]] = {
        "SAFE_METHODS": "view_attendance",
        "POST": "add_attendance",
        "PATCH": "edit_attendance",
        "DELETE": "delete_attendance",
    }


class ClassDatePermission(ModelPermission):
    def has_permission(self, request, view):
        return any(
            validate_permissions(request, {"SAFE_METHODS": permission})
            for permission in ("view_attendance", "view_internal_exam", "view_assignment")
        )


class ScheduleSerializer(OwnAllocationMixin, AuditedModelSerializer):
    class Meta:
        model = ClassScheduleChange
        fields = ("id", "allocation", "date", "kind", "reason")
        read_only_fields = ("id",)

    def validate(self, attrs):
        if self.instance:
            for field in ("allocation", "date"):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError(
                        {field: "Create a separate schedule change for another date or class."}
                    )
        candidate = ClassScheduleChange(
            allocation=attrs.get("allocation", getattr(self.instance, "allocation", None)),
            date=attrs.get("date", getattr(self.instance, "date", None)),
            kind=attrs.get("kind", getattr(self.instance, "kind", "")),
            reason=attrs.get("reason", getattr(self.instance, "reason", "")),
        )
        try:
            candidate.clean()
        except ModelValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs


class ClassScheduleViewSet(AllocationOwnerScopedQuerysetMixin, BaseAcademicViewSet):
    permission_classes = (SchedulePermission,)
    queryset = ClassScheduleChange.objects.filter(is_archived=False).select_related(
        "allocation__batch_semester"
    )
    owner_scope_path = "allocation__teacher"
    list_serializer_class = create_serializer_class = patch_serializer_class = ScheduleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ("allocation", "date")
    archive_message = "Schedule change removed."

    @transaction.atomic
    def perform_create(self, serializer):
        allocation = serializer.validated_data["allocation"]
        SubjectAllocation.objects.select_for_update().get(pk=allocation.pk)
        serializer.save()

    @transaction.atomic
    def perform_update(self, serializer):
        SubjectAllocation.objects.select_for_update().get(pk=serializer.instance.allocation_id)
        validate_allocation_is_writable(serializer.instance.allocation)
        serializer.save()

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        SubjectAllocation.objects.select_for_update().get(pk=instance.allocation_id)
        validate_allocation_is_writable(instance.allocation)
        if AttendanceSession.objects.filter(
            allocation_id=instance.allocation_id, date=instance.date, is_archived=False
        ).exists():
            raise ValidationError({"date": "Keep the schedule change for this recorded class."})
        return super().destroy(request, *args, **kwargs)


class DateQuery(serializers.Serializer):
    allocation = serializers.IntegerField(min_value=1)
    date = serializers.DateField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)


class ClassCalendarView(generics.GenericAPIView):
    permission_classes = (ClassDatePermission,)
    serializer_class = DateQuery

    def get(self, request):
        query = DateQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        allocation = generics.get_object_or_404(
            scope_to_allocation_owner(
                SubjectAllocation.objects.filter(is_archived=False)
                .select_related("batch_semester")
                .prefetch_related("meetings"),
                request.user,
                path="teacher",
            ),
            pk=params["allocation"],
        )
        if "date" in params:
            date = params["date"]
            return Response(TeachingCalendar(date, date, [allocation.pk]).day(allocation, date))
        start, end = params.get("date_from"), params.get("date_to")
        if not start or not end:
            raise ValidationError({"date_from": "Choose a date range."})
        if end < start or (end - start).days > 366:
            raise ValidationError({"date_to": "Choose an ordered range of at most 367 days."})
        calendar = TeachingCalendar(start, end, [allocation.pk])
        days = [
            calendar.day(allocation, start + datetime.timedelta(days=i))
            for i in range((end - start).days + 1)
        ]
        sessions = AttendanceSession.objects.filter(
            allocation=allocation, date__range=(start, end), is_archived=False
        )
        held = set(sessions.values_list("date", flat=True))
        makeup = set(sessions.exclude(makeup_reason="").values_list("date", flat=True))
        expected = {day["date"] for day in days if day["is_expected"]}
        return Response(
            {
                "days": days,
                "summary": {
                    "planned_days": len(expected),
                    "held_days": len(held),
                    "cancelled_days": sum(day["is_cancelled"] for day in days),
                    "makeup_days": len(makeup | {day["date"] for day in days if day["is_makeup"]}),
                    "unrecorded_days": len(
                        {date for date in expected - held if date <= timezone.localdate()}
                    ),
                    "is_scheduled": any(day["is_scheduled"] for day in days),
                },
            }
        )
