from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AcademicCalendarConfigurationView,
    AcademicCalendarEntryViewSet,
    AcademicCalendarYearView,
    BatchSemesterViewSet,
    BatchViewSet,
    DepartmentViewSet,
    ProgramViewSet,
    StudentPortalCalendarYearView,
    SubjectAllocationViewSet,
    SubjectImportView,
    SubjectViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register("departments", DepartmentViewSet, basename="department")
router.register("programs", ProgramViewSet, basename="program")
router.register("batches", BatchViewSet, basename="batch")
router.register("batch-semesters", BatchSemesterViewSet, basename="batch-semester")
router.register("subjects", SubjectViewSet, basename="subject")
router.register("allocations", SubjectAllocationViewSet, basename="subject-allocation")
router.register(
    "calendar-entries", AcademicCalendarEntryViewSet, basename="academic-calendar-entry"
)

urlpatterns = [
    path("subjects/import", SubjectImportView.as_view(), name="subject-import"),
    path(
        "calendar/settings",
        AcademicCalendarConfigurationView.as_view(),
        name="academic-calendar-settings",
    ),
    path("calendar/year", AcademicCalendarYearView.as_view(), name="academic-calendar-year"),
    path(
        "student-portal/calendar/year",
        StudentPortalCalendarYearView.as_view(),
        name="student-portal-calendar-year",
    ),
    path("", include(router.urls)),
]
