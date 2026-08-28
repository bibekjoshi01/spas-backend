"""
Aggregate reads for the teacher-facing screens.

These are the only endpoints that compute rather than store: attendance
percentages, internal mark totals and assignment completion. Keeping them here
means the write endpoints stay narrow and every screen has one call to make.
"""

from datetime import timedelta

from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

# Project Imports
from src.academics.constants import SemesterStatus
from src.academics.models import SubjectAllocation
from src.libs.permissions import get_permissions_for_user, scope_to_allocation_owner
from src.libs.scoping import management_scope, scope_by_authority
from src.students.models import Student, SubjectEnrollment

from .constants import AssignmentStatus, AttendanceStatus
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
from .permissions import AttendancePermission
from .serializers import AttendanceAttentionSerializer

# A student is counted as having attended when they were there at all. Excused
# absences still count against the requirement, which is the strict reading
# colleges apply to the 75% rule.
ATTENDED_STATUSES = (AttendanceStatus.PRESENT.value, AttendanceStatus.LATE.value)

ELIGIBILITY_THRESHOLD = 75.0


class ManagementAuthorityPermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and not management_scope(user).is_empty
        )


class ManagementStudentReportPermission(ManagementAuthorityPermission):
    required_permissions = (
        "view_student",
        "view_attendance",
        "view_internal_exam",
        "view_assignment",
        "view_class_performance",
    )

    def has_permission(self, request, view):
        return super().has_permission(request, view) and set(self.required_permissions).issubset(
            get_permissions_for_user(request.user)
        )


def percentage(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def weighted_percentage(metrics: list[tuple[float | None, int]]) -> float | None:
    """Re-normalize configured weights across only metrics with evidence."""
    available = [(value, weight) for value, weight in metrics if value is not None and weight > 0]
    total_weight = sum(weight for _, weight in available)
    if not total_weight:
        return None
    return round(sum(value * weight for value, weight in available) / total_weight, 1)


def schedule_ordered(queryset):
    """Chronological class order, with unscheduled classes kept at the end."""
    return queryset.order_by(
        F("start_time").asc(nulls_last=True),
        F("end_time").asc(nulls_last=True),
        "subject__code",
        "id",
    )


def annotated_allocations():
    return (
        SubjectAllocation.objects.filter(is_archived=False)
        .select_related("subject__program", "teacher", "batch_semester__batch")
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
        )
    )


def allocation_queryset(user):
    """Every teaching class owned by the user, with its headline counts."""
    return scope_to_allocation_owner(
        annotated_allocations(),
        user,
        path="teacher",
    )


def overview_allocation_queryset(user):
    """Dashboard classes: own for teachers, hierarchy-scoped for managers."""
    queryset = annotated_allocations()
    authority = management_scope(user)
    if not authority.is_empty:
        return scope_by_authority(
            queryset,
            user,
            department_path="subject__program__department_id",
            program_path="subject__program_id",
        )
    if user.roles.filter(codename="TEACHER").exists():
        return scope_to_allocation_owner(queryset, user, path="teacher")
    return queryset.none()


def management_level(user) -> str | None:
    authority = management_scope(user)
    if authority.unlimited:
        return "CAMPUS"
    if authority.by_programme:
        return "PROGRAM"
    if authority.department_ids:
        return "DEPARTMENT"
    return None


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
        "semester_status": allocation.batch_semester.status,
        "semester_start_date": allocation.batch_semester.start_date,
        "semester_end_date": allocation.batch_semester.end_date,
        "batch_year": allocation.batch_semester.batch.year,
        "start_time": allocation.start_time,
        "end_time": allocation.end_time,
        "teacher": {
            "id": allocation.teacher_id,
            "full_name": allocation.teacher.full_name or allocation.teacher.username,
        },
        "student_count": allocation.student_count,
        "classes_held": allocation.classes_held,
        "attendance_percentage": percentage(allocation.attended_records, possible),
    }


