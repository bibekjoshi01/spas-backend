from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .analytics import ClassStudentSummaryView, ClassSummaryView, OverviewView
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
    # Aggregate reads for the teacher-facing screens
    path("analytics/overview", OverviewView.as_view(), name="analytics-overview"),
    path("analytics/classes", ClassSummaryView.as_view(), name="analytics-classes"),
    path(
        "analytics/classes/<int:allocation_id>/students",
        ClassStudentSummaryView.as_view(),
        name="analytics-class-students",
    ),
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
