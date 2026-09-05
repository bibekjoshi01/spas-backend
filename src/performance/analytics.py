"""
Aggregate reads for the teacher-facing screens.

These are the only endpoints that compute rather than store: attendance
percentages, internal mark totals and assignment completion. Keeping them here
means the write endpoints stay narrow and every screen has one call to make.
"""

from collections import defaultdict
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
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

# Project Imports
from src.academics.constants import SemesterStatus
from src.academics.models import BatchSemester, SubjectAllocation
from src.libs.permissions import get_permissions_for_user, scope_to_allocation_owner
from src.libs.scoping import management_scope, scope_by_authority
from src.students.models import SemesterEnrollment, Student, SubjectEnrollment
from src.students.permissions import StudentPortalPermission

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
from .trends import class_attendance_trends, student_attendance_trends

# A student is counted as having attended when they were there at all. Excused
# absences still count against the requirement, which is the strict reading
# colleges apply to the attendance rule.
ATTENDED_STATUSES = (AttendanceStatus.PRESENT.value, AttendanceStatus.LATE.value)


def eligibility_threshold() -> float:
    """The attendance percentage this college requires."""
    return PerformanceWeightConfiguration.current().eligibility_threshold


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


class ManagementAttendanceReportPermission(ManagementAuthorityPermission):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and "view_attendance" in set(
            get_permissions_for_user(request.user)
        )


