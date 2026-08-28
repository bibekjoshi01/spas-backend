from django.contrib import admin

from src.base.admin import BaseAdmin

from .models import SemesterEnrollment, Student, SubjectEnrollment


@admin.register(Student)
class StudentAdmin(BaseAdmin):
    list_display = ("roll_number", "full_name", "batch", "status", "is_active", "edit_action")
    search_fields = ("roll_number", "registration_number", "first_name", "last_name", "email")
    list_filter = ("batch", "status", "gender", "is_active")


@admin.register(SemesterEnrollment)
class SemesterEnrollmentAdmin(BaseAdmin):
    list_display = ("student", "batch_semester", "status", "edit_action")
    search_fields = ("student__roll_number", "student__first_name", "student__last_name")
    list_filter = ("status", "batch_semester__batch__program", "batch_semester__semester")


@admin.register(SubjectEnrollment)
class SubjectEnrollmentAdmin(BaseAdmin):
    list_display = ("student", "allocation", "is_retake", "is_active", "edit_action")
    search_fields = ("student__roll_number", "allocation__subject__code")
    list_filter = ("is_retake", "allocation__subject__program", "is_active")
