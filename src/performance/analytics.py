"""
Aggregate reads for the teacher-facing screens.

These are the only endpoints that compute rather than store: attendance
percentages, internal mark totals and assignment completion. Keeping them here
means the write endpoints stay narrow and every screen has one call to make.
"""

from datetime import timedelta

from django.db.models import Count, DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.response import Response

# Project Imports
from src.academics.models import SubjectAllocation
from src.libs.permissions import scope_to_teacher
from src.students.models import SubjectEnrollment

from .constants import AssignmentStatus, AttendanceStatus
from .models import AttendanceSession, InternalExamMark
from .permissions import AttendancePermission

# A student is counted as having attended when they were there at all. Excused
# absences still count against the requirement, which is the strict reading
# colleges apply to the 75% rule.
ATTENDED_STATUSES = (AttendanceStatus.PRESENT.value, AttendanceStatus.LATE.value)

ELIGIBILITY_THRESHOLD = 75.0


def percentage(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def allocation_queryset(user):
    """Every class the user may see, with its headline counts."""
    return scope_to_teacher(
        SubjectAllocation.objects.filter(is_archived=False)
        .select_related("subject__program", "teacher__user", "batch_semester__batch")
        .annotate(
            student_count=Count(
                "enrollments", filter=Q(enrollments__is_archived=False), distinct=True
            ),
            classes_held=Count(
                "attendance_sessions",
                filter=Q(attendance_sessions__is_archived=False),
                distinct=True,
            ),
            attended_records=Count(
                "attendance_sessions__records",
                filter=Q(
                    attendance_sessions__records__is_archived=False,
                    attendance_sessions__records__status__in=ATTENDED_STATUSES,
                ),
                distinct=True,
            ),
        ),
        user,
        path="teacher__user",
    )


def class_payload(allocation) -> dict:
    """
    One class as the class list renders it.

    Attendance percentage divides by every class held for the allocation, so a
    student who joined late is measured against the full term.
    """
    possible = allocation.student_count * allocation.classes_held

    return {
        "id": allocation.id,
        "allocation": allocation.id,
        "subject_id": allocation.subject_id,
        "code": allocation.subject.code,
        "name": allocation.subject.name,
        "program": allocation.subject.program.name,
        "program_code": allocation.subject.program.code,
        "semester": allocation.batch_semester.semester,
        "batch_year": allocation.batch_semester.batch.year,
        "teacher": {
            "id": allocation.teacher_id,
            "full_name": allocation.teacher.user.full_name or allocation.teacher.user.username,
        },
        "student_count": allocation.student_count,
        "classes_held": allocation.classes_held,
        "attendance_percentage": percentage(allocation.attended_records, possible),
    }


class ClassSummaryView(generics.GenericAPIView):
    """Every class the caller may see, with roster size and attendance."""

    permission_classes = (AttendancePermission,)
    pagination_class = None

    @extend_schema(responses=dict)
    def get(self, request):
        allocations = allocation_queryset(request.user).order_by(
            "batch_semester__batch__year", "subject__code"
        )
        return Response([class_payload(allocation) for allocation in allocations])


class ClassStudentSummaryView(generics.GenericAPIView):
    """
    Every student on one class, with the three parameters rolled up.

    This is what the students table and the student detail panel read.
    """

    permission_classes = (AttendancePermission,)
    pagination_class = None

    @extend_schema(
        parameters=[OpenApiParameter(name="allocation_id", location="path", type=int)],
        responses=dict,
    )
    def get(self, request, allocation_id):
        allocation = generics.get_object_or_404(allocation_queryset(request.user), pk=allocation_id)

        marks_total = (
            allocation.internal_exams.filter(is_archived=False).aggregate(total=Sum("full_marks"))[
                "total"
            ]
            or 0
        )
        assignments_total = allocation.assignments.filter(is_archived=False).count()

        enrollments = (
            SubjectEnrollment.objects.filter(allocation=allocation, is_archived=False)
            .select_related("student")
            .annotate(
                attended=Count(
                    "attendance_records",
                    filter=Q(
                        attendance_records__is_archived=False,
                        attendance_records__status__in=ATTENDED_STATUSES,
                    ),
                    distinct=True,
                ),
                # A subquery, not an annotation: summing across a second
                # multi-valued join would multiply the total by the number of
                # attendance rows. distinct=True saves a Count, not a Sum.
                marks_obtained=Coalesce(
                    Subquery(
                        InternalExamMark.objects.filter(
                            enrollment=OuterRef("pk"), is_archived=False
                        )
                        .values("enrollment")
                        .annotate(total=Sum("marks_obtained"))
                        .values("total"),
                        output_field=DecimalField(max_digits=8, decimal_places=2),
                    ),
                    Value(0, output_field=DecimalField(max_digits=8, decimal_places=2)),
                ),
                done_count=Count(
                    "assignment_submissions",
                    filter=Q(
                        assignment_submissions__is_archived=False,
                        assignment_submissions__status=AssignmentStatus.DONE.value,
                    ),
                    distinct=True,
                ),
            )
            .order_by("student__roll_number")
        )

        return Response(
            [
                {
                    "enrollment": enrollment.id,
                    "student_id": enrollment.student_id,
                    "roll_number": enrollment.student.roll_number,
                    "registration_number": enrollment.student.registration_number,
                    "full_name": enrollment.student.full_name,
                    "is_retake": enrollment.is_retake,
                    "attendance": {
                        "held": allocation.classes_held,
                        "attended": enrollment.attended,
                        "percentage": percentage(enrollment.attended, allocation.classes_held),
                    },
                    "internal_marks": {
                        "obtained": float(enrollment.marks_obtained or 0),
                        "total": marks_total,
                    },
                    "assignments": {
                        "done": enrollment.done_count,
                        "total": assignments_total,
                    },
                }
                for enrollment in enrollments
            ]
        )


class OverviewView(generics.GenericAPIView):
    """Headline numbers for the dashboard, scoped to the caller."""

    permission_classes = (AttendancePermission,)
    pagination_class = None

    @extend_schema(responses=dict)
    def get(self, request):
        today = timezone.localdate()
        allocations = list(allocation_queryset(request.user))
        allocation_ids = [allocation.id for allocation in allocations]

        recorded_today = set(
            AttendanceSession.objects.filter(
                allocation_id__in=allocation_ids, date=today, is_archived=False
            ).values_list("allocation_id", flat=True)
        )

        attended = sum(allocation.attended_records for allocation in allocations)
        possible = sum(
            allocation.student_count * allocation.classes_held for allocation in allocations
        )
        total_students = sum(allocation.student_count for allocation in allocations)

        at_risk = self.students_below_threshold(allocations)

        return Response(
            {
                "stats": {
                    "total_classes": len(allocations),
                    "total_students": total_students,
                    "avg_attendance_percentage": percentage(attended, possible),
                    "students_below_eligibility": len(at_risk),
                    "classes_recorded_today": len(recorded_today),
                    "classes_total_today": len(allocations),
                },
                "pending_attendance_count": len(allocations) - len(recorded_today),
                "todays_classes": [
                    {
                        **class_payload(allocation),
                        "recorded": allocation.id in recorded_today,
                    }
                    for allocation in allocations
                ],
                "students_needing_attention": at_risk[:10],
                "recent_activity": self.recent_activity(allocation_ids),
            }
        )

    def students_below_threshold(self, allocations) -> list[dict]:
        """Students under the attendance requirement, worst first."""
        rows = []

        for allocation in allocations:
            if not allocation.classes_held:
                continue

            enrollments = (
                SubjectEnrollment.objects.filter(allocation=allocation, is_archived=False)
                .select_related("student")
                .annotate(
                    attended=Count(
                        "attendance_records",
                        filter=Q(
                            attendance_records__is_archived=False,
                            attendance_records__status__in=ATTENDED_STATUSES,
                        ),
                        distinct=True,
                    )
                )
            )

            for enrollment in enrollments:
                value = percentage(enrollment.attended, allocation.classes_held)
                if value < ELIGIBILITY_THRESHOLD:
                    rows.append(
                        {
                            "student_id": enrollment.student_id,
                            "enrollment": enrollment.id,
                            "full_name": enrollment.student.full_name,
                            "roll_number": enrollment.student.roll_number,
                            "subject": allocation.subject.name,
                            "subject_code": allocation.subject.code,
                            "semester": allocation.batch_semester.semester,
                            "attendance_percentage": value,
                        }
                    )

        return sorted(rows, key=lambda row: row["attendance_percentage"])

    def recent_activity(self, allocation_ids: list[int]) -> list[dict]:
        """The last week of classes recorded, newest first."""
        since = timezone.localdate() - timedelta(days=7)

        sessions = (
            AttendanceSession.objects.filter(
                allocation_id__in=allocation_ids, date__gte=since, is_archived=False
            )
            .select_related("allocation__subject")
            .annotate(marked=Count("records", filter=Q(records__is_archived=False), distinct=True))
            .order_by("-date", "-id")[:10]
        )

        return [
            {
                "id": session.id,
                "type": "success",
                "message": (
                    f"Attendance recorded for {session.allocation.subject.code} "
                    f"({session.marked} students)"
                ),
                "timestamp": session.created_at.isoformat(),
            }
            for session in sessions
        ]