class ClassSummaryView(generics.GenericAPIView):
    """Every class the caller may see, with roster size and attendance."""

    permission_classes = (AttendancePermission,)
    pagination_class = None

    @extend_schema(
        operation_id="performance_class_list",
        parameters=[
            OpenApiParameter(
                name="semester_status",
                type=str,
                enum=[item.value for item in SemesterStatus],
                description="Limit classes to one semester lifecycle state.",
            )
        ],
        responses=dict,
    )
    def get(self, request):
        allocations = allocation_queryset(request.user)
        semester_status = request.query_params.get("semester_status")
        allowed_statuses = {item.value for item in SemesterStatus}
        if semester_status and semester_status not in allowed_statuses:
            return Response(
                {"semester_status": "Choose UPCOMING, RUNNING, or COMPLETED."},
                status=400,
            )
        if semester_status:
            allocations = allocations.filter(batch_semester__status=semester_status)

        allocations = schedule_ordered(allocations)
        return Response([class_payload(allocation) for allocation in allocations])


class AttendanceAttentionView(generics.GenericAPIView):
    """Management-only queue of active class enrollments below 75% attendance."""

    permission_classes = (ManagementAuthorityPermission,)
    serializer_class = AttendanceAttentionSerializer

    def get(self, request):
        queryset = SubjectEnrollment.objects.filter(
            is_archived=False,
            student__is_archived=False,
            allocation__is_archived=False,
            allocation__batch_semester__status=SemesterStatus.RUNNING.value,
        ).select_related(
            "student",
            "allocation__teacher",
            "allocation__subject__program",
            "allocation__batch_semester__batch",
        )
        queryset = (
            scope_by_authority(
                queryset,
                request.user,
                department_path="allocation__subject__program__department_id",
                program_path="allocation__subject__program_id",
            )
            .annotate(
                classes_held=Count(
                    "allocation__attendance_sessions",
                    filter=Q(allocation__attendance_sessions__is_archived=False),
                    distinct=True,
                ),
                present_count=Count(
                    "attendance_records",
                    filter=Q(
                        attendance_records__is_archived=False,
                        attendance_records__session__is_archived=False,
                        attendance_records__status=AttendanceStatus.PRESENT.value,
                    ),
                    distinct=True,
                ),
                absent_count=Count(
                    "attendance_records",
                    filter=Q(
                        attendance_records__is_archived=False,
                        attendance_records__session__is_archived=False,
                        attendance_records__status=AttendanceStatus.ABSENT.value,
                    ),
                    distinct=True,
                ),
                late_count=Count(
                    "attendance_records",
                    filter=Q(
                        attendance_records__is_archived=False,
                        attendance_records__session__is_archived=False,
                        attendance_records__status=AttendanceStatus.LATE.value,
                    ),
                    distinct=True,
                ),
                excused_count=Count(
                    "attendance_records",
                    filter=Q(
                        attendance_records__is_archived=False,
                        attendance_records__session__is_archived=False,
                        attendance_records__status=AttendanceStatus.EXCUSED.value,
                    ),
                    distinct=True,
                ),
                last_attendance_date=Max(
                    "attendance_records__session__date",
                    filter=Q(
                        attendance_records__is_archived=False,
                        attendance_records__session__is_archived=False,
                    ),
                ),
            )
            .annotate(
                attendance_percentage=ExpressionWrapper(
                    100.0 * (F("present_count") + F("late_count")) / NullIf(F("classes_held"), 0),
                    output_field=FloatField(),
                )
            )
        )

        queryset = queryset.filter(
            classes_held__gt=0,
            attendance_percentage__lt=ELIGIBILITY_THRESHOLD,
        )
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(student__first_name__icontains=search)
                | Q(student__last_name__icontains=search)
                | Q(student__roll_number__icontains=search)
                | Q(student__phone_no__icontains=search)
                | Q(allocation__subject__code__icontains=search)
            )
        for parameter, field in (
            ("program", "allocation__subject__program_id"),
            ("batch", "allocation__batch_semester__batch_id"),
            ("allocation", "allocation_id"),
        ):
            value = request.query_params.get(parameter)
            if value and value.isdigit():
                queryset = queryset.filter(**{field: int(value)})

        ordering = request.query_params.get("ordering", "attendance_percentage")
        ordering_fields = {
            "attendance_percentage": "attendance_percentage",
            "-attendance_percentage": "-attendance_percentage",
            "roll_number": "student__roll_number",
            "-last_attendance_date": "-last_attendance_date",
        }
        ordering = ordering_fields.get(ordering, "attendance_percentage")
        queryset = queryset.order_by(ordering, "student__roll_number", "id")

        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class ManagementStudentReportView(generics.GenericAPIView):
    """A student's complete subject record, restricted to management authority."""

    permission_classes = (ManagementStudentReportPermission,)
    pagination_class = None

    @extend_schema(operation_id="performance_management_student_report", responses=dict)
    def get(self, request, student_id):
        students = scope_by_authority(
            Student.objects.select_related("batch__program__department"),
            request.user,
            department_path="batch__program__department_id",
            program_path="batch__program_id",
        )
        student = generics.get_object_or_404(students, pk=student_id)
        allocations = {
            allocation.id: allocation
            for allocation in annotated_allocations().filter(
                enrollments__student=student,
                enrollments__is_archived=False,
            )
        }
        enrollments = (
            SubjectEnrollment.objects.filter(
                student=student,
                is_archived=False,
                allocation__is_archived=False,
            )
            .select_related(
                "allocation__teacher",
                "allocation__subject__program",
                "allocation__batch_semester__batch",
            )
            .order_by(
                "allocation__batch_semester__semester",
                "allocation__subject__code",
            )
        )
        permissions = get_permissions_for_user(request.user)
        subjects = []
        for enrollment in enrollments:
            allocation = allocations[enrollment.allocation_id]
            subjects.append(
                {
                    "enrollment": enrollment.id,
                    "semester": allocation.batch_semester.semester,
                    "semester_status": allocation.batch_semester.status,
                    "class": class_payload(allocation),
                    "attendance": ClassStudentDetailView.attendance_payload(
                        allocation, enrollment, permissions
                    ),
                    "assessments": ClassStudentDetailView.assessment_payload(
                        allocation, enrollment, permissions
                    ),
                    "assignments": ClassStudentDetailView.assignment_payload(
                        allocation, enrollment, permissions
                    ),
                    "class_performance": ClassStudentDetailView.rating_payload(
                        enrollment, permissions
                    ),
                }
            )

        return Response(
            {
                "student": {
                    "id": student.id,
                    "roll_number": student.roll_number,
                    "registration_number": student.registration_number,
                    "full_name": student.full_name,
                    "email": student.email,
                    "phone_no": student.phone_no,
                    "alternate_phone_no": student.alternate_phone_no,
                    "status": student.status,
                    "program_code": student.batch.program.code,
                    "program_name": student.batch.program.name,
                    "department_name": student.batch.program.department.name,
                    "batch_year": student.batch.year,
                },
                "subjects": subjects,
            }
        )


