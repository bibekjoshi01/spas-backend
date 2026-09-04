from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    SemesterEnrollmentBulkView,
    SemesterEnrollmentViewSet,
    StudentImportView,
    StudentPortalConfigurationView,
    StudentViewSet,
    SubjectEnrollmentBulkView,
    SubjectEnrollmentViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register("students", StudentViewSet, basename="student")
router.register("semester-enrollments", SemesterEnrollmentViewSet, basename="semester-enrollment")
router.register("subject-enrollments", SubjectEnrollmentViewSet, basename="subject-enrollment")

urlpatterns = [
    path(
        "settings/student-portal",
        StudentPortalConfigurationView.as_view(),
        name="student-portal-configuration",
    ),
    path("students/import", StudentImportView.as_view(), name="student-import"),
    path(
        "semester-enrollments/bulk",
        SemesterEnrollmentBulkView.as_view(),
        name="semester-enrollment-bulk",
    ),
    path(
        "subject-enrollments/bulk",
        SubjectEnrollmentBulkView.as_view(),
        name="subject-enrollment-bulk",
    ),
    path("", include(router.urls)),
]
