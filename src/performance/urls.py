from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AssignmentSubmissionView,
    AssignmentViewSet,
    AttendanceSessionViewSet,
    InternalExamMarkView,
    InternalExamViewSet,
    RosterView,
)

router = DefaultRouter(trailing_slash=False)
router.register("attendance-sessions", AttendanceSessionViewSet, basename="attendance-session")
router.register("internal-exams", InternalExamViewSet, basename="internal-exam")
router.register("assignments", AssignmentViewSet, basename="assignment")

urlpatterns = [
    path("roster", RosterView.as_view(), name="roster"),
    path(
        "internal-exams/<int:exam_id>/marks",
        InternalExamMarkView.as_view(),
        name="internal-exam-marks",
    ),
    path(
        "assignments/<int:assignment_id>/submissions",
        AssignmentSubmissionView.as_view(),
        name="assignment-submissions",
    ),
    path("", include(router.urls)),
]