class ClassStudentSummaryView(generics.GenericAPIView):
    """
    Every student on one class, with the three parameters rolled up.

    This is what the students table and the student detail panel read.
    """

    permission_classes = (AttendancePermission,)
    pagination_class = None

    @extend_schema(
        operation_id="performance_class_student_list",
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

        enrollments = list(
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
                class_performance_score=Subquery(
                    ClassPerformanceRating.objects.filter(
                        enrollment=OuterRef("pk"), is_archived=False
                    ).values("score")[:1]
                ),
            )
            .order_by("student__roll_number")
        )

        enrollment_ids = [enrollment.id for enrollment in enrollments]
        assessment_metrics = {
            row["enrollment"]: {
                "obtained": float(row["obtained"] or 0),
                "total": row["total"],
            }
            for row in InternalExamMark.objects.filter(
                enrollment_id__in=enrollment_ids,
                is_archived=False,
                exam__is_archived=False,
            )
            .values("enrollment")
            .annotate(obtained=Sum("marks_obtained"), total=Sum("exam__full_marks"))
        }
        assignment_metrics = {enrollment_id: [] for enrollment_id in enrollment_ids}
        for submission in AssignmentSubmission.objects.filter(
            enrollment_id__in=enrollment_ids,
            is_archived=False,
            assignment__is_archived=False,
        ).values("enrollment_id", "status"):
            assignment_metrics[submission["enrollment_id"]].append(submission["status"])

        weights = (
            PerformanceWeightConfiguration.objects.filter(singleton_key=True).first()
            or PerformanceWeightConfiguration()
        )

        recent_attendance = {enrollment.id: [] for enrollment in enrollments}
        recent_records = (
            AttendanceRecord.objects.filter(
                enrollment_id__in=recent_attendance,
                is_archived=False,
                session__is_archived=False,
            )
            .select_related("session")
            .order_by("enrollment_id", "-session__date", "-session__period", "-id")
        )
        for record in recent_records:
            rows = recent_attendance[record.enrollment_id]
            if len(rows) < 5:
                rows.append(
                    {
                        "date": record.session.date,
                        "period": record.session.period,
                        "status": record.status,
                    }
                )

        rows = []
        for enrollment in enrollments:
            assessment = assessment_metrics.get(enrollment.id)
            submissions = assignment_metrics[enrollment.id]
            assignment_points = sum(
                100
                if value == AssignmentStatus.DONE.value
                else 50
                if value == AssignmentStatus.PARTIAL.value
                else 0
                for value in submissions
            )
            performance_percentage = weighted_percentage(
                [
                    (
                        percentage(enrollment.attended, allocation.classes_held)
                        if allocation.classes_held
                        else None,
                        weights.attendance_weight,
                    ),
                    (
                        enrollment.class_performance_score * 10
                        if enrollment.class_performance_score is not None
                        else None,
                        weights.class_performance_weight,
                    ),
                    (
                        percentage(assignment_points, len(submissions) * 100)
                        if submissions
                        else None,
                        weights.assignment_weight,
                    ),
                    (
                        percentage(assessment["obtained"], assessment["total"])
                        if assessment and assessment["total"]
                        else None,
                        weights.assessment_weight,
                    ),
                ]
            )
            rows.append(
                {
                    "enrollment": enrollment.id,
                    "student_id": enrollment.student_id,
                    "roll_number": enrollment.student.roll_number,
                    "registration_number": enrollment.student.registration_number,
                    "full_name": enrollment.student.full_name,
                    "email": enrollment.student.email,
                    "phone_no": enrollment.student.phone_no,
                    "alternate_phone_no": enrollment.student.alternate_phone_no,
                    "is_retake": enrollment.is_retake,
                    "attendance": {
                        "held": allocation.classes_held,
                        "attended": enrollment.attended,
                        "percentage": percentage(enrollment.attended, allocation.classes_held),
                        "recent": recent_attendance[enrollment.id],
                    },
                    "internal_marks": {
                        "obtained": float(enrollment.marks_obtained or 0),
                        "total": marks_total,
                    },
                    "assignments": {
                        "done": enrollment.done_count,
                        "total": assignments_total,
                    },
                    "class_performance": {
                        "score": enrollment.class_performance_score,
                        "scale": 10,
                    },
                    "performance_percentage": performance_percentage,
                }
            )
        return Response(rows)


