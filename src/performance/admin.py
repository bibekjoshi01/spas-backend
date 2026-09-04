from django.contrib import admin

from src.base.admin import BaseAdmin

from .models import (
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    ClassPerformanceRating,
    InternalExam,
    InternalExamMark,
    PerformanceWeightConfiguration,
)


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0
    fields = ("enrollment", "status", "excuse_reason")


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(BaseAdmin):
    list_display = ("allocation", "date", "period", "edit_action")
    list_filter = ("allocation__subject__program", "date")
    inlines = (AttendanceRecordInline,)


class InternalExamMarkInline(admin.TabularInline):
    model = InternalExamMark
    extra = 0
    fields = ("enrollment", "marks_obtained", "is_absent")


@admin.register(InternalExam)
class InternalExamAdmin(BaseAdmin):
    list_display = ("title", "allocation", "exam_type", "full_marks", "exam_date", "edit_action")
    search_fields = ("title", "allocation__subject__code")
    list_filter = ("exam_type", "allocation__subject__program")
    inlines = (InternalExamMarkInline,)


class AssignmentSubmissionInline(admin.TabularInline):
    model = AssignmentSubmission
    extra = 0
    fields = ("enrollment", "status", "remarks")


@admin.register(Assignment)
class AssignmentAdmin(BaseAdmin):
    list_display = ("title", "allocation", "assigned_date", "due_date", "edit_action")
    search_fields = ("title", "allocation__subject__code")
    list_filter = ("allocation__subject__program", "assigned_date")
    inlines = (AssignmentSubmissionInline,)


@admin.register(ClassPerformanceRating)
class ClassPerformanceRatingAdmin(BaseAdmin):
    list_display = ("enrollment", "score", "updated_at", "edit_action")
    search_fields = (
        "enrollment__student__roll_number",
        "enrollment__student__first_name",
        "enrollment__student__last_name",
    )
    list_filter = ("score", "enrollment__allocation__subject__program")


@admin.register(PerformanceWeightConfiguration)
class PerformanceWeightConfigurationAdmin(BaseAdmin):
    list_display = (
        "attendance_weight",
        "class_performance_weight",
        "assignment_weight",
        "assessment_weight",
        "updated_at",
        "edit_action",
    )
