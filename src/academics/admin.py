from django.contrib import admin

from src.base.admin import BaseAdmin

from .models import (
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
    Teacher,
)


@admin.register(Department)
class DepartmentAdmin(BaseAdmin):
    list_display = ("name", "code", "is_active", "edit_action")
    search_fields = ("name", "code")
    list_filter = ("is_active",)


@admin.register(Teacher)
class TeacherAdmin(BaseAdmin):
    list_display = (
        "user",
        "department",
        "designation",
        "employee_code",
        "is_active",
        "edit_action",
    )
    search_fields = ("user__username", "user__email", "employee_code")
    list_filter = ("department", "designation", "is_active")


@admin.register(Program)
class ProgramAdmin(BaseAdmin):
    list_display = (
        "name",
        "code",
        "department",
        "total_semesters",
        "coordinator",
        "is_active",
        "edit_action",
    )
    search_fields = ("name", "code")
    list_filter = ("department", "is_active")


@admin.register(Batch)
class BatchAdmin(BaseAdmin):
    list_display = ("year", "program", "is_active", "edit_action")
    search_fields = ("program__code", "program__name")
    list_filter = ("program", "is_active")


@admin.register(BatchSemester)
class BatchSemesterAdmin(BaseAdmin):
    list_display = ("batch", "semester", "status", "start_date", "end_date", "edit_action")
    list_filter = ("status", "semester", "batch__program")


@admin.register(Subject)
class SubjectAdmin(BaseAdmin):
    list_display = (
        "code",
        "name",
        "program",
        "semester",
        "credit_hours",
        "is_elective",
        "edit_action",
    )
    search_fields = ("code", "name")
    list_filter = ("program", "semester", "is_elective", "is_active")


@admin.register(SubjectAllocation)
class SubjectAllocationAdmin(BaseAdmin):
    list_display = ("subject", "batch_semester", "teacher", "is_active", "edit_action")
    search_fields = ("subject__code", "subject__name")
    list_filter = ("teacher", "batch_semester__batch__program", "is_active")