class ClassStudentDetailView(generics.GenericAPIView):
    """One roster student's complete performance record for one owned class."""

    permission_classes = (AttendancePermission,)
    pagination_class = None

    @extend_schema(operation_id="performance_class_student_detail", responses=dict)
    def get(self, request, allocation_id, enrollment_id):
        allocation = generics.get_object_or_404(allocation_queryset(request.user), pk=allocation_id)
        enrollment = generics.get_object_or_404(
            SubjectEnrollment.objects.select_related("student").filter(
                allocation=allocation, is_archived=False
            ),
            pk=enrollment_id,
        )
        permissions = set(get_permissions_for_user(request.user))

        attendance = self.attendance_payload(allocation, enrollment, permissions)
        assessments = self.assessment_payload(allocation, enrollment, permissions)
        assignments = self.assignment_payload(allocation, enrollment, permissions)
        rating = self.rating_payload(enrollment, permissions)
        student = enrollment.student

        return Response(
            {
                "enrollment": enrollment.id,
                "student": {
                    "id": student.id,
                    "roll_number": student.roll_number,
                    "registration_number": student.registration_number,
                    "full_name": student.full_name,
                    "email": student.email,
                    "phone_no": student.phone_no,
                    "alternate_phone_no": student.alternate_phone_no,
                },
                "class": class_payload(allocation),
                "attendance": attendance,
                "assessments": assessments,
                "assignments": assignments,
                "class_performance": rating,
            }
        )

    @staticmethod
    def attendance_payload(allocation, enrollment, permissions):
        if "view_attendance" not in permissions:
            return None
        counts = {status.value: 0 for status in AttendanceStatus}
        records = (
            enrollment.attendance_records.filter(is_archived=False, session__is_archived=False)
            .values("status")
            .annotate(total=Count("id"))
        )
        for row in records:
            counts[row["status"]] = row["total"]

        held = allocation.attendance_sessions.filter(is_archived=False).count()
        attended = counts[AttendanceStatus.PRESENT.value] + counts[AttendanceStatus.LATE.value]
        return {
            "held": held,
            "present": counts[AttendanceStatus.PRESENT.value],
            "absent": counts[AttendanceStatus.ABSENT.value],
            "excused": counts[AttendanceStatus.EXCUSED.value],
            "late": counts[AttendanceStatus.LATE.value],
            "percentage": percentage(attended, held),
        }

    @staticmethod
    def assessment_payload(allocation, enrollment, permissions):
        if "view_internal_exam" not in permissions:
            return []
        marks = {
            mark.exam_id: mark
            for mark in enrollment.internal_marks.filter(is_archived=False, exam__is_archived=False)
        }
        return [
            {
                "exam_id": exam.id,
                "title": exam.title,
                "exam_type": exam.exam_type,
                "exam_date": exam.exam_date,
                "full_marks": exam.full_marks,
                "pass_marks": exam.pass_marks,
                "marks_obtained": marks[exam.id].marks_obtained if exam.id in marks else None,
                "is_absent": marks[exam.id].is_absent if exam.id in marks else False,
            }
            for exam in allocation.internal_exams.filter(is_archived=False).order_by(
                "-exam_date", "title"
            )
        ]

    @staticmethod
    def assignment_payload(allocation, enrollment, permissions):
        if "view_assignment" not in permissions:
            return []
        submissions = {
            submission.assignment_id: submission
            for submission in enrollment.assignment_submissions.filter(
                is_archived=False, assignment__is_archived=False
            )
        }
        return [
            {
                "assignment_id": assignment.id,
                "title": assignment.title,
                "assigned_date": assignment.assigned_date,
                "due_date": assignment.due_date,
                "status": submissions[assignment.id].status
                if assignment.id in submissions
                else None,
                "remarks": submissions[assignment.id].remarks
                if assignment.id in submissions
                else "",
            }
            for assignment in allocation.assignments.filter(is_archived=False).order_by(
                "-assigned_date", "title"
            )
        ]

    @staticmethod
    def rating_payload(enrollment, permissions):
        if "view_class_performance" not in permissions:
            return None
        rating = enrollment.class_performance_ratings.filter(is_archived=False).first()
        if not rating:
            return None
        return {
            "score": rating.score,
            "remarks": rating.remarks,
            "updated_at": rating.updated_at,
        }