def percentage(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def weighted_percentage(metrics: list[tuple[float | None, int]]) -> float | None:
    """Re-normalize configured weights across only metrics with evidence."""
    available = [(value, weight) for value, weight in metrics if value is not None and weight > 0]
    total_weight = sum(weight for _, weight in available)
    if not total_weight:
        return None
    return round(sum(value * weight for value, weight in available) / total_weight, 2)


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
    queryset = annotated_allocations().prefetch_related("meetings")
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


def meets_on(allocation, weekday: int) -> bool:
    """
    Whether this class sits on the given weekday.

    A class nobody has timetabled yet counts as meeting every day. That is what
    every allocation looks like before the timetable is filled in, and treating
    it as meeting nothing would empty the dashboard of a college that has not
    got round to scheduling — a worse answer than the one this replaces.
    """
    meetings = [meeting for meeting in allocation.meetings.all() if not meeting.is_archived]
    if not meetings:
        return True
    return any(meeting.weekday == weekday for meeting in meetings)


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
        "meetings": [
            {
                "weekday": meeting.weekday,
                "start_time": meeting.start_time,
                "end_time": meeting.end_time,
            }
            for meeting in allocation.meetings.all()
            if not meeting.is_archived
        ],
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

        allocations = list(schedule_ordered(allocations))
        # One extra pair of queries for the whole list, so every class card can
        # say which way it is going without a request of its own.
        trends = class_attendance_trends([allocation.id for allocation in allocations])
        return Response(
            [
                {**class_payload(allocation), "trend": trends.get(allocation.id)}
                for allocation in allocations
            ]
        )


class AttendanceAttentionView(generics.GenericAPIView):
    """
    Management-only queue of active class enrollments below the college's
    attendance requirement.
    """

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
            attendance_percentage__lt=eligibility_threshold(),
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

        ordering = request.query_params.get("ordering", "full_name")
        ordering_fields = {
            "full_name": "student__first_name",
            "-full_name": "-student__first_name",
            "attendance_percentage": "attendance_percentage",
            "-attendance_percentage": "-attendance_percentage",
            "roll_number": "student__roll_number",
            "-last_attendance_date": "-last_attendance_date",
        }
        ordering = ordering_fields.get(ordering, "student__first_name")
        descending = ordering.startswith("-")
        prefix = "-" if descending else ""
        queryset = queryset.order_by(
            ordering,
            f"{prefix}student__middle_name",
            f"{prefix}student__last_name",
            "id",
        )

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
        return BatchSemesterPerformanceReportView.build_student_report(
            students, student_id, request.user
        )


class StudentPortalOverviewView(generics.GenericAPIView):
    """The authenticated student's own record; no student identifier is accepted."""

    permission_classes = (StudentPortalPermission,)
    pagination_class = None

    @extend_schema(operation_id="student_portal_overview", responses=dict)
    def get(self, request):
        student = request.user.student_profile
        response = BatchSemesterPerformanceReportView.build_student_report(
            Student.objects.filter(pk=student.pk).select_related("batch__program__department"),
            student.pk,
            request.user,
            permissions={
                "view_attendance",
                "view_internal_exam",
                "view_assignment",
                "view_class_performance",
            },
        )
        policy = PerformanceWeightConfiguration.current()
        response.data["policy"] = {
            "attendance_weight": policy.attendance_weight,
            "class_performance_weight": policy.class_performance_weight,
            "assignment_weight": policy.assignment_weight,
            "assessment_weight": policy.assessment_weight,
            "attendance_eligibility_threshold": policy.attendance_eligibility_threshold,
        }
        return response


class BatchSemesterPerformanceReportView(generics.GenericAPIView):
    """Management cohort report across every subject in one batch semester."""

    permission_classes = (ManagementStudentReportPermission,)

    @extend_schema(operation_id="performance_batch_semester_report", responses=dict)
    def get(self, request):
        semester_id = request.query_params.get("batch_semester")
        if not semester_id or not semester_id.isdigit():
            return Response({"batch_semester": "Select a valid batch semester."}, status=400)

        semesters = scope_by_authority(
            BatchSemester.objects.filter(is_archived=False).select_related(
                "batch__program__department"
            ),
            request.user,
            department_path="batch__program__department_id",
            program_path="batch__program_id",
        )
        semester = generics.get_object_or_404(semesters, pk=int(semester_id))
        semester_student_ids = set(
            SemesterEnrollment.objects.filter(
                batch_semester=semester,
                is_archived=False,
                student__is_archived=False,
            ).values_list("student_id", flat=True)
        )

        # Subject rosters predate mandatory semester-progression records in
        # some tenants. Include same-cohort roster students so valid teaching
        # data never disappears from management reports; retakes from another
        # admission batch remain outside this batch report.
        subject_enrollments = list(
            SubjectEnrollment.objects.filter(
                allocation__batch_semester=semester,
                student__batch=semester.batch,
                student__is_archived=False,
                is_archived=False,
                allocation__is_archived=False,
            ).select_related("student", "allocation__subject")
        )
        student_ids = semester_student_ids | {
            enrollment.student_id for enrollment in subject_enrollments
        }
        students = Student.objects.filter(
            id__in=student_ids,
            is_archived=False,
        ).order_by("first_name", "middle_name", "last_name", "id")
        enrollment_ids = [row.id for row in subject_enrollments]
        allocation_ids = {row.allocation_id for row in subject_enrollments}

        held_by_allocation = dict(
            AttendanceSession.objects.filter(allocation_id__in=allocation_ids, is_archived=False)
            .values("allocation_id")
            .annotate(total=Count("id"))
            .values_list("allocation_id", "total")
        )
        attendance_by_enrollment = {
            row["enrollment_id"]: row
            for row in AttendanceRecord.objects.filter(
                enrollment_id__in=enrollment_ids,
                is_archived=False,
                session__is_archived=False,
            )
            .values("enrollment_id")
            .annotate(
                present=Count("id", filter=Q(status=AttendanceStatus.PRESENT.value)),
                absent=Count("id", filter=Q(status=AttendanceStatus.ABSENT.value)),
                late=Count("id", filter=Q(status=AttendanceStatus.LATE.value)),
                excused=Count("id", filter=Q(status=AttendanceStatus.EXCUSED.value)),
            )
        }
        assessment_by_enrollment = {
            row["enrollment_id"]: row
            for row in InternalExamMark.objects.filter(
                enrollment_id__in=enrollment_ids,
                is_archived=False,
                exam__is_archived=False,
            )
            .values("enrollment_id")
            .annotate(
                obtained=Coalesce(
                    Sum("marks_obtained"),
                    Value(0, output_field=DecimalField(max_digits=10, decimal_places=2)),
                ),
                total=Sum("exam__full_marks"),
                recorded=Count("id"),
            )
        }
        assignment_by_enrollment = defaultdict(list)
        for submission in AssignmentSubmission.objects.filter(
            enrollment_id__in=enrollment_ids,
            is_archived=False,
            assignment__is_archived=False,
        ).values("enrollment_id", "status"):
            assignment_by_enrollment[submission["enrollment_id"]].append(submission["status"])
        rating_by_enrollment = dict(
            ClassPerformanceRating.objects.filter(
                enrollment_id__in=enrollment_ids, is_archived=False
            ).values_list("enrollment_id", "score")
        )

        weights = PerformanceWeightConfiguration.current()
        threshold = weights.eligibility_threshold
        by_student = defaultdict(list)
        for enrollment in subject_enrollments:
            by_student[enrollment.student_id].append(enrollment)

        rows = []
        for student in students:
            enrollments = by_student[student.id]
            held = sum(held_by_allocation.get(row.allocation_id, 0) for row in enrollments)
            attendance = {"present": 0, "absent": 0, "late": 0, "excused": 0}
            assessment_obtained = 0.0
            assessment_total = 0
            assessments_recorded = 0
            assignment_points = 0
            assignments_recorded = 0
            ratings = []
            for enrollment in enrollments:
                attendance_row = attendance_by_enrollment.get(enrollment.id, {})
                for status_name in attendance:
                    attendance[status_name] += attendance_row.get(status_name, 0)
                assessment = assessment_by_enrollment.get(enrollment.id)
                if assessment:
                    assessment_obtained += float(assessment["obtained"] or 0)
                    assessment_total += assessment["total"] or 0
                    assessments_recorded += assessment["recorded"]
                statuses = assignment_by_enrollment[enrollment.id]
                assignments_recorded += len(statuses)
                assignment_points += sum(
                    100
                    if status_value == AssignmentStatus.DONE.value
                    else 50
                    if status_value == AssignmentStatus.PARTIAL.value
                    else 0
                    for status_value in statuses
                )
                if enrollment.id in rating_by_enrollment:
                    ratings.append(rating_by_enrollment[enrollment.id])

            attended = attendance["present"] + attendance["late"]
            attendance_percentage = percentage(attended, held) if held else None
            assessment_percentage = (
                percentage(assessment_obtained, assessment_total) if assessment_total else None
            )
            assignment_percentage = (
                percentage(assignment_points, assignments_recorded * 100)
                if assignments_recorded
                else None
            )
            class_performance_percentage = (
                round(sum(ratings) / len(ratings) * 10, 2) if ratings else None
            )
            overall = weighted_percentage(
                [
                    (attendance_percentage, weights.attendance_weight),
                    (class_performance_percentage, weights.class_performance_weight),
                    (assignment_percentage, weights.assignment_weight),
                    (assessment_percentage, weights.assessment_weight),
                ]
            )
            needs_attention = bool(
                (attendance_percentage is not None and attendance_percentage < threshold)
                or (overall is not None and overall < 50)
            )
            rows.append(
                {
                    "student_id": student.id,
                    "roll_number": student.roll_number,
                    "registration_number": student.registration_number,
                    "full_name": student.full_name,
                    "email": student.email,
                    "phone_no": student.phone_no,
                    "alternate_phone_no": student.alternate_phone_no,
                    "subjects": len(enrollments),
                    "attendance": {**attendance, "held": held, "percentage": attendance_percentage},
                    "assessment": {
                        "obtained": round(assessment_obtained, 2),
                        "total": assessment_total,
                        "recorded": assessments_recorded,
                        "percentage": assessment_percentage,
                    },
                    "assignment": {
                        "recorded": assignments_recorded,
                        "percentage": assignment_percentage,
                    },
                    "class_performance_percentage": class_performance_percentage,
                    "overall_percentage": overall,
                    "needs_attention": needs_attention,
                }
            )

        all_rows = rows
        search = request.query_params.get("search", "").strip().casefold()
        if search:
            rows = [
                row
                for row in rows
                if search
                in " ".join(
                    (
                        row["full_name"],
                        row["roll_number"],
                        row["registration_number"],
                        row["phone_no"],
                    )
                ).casefold()
            ]
        if request.query_params.get("attention") == "true":
            rows = [row for row in rows if row["needs_attention"]]

        ordering = request.query_params.get("ordering", "full_name")
        if ordering in ("full_name", "-full_name"):
            reverse = ordering.startswith("-")
            rows.sort(
                key=lambda row: (row["full_name"].casefold(), row["student_id"]),
                reverse=reverse,
            )
        elif ordering == "roll_number":
            rows.sort(key=lambda row: (row["roll_number"], row["student_id"]))
        elif ordering == "-overall_percentage":
            rows.sort(
                key=lambda row: (
                    row["overall_percentage"] is None,
                    -(row["overall_percentage"] or 0),
                    row["roll_number"],
                )
            )
        else:
            rows.sort(
                key=lambda row: (
                    not row["needs_attention"],
                    row["overall_percentage"] is None,
                    row["overall_percentage"] or 0,
                    row["roll_number"],
                )
            )

        evidenced = [
            row["overall_percentage"] for row in all_rows if row["overall_percentage"] is not None
        ]
        page = self.paginate_queryset(rows)
        response = self.get_paginated_response(page)
        response.data["semester"] = {
            "id": semester.id,
            "semester": semester.semester,
            "status": semester.status,
            "start_date": semester.start_date,
            "end_date": semester.end_date,
            "batch": {
                "id": semester.batch_id,
                "year": semester.batch.year,
                "program_code": semester.batch.program.code,
                "program_name": semester.batch.program.name,
            },
        }
        response.data["summary"] = {
            "students": len(all_rows),
            "with_evidence": len(evidenced),
            "needs_attention": sum(row["needs_attention"] for row in all_rows),
            "average_performance": round(sum(evidenced) / len(evidenced), 2) if evidenced else None,
        }
        return response

    @staticmethod
    def build_student_report(students, student_id, user, permissions=None):
        student = generics.get_object_or_404(students, pk=student_id)
        allocations = {
            allocation.id: allocation
            for allocation in annotated_allocations()
            .prefetch_related("meetings")
            .filter(enrollments__student=student, enrollments__is_archived=False)
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
        permissions = permissions or get_permissions_for_user(user)
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


class ManagementAttendanceReportView(generics.GenericAPIView):
    """Held-class attendance across a bounded management date range."""

    permission_classes = (ManagementAttendanceReportPermission,)

    @extend_schema(operation_id="performance_management_attendance_report", responses=dict)
    def get(self, request):
        start_date = parse_date(request.query_params.get("start_date", ""))
        end_date = parse_date(request.query_params.get("end_date", ""))
        if start_date is None or end_date is None:
            return Response(
                {"date_range": "Provide valid start_date and end_date values."}, status=400
            )
        if start_date > end_date:
            return Response({"end_date": "End date cannot precede start date."}, status=400)
        if end_date > timezone.localdate():
            return Response(
                {"end_date": "Attendance reports cannot include future dates."}, status=400
            )
        if (end_date - start_date).days > 366:
            return Response({"date_range": "Choose a range of 367 days or fewer."}, status=400)

        queryset = AttendanceSession.objects.filter(
            is_archived=False,
            date__range=(start_date, end_date),
            allocation__is_archived=False,
        ).select_related(
            "allocation__subject__program",
            "allocation__batch_semester__batch",
            "allocation__teacher",
        )
        queryset = scope_by_authority(
            queryset,
            request.user,
            department_path="allocation__subject__program__department_id",
            program_path="allocation__subject__program_id",
        )
        for parameter, field in (
            ("program", "allocation__subject__program_id"),
            ("batch", "allocation__batch_semester__batch_id"),
            ("batch_semester", "allocation__batch_semester_id"),
            ("allocation", "allocation_id"),
        ):
            value = request.query_params.get(parameter)
            if value:
                if not value.isdigit():
                    return Response({parameter: "Select a valid value."}, status=400)
                queryset = queryset.filter(**{field: int(value)})

        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(allocation__subject__code__icontains=search)
                | Q(allocation__subject__name__icontains=search)
                | Q(allocation__teacher__first_name__icontains=search)
                | Q(allocation__teacher__last_name__icontains=search)
            )
        queryset = queryset.annotate(
            marked=Count("records", filter=Q(records__is_archived=False), distinct=True),
            present=Count(
                "records",
                filter=Q(
                    records__is_archived=False,
                    records__status=AttendanceStatus.PRESENT.value,
                ),
                distinct=True,
            ),
            absent=Count(
                "records",
                filter=Q(
                    records__is_archived=False,
                    records__status=AttendanceStatus.ABSENT.value,
                ),
                distinct=True,
            ),
            late=Count(
                "records",
                filter=Q(
                    records__is_archived=False,
                    records__status=AttendanceStatus.LATE.value,
                ),
                distinct=True,
            ),
            excused=Count(
                "records",
                filter=Q(
                    records__is_archived=False,
                    records__status=AttendanceStatus.EXCUSED.value,
                ),
                distinct=True,
            ),
        )

        aggregate = queryset.aggregate(
            sessions=Count("id", distinct=True),
            marked=Count("records", filter=Q(records__is_archived=False), distinct=True),
            present=Count(
                "records",
                filter=Q(
                    records__is_archived=False,
                    records__status=AttendanceStatus.PRESENT.value,
                ),
                distinct=True,
            ),
            absent=Count(
                "records",
                filter=Q(
                    records__is_archived=False,
                    records__status=AttendanceStatus.ABSENT.value,
                ),
                distinct=True,
            ),
            late=Count(
                "records",
                filter=Q(
                    records__is_archived=False,
                    records__status=AttendanceStatus.LATE.value,
                ),
                distinct=True,
            ),
            excused=Count(
                "records",
                filter=Q(
                    records__is_archived=False,
                    records__status=AttendanceStatus.EXCUSED.value,
                ),
                distinct=True,
            ),
        )
        ordering = request.query_params.get("ordering", "-date")
        ordering_fields = {
            "-date": ("-date", "-period", "id"),
            "date": ("date", "period", "id"),
            "-absent": ("-absent", "-date", "id"),
            "subject": ("allocation__subject__code", "-date", "id"),
        }
        queryset = queryset.order_by(*ordering_fields.get(ordering, ordering_fields["-date"]))
        page = self.paginate_queryset(queryset)
        results = [self.row_payload(session) for session in page]
        response = self.get_paginated_response(results)
        marked = aggregate["marked"] or 0
        attended = (aggregate["present"] or 0) + (aggregate["late"] or 0)
        response.data["range"] = {"start_date": start_date, "end_date": end_date}
        response.data["summary"] = {
            key: aggregate[key] or 0
            for key in ("sessions", "marked", "present", "absent", "late", "excused")
        }
        response.data["summary"]["attendance_percentage"] = percentage(attended, marked)
        return response

    @staticmethod
    def row_payload(session):
        allocation = session.allocation
        attended = session.present + session.late
        return {
            "id": session.id,
            "date": session.date,
            "period": session.period,
            "allocation": allocation.id,
            "subject_code": allocation.subject.code,
            "subject_name": allocation.subject.name,
            "program_code": allocation.subject.program.code,
            "batch_year": allocation.batch_semester.batch.year,
            "semester": allocation.batch_semester.semester,
            "teacher_name": allocation.teacher.full_name or allocation.teacher.username,
            "marked": session.marked,
            "present": session.present,
            "absent": session.absent,
            "late": session.late,
            "excused": session.excused,
            "attendance_percentage": percentage(attended, session.marked),
        }


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
        allocation = generics.get_object_or_404(
            overview_allocation_queryset(request.user), pk=allocation_id
        )

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
            .order_by(
                "student__first_name",
                "student__middle_name",
                "student__last_name",
                "student_id",
            )
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

        weights = PerformanceWeightConfiguration.current()

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
        allocation = generics.get_object_or_404(
            overview_allocation_queryset(request.user), pk=allocation_id
        )
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
        trends = student_attendance_trends(allocation.id, [enrollment.id])
        return {
            "held": held,
            "present": counts[AttendanceStatus.PRESENT.value],
            "absent": counts[AttendanceStatus.ABSENT.value],
            "excused": counts[AttendanceStatus.EXCUSED.value],
            "late": counts[AttendanceStatus.LATE.value],
            "percentage": percentage(attended, held),
            "trend": trends.get(enrollment.id),
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
        from src.academics.teaching_calendar import TeachingCalendar

        calendar = TeachingCalendar(today, today, allocation_ids)
        todays_allocations = [
            allocation
            for allocation in allocations
            if calendar.day(allocation, today)["is_expected"]
            and meets_on(allocation, today.isoweekday())
        ]

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

        at_risk = self.students_below_threshold(allocations, eligibility_threshold())
        level = management_level(request.user)
        work_queue = self.teacher_work_queue(
            allocations,
            todays_allocations,
            recorded_today,
            set(get_permissions_for_user(request.user)),
            today,
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
                    "classes_total_today": len(todays_allocations),
                },
                "pending_attendance_count": max(len(todays_allocations) - len(recorded_today), 0),
                "today_attendance": self.today_attendance(
                    allocations, todays_allocations, recorded_today, today
                ),
                # Only what actually meets today, so the queue can reach zero.
                "todays_classes": [
                    {
                        **class_payload(allocation),
                        "recorded": allocation.id in recorded_today,
                    }
                    for allocation in todays_allocations
                ],
                "students_needing_attention": at_risk[:10],
                "work_queue": work_queue,
                "recent_activity": self.recent_activity(allocation_ids),
            }
        )

    @staticmethod
    def today_attendance(allocations, todays_allocations, recorded_today, today) -> dict:
        """
        Counts run over every class, so a makeup session held today still counts.
        The review list runs over today's timetable only, so it can empty out.
        """
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
                for allocation in todays_allocations
                if allocation.id not in recorded_today
            ][:10],
        }

    @staticmethod
    def teacher_work_queue(
        allocations, todays_allocations, recorded_today, permissions, today
    ) -> list[dict]:
        """
        Small, actionable queue for active classes; never crosses the caller's scope.

        Attendance is chased only for classes timetabled today — a teacher who
        holds six classes but teaches three on a Tuesday should not be asked for
        the other three. Marking is chased across every class, because an
        unmarked exam is owed whatever day it is.
        """
        if not allocations:
            return []

        allocation_by_id = {allocation.id: allocation for allocation in allocations}
        allocation_ids = list(allocation_by_id)
        rows = []

        if {"add_attendance", "edit_attendance"} & permissions:
            for allocation in todays_allocations:
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

    def students_below_threshold(self, allocations, threshold: float) -> list[dict]:
        """
        Students under the college's attendance requirement, worst first.

        Each carries which way they are moving, because a flat list of names
        below the bar is not a list a teacher can act on: two students at 55%
        need opposite conversations depending on whether they are climbing back
        or still dropping.
        """
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

            below = [
                enrollment
                for enrollment in enrollments
                if percentage(enrollment.attended, allocation.classes_held) < threshold
            ]
            trends = student_attendance_trends(
                allocation.id, [enrollment.id for enrollment in below]
            )

            for enrollment in below:
                trend = trends.get(enrollment.id, {})
                rows.append(
                    {
                        "student_id": enrollment.student_id,
                        "enrollment": enrollment.id,
                        "full_name": enrollment.student.full_name,
                        "roll_number": enrollment.student.roll_number,
                        "subject": allocation.subject.name,
                        "subject_code": allocation.subject.code,
                        "semester": allocation.batch_semester.semester,
                        "attendance_percentage": percentage(
                            enrollment.attended, allocation.classes_held
                        ),
                        "trend": trend,
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
