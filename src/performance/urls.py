from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .analytics import (
    AttendanceAttentionView,
    ClassStudentDetailView,
    ClassStudentSummaryView,
    ClassSummaryView,
    ManagementStudentReportView,
    OverviewView,
)
from .views import (
    AssignmentSubmissionView,
    AssignmentViewSet,
    AttendanceSessionViewSet,
    ClassPerformanceView,
    InternalExamMarkView,
    InternalExamViewSet,
    PerformanceWeightConfigurationView,
    RosterView,
)

router = DefaultRouter(trailing_slash=False)
router.register("attendance-sessions", AttendanceSessionViewSet, basename="attendance-session")
router.register("internal-exams", InternalExamViewSet, basename="internal-exam")
router.register("assignments", AssignmentViewSet, basename="assignment")

urlpatterns = [
    path(
        "settings/performance-weights",
        PerformanceWeightConfigurationView.as_view(),
        name="performance-weight-configuration",
    ),
    path("roster", RosterView.as_view(), name="roster"),
    path("class-performance", ClassPerformanceView.as_view(), name="class-performance"),
    # Aggregate reads for the teacher-facing screens
    path("analytics/overview", OverviewView.as_view(), name="analytics-overview"),
    path(
        "analytics/attendance-attention",
        AttendanceAttentionView.as_view(),
        name="analytics-attendance-attention",
    ),
    path(
        "analytics/students/<int:student_id>/report",
        ManagementStudentReportView.as_view(),
        name="analytics-management-student-report",
    ),
    path("analytics/classes", ClassSummaryView.as_view(), name="analytics-classes"),
    path(
        "analytics/classes/<int:allocation_id>/students",
        ClassStudentSummaryView.as_view(),
        name="analytics-class-students",
    ),
    path(
        "analytics/classes/<int:allocation_id>/students/<int:enrollment_id>",
        ClassStudentDetailView.as_view(),
        name="analytics-class-student-detail",
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