class OverviewView(generics.GenericAPIView):
    """Headline numbers for the dashboard, scoped to the caller."""

    permission_classes = (IsAuthenticated,)
    pagination_class = None

    @extend_schema(responses=dict)
    def get(self, request):
        today = timezone.localdate()
        allocations = list(
            schedule_ordered(
                overview_allocation_queryset(request.user).filter(
                    batch_semester__status=SemesterStatus.RUNNING.value
                )
            )
        )
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
        total_students = (
            SubjectEnrollment.objects.filter(
                allocation_id__in=allocation_ids,
                is_archived=False,
                student__is_archived=False,
            )
            .values("student_id")
            .distinct()
            .count()
        )

        at_risk = self.students_below_threshold(allocations)
        level = management_level(request.user)
        work_queue = self.teacher_work_queue(
            allocations, recorded_today, set(get_permissions_for_user(request.user)), today
        )

        return Response(
            {
                "experience": "MANAGEMENT" if level else "TEACHER",
                "management_level": level,
                "stats": {
                    "total_classes": len(allocations),
                    "total_students": total_students,
                    "avg_attendance_percentage": percentage(attended, possible),
                    "students_below_eligibility": len(at_risk),
                    "classes_recorded_today": len(recorded_today),
                    "classes_total_today": len(allocations),
                },
                "pending_attendance_count": len(allocations) - len(recorded_today),
                "today_attendance": self.today_attendance(allocations, recorded_today, today),
                "todays_classes": [
                    {
                        **class_payload(allocation),
                        "recorded": allocation.id in recorded_today,
                    }
                    for allocation in allocations
                ],
                "students_needing_attention": at_risk[:10],
                "work_queue": work_queue,
                "recent_activity": self.recent_activity(allocation_ids),
            }
        )

    @staticmethod
    def today_attendance(allocations, recorded_today, today) -> dict:
        allocation_ids = [allocation.id for allocation in allocations]
        records = AttendanceRecord.objects.filter(
            session__allocation_id__in=allocation_ids,
            session__date=today,
            session__is_archived=False,
            is_archived=False,
        )
        counts = records.aggregate(
            marked=Count("id"),
            present=Count("id", filter=Q(status=AttendanceStatus.PRESENT.value)),
            absent=Count("id", filter=Q(status=AttendanceStatus.ABSENT.value)),
            late=Count("id", filter=Q(status=AttendanceStatus.LATE.value)),
            excused=Count("id", filter=Q(status=AttendanceStatus.EXCUSED.value)),
        )
        sessions = AttendanceSession.objects.filter(
            allocation_id__in=allocation_ids,
            date=today,
            is_archived=False,
        ).count()
        attended = counts["present"] + counts["late"]

        return {
            "sessions_recorded": sessions,
            "classes_recorded": len(recorded_today),
            "active_classes": len(allocations),
            "marked": counts["marked"],
            "present": counts["present"],
            "absent": counts["absent"],
            "late": counts["late"],
            "excused": counts["excused"],
            "attendance_percentage": percentage(attended, counts["marked"]),
            "classes_to_review": [
                class_payload(allocation)
                for allocation in allocations
                if allocation.id not in recorded_today
            ][:10],
        }

    @staticmethod
    def teacher_work_queue(allocations, recorded_today, permissions, today) -> list[dict]:
        """Small, actionable queue for active classes; never crosses the caller's scope."""
        if not allocations:
            return []

        allocation_by_id = {allocation.id: allocation for allocation in allocations}
        allocation_ids = list(allocation_by_id)
        rows = []

        if {"add_attendance", "edit_attendance"} & permissions:
            for allocation in allocations:
                if allocation.id not in recorded_today:
                    rows.append(
                        {
                            "key": f"attendance-{allocation.id}",
                            "kind": "ATTENDANCE",
                            "allocation": allocation.id,
                            "subject_code": allocation.subject.code,
                            "class_label": (
                                f"{allocation.subject.program.code} {allocation.batch_semester.batch.year}"
                                f" · Semester {allocation.batch_semester.semester}"
                            ),
                            "title": "Attendance not recorded today",
                            "detail": "Open the roster and record this class when it is held.",
                            "remaining": allocation.student_count,
                            "due_date": today,
                            "priority": 0,
                        }
                    )

        if "edit_internal_exam" in permissions:
            exams = (
                InternalExam.objects.filter(allocation_id__in=allocation_ids, is_archived=False)
                .annotate(
                    marked_count=Count("marks", filter=Q(marks__is_archived=False), distinct=True)
                )
                .order_by("exam_date", "id")
            )
            for exam in exams:
                allocation = allocation_by_id[exam.allocation_id]
                remaining = max(allocation.student_count - exam.marked_count, 0)
                if remaining:
                    rows.append(
                        {
                            "key": f"assessment-{exam.id}",
                            "kind": "ASSESSMENT",
                            "allocation": allocation.id,
                            "subject_code": allocation.subject.code,
                            "class_label": (
                                f"{allocation.subject.program.code} {allocation.batch_semester.batch.year}"
                                f" · Semester {allocation.batch_semester.semester}"
                            ),
                            "title": f"Complete marks for {exam.title}",
                            "detail": f"{remaining} student{'s' if remaining != 1 else ''} still unmarked.",
                            "remaining": remaining,
                            "due_date": exam.exam_date,
                            "priority": 1,
                        }
                    )

        if "edit_assignment" in permissions:
            assignments = (
                Assignment.objects.filter(allocation_id__in=allocation_ids, is_archived=False)
                .annotate(
                    evaluated_count=Count(
                        "submissions", filter=Q(submissions__is_archived=False), distinct=True
                    )
                )
                .order_by("due_date", "id")
            )
            for assignment in assignments:
                allocation = allocation_by_id[assignment.allocation_id]
                remaining = max(allocation.student_count - assignment.evaluated_count, 0)
                if remaining:
                    rows.append(
                        {
                            "key": f"assignment-{assignment.id}",
                            "kind": "ASSIGNMENT",
                            "allocation": allocation.id,
                            "subject_code": allocation.subject.code,
                            "class_label": (
                                f"{allocation.subject.program.code} {allocation.batch_semester.batch.year}"
                                f" · Semester {allocation.batch_semester.semester}"
                            ),
                            "title": f"Evaluate {assignment.title}",
                            "detail": f"{remaining} student{'s' if remaining != 1 else ''} still unevaluated.",
                            "remaining": remaining,
                            "due_date": assignment.due_date,
                            "priority": 1,
                        }
                    )

        if "edit_class_performance" in permissions:
            rated_by_allocation = dict(
                ClassPerformanceRating.objects.filter(
                    enrollment__allocation_id__in=allocation_ids,
                    enrollment__is_archived=False,
                    is_archived=False,
                )
                .values("enrollment__allocation_id")
                .annotate(total=Count("id", distinct=True))
                .values_list("enrollment__allocation_id", "total")
            )
            for allocation in allocations:
                remaining = max(
                    allocation.student_count - rated_by_allocation.get(allocation.id, 0), 0
                )
                if remaining:
                    rows.append(
                        {
                            "key": f"performance-{allocation.id}",
                            "kind": "PERFORMANCE",
                            "allocation": allocation.id,
                            "subject_code": allocation.subject.code,
                            "class_label": (
                                f"{allocation.subject.program.code} {allocation.batch_semester.batch.year}"
                                f" · Semester {allocation.batch_semester.semester}"
                            ),
                            "title": "Complete class performance ratings",
                            "detail": f"{remaining} student{'s' if remaining != 1 else ''} still unrated.",
                            "remaining": remaining,
                            "due_date": None,
                            "priority": 2,
                        }
                    )

        return sorted(
            rows,
            key=lambda row: (
                row["priority"],
                row["due_date"] is None,
                row["due_date"] or today,
                row["subject_code"],
                row["key"],
            ),
        )[:20]

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
