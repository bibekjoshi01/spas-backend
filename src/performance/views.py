from django.db import transaction
from django.db.models import Avg, Count, F, Q
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response

# Project Imports
from src.academics.models import SubjectAllocation
from src.academics.views import BaseAcademicViewSet
from src.base.schemas import MessageResponseSerializer
from src.libs.permissions import AllocationOwnerScopedQuerysetMixin, scope_to_allocation_owner
from src.students.models import SubjectEnrollment

from .constants import AttendanceStatus
from .models import (
    Assignment,
    AssignmentSubmission,
    AttendanceSession,
    ClassPerformanceRating,
    InternalExam,
    InternalExamMark,
    PerformanceWeightConfiguration,
)
from .permissions import (
    AssignmentPermission,
    AssignmentSubmissionPermission,
    AttendancePermission,
    ClassPerformancePermission,
    InternalExamMarkPermission,
    InternalExamPermission,
)
from .serializers import (
    AssignmentCreateSerializer,
    AssignmentListSerializer,
    AssignmentPatchSerializer,
    AssignmentSubmissionBulkSerializer,
    AssignmentSubmissionReadSerializer,
    AttendanceSessionCreateSerializer,
    AttendanceSessionListSerializer,
    AttendanceSessionRetrieveSerializer,
    ClassPerformanceBulkSerializer,
    InternalExamCreateSerializer,
    InternalExamListSerializer,
    InternalExamMarkBulkSerializer,
    InternalExamMarkReadSerializer,
    InternalExamPatchSerializer,
    PerformanceWeightConfigurationSerializer,
    RosterEntryReadSerializer,
    validate_allocation_is_writable,
)

PRESENT_STATUSES = (AttendanceStatus.PRESENT.value, AttendanceStatus.LATE.value)


class SuperuserOnly(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_active and request.user.is_superuser)


class ReadPolicyWriteSuperuser(BasePermission):
    """
    Any signed-in member of the college may read the performance policy; only a
    superuser may change it.

    The attendance requirement is printed on rosters, reports, eligibility
    badges and the attention queue, so every screen has to know the figure.
    Setting it is administration.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.is_active):
            return False
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user.is_superuser)


class PerformanceWeightConfigurationView(generics.GenericAPIView):
    """Read and update the single tenant-scoped performance policy."""

    permission_classes = (ReadPolicyWriteSuperuser,)
    serializer_class = PerformanceWeightConfigurationSerializer

    def get_object(self, request):
        configuration, _ = PerformanceWeightConfiguration.objects.get_or_create(
            singleton_key=True,
            defaults={"created_by": request.user},
        )
        return configuration

    def get(self, request):
        # A read must not create an audited row, so this does not get_or_create.
        return Response(
            PerformanceWeightConfigurationSerializer(PerformanceWeightConfiguration.current()).data
        )

    def put(self, request):
        configuration = self.get_object(request)
        serializer = PerformanceWeightConfigurationSerializer(
            configuration, data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)


class RunningSemesterMutationMixin:
    """Allow historical reads while rejecting updates and archives."""

    def _validate_instance_semester(self):
        instance = self.get_object()
        validate_allocation_is_writable(instance.allocation)

    def partial_update(self, request, *args, **kwargs):
        self._validate_instance_semester()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._validate_instance_semester()
        return super().destroy(request, *args, **kwargs)


class RosterView(generics.GenericAPIView):
    """
    The students a teacher is about to mark, in roll-number order.

    Every marking screen — attendance, exam marks, assignment status — opens
    with this call, so the client never has to assemble a roster itself.
    """

    permission_classes = (AttendancePermission,)
    queryset = SubjectEnrollment.objects.none()
    serializer_class = RosterEntryReadSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="allocation",
                required=True,
                type=int,
                description="The class whose roster is wanted.",
            )
        ],
        responses=RosterEntryReadSerializer(many=True),
    )
    def get(self, request):
        allocation_id = request.query_params.get("allocation")

        if not allocation_id:
            return Response(
                {"allocation": "This query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        allocation = get_object_or_404(
            scope_to_allocation_owner(
                SubjectAllocation.objects.filter(is_archived=False),
                request.user,
                path="teacher",
            ),
            pk=allocation_id,
        )

        enrollments = (
            SubjectEnrollment.objects.filter(allocation=allocation, is_archived=False)
            .select_related("student")
            .order_by(
                "student__first_name",
                "student__middle_name",
                "student__last_name",
                "student_id",
            )
        )

        return Response(RosterEntryReadSerializer(enrollments, many=True).data)


class AttendanceSessionViewSet(
    RunningSemesterMutationMixin, AllocationOwnerScopedQuerysetMixin, BaseAcademicViewSet
):
    """Classes held, and the roster's attendance for each."""

    permission_classes = (AttendancePermission,)
    queryset = (
        AttendanceSession.objects.filter(is_archived=False)
        .select_related("allocation__subject")
        .annotate(
            marked_count=Count("records", filter=Q(records__is_archived=False), distinct=True),
            present_count=Count(
                "records",
                filter=Q(records__is_archived=False, records__status__in=PRESENT_STATUSES),
                distinct=True,
            ),
        )
    )
    owner_scope_path = "allocation__teacher"
    list_serializer_class = AttendanceSessionListSerializer
    create_serializer_class = AttendanceSessionCreateSerializer
    patch_serializer_class = AttendanceSessionCreateSerializer
    archive_message = "Attendance session removed successfully."
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ("allocation", "date")
    ordering = ("-date",)
    ordering_fields = ("id", "date")
    http_method_names = ("get", "head", "post", "options", "delete")

    def get_serializer_class(self):
        if self.request.method in ("GET", "HEAD") and self.action != "list":
            return AttendanceSessionRetrieveSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != "list":
            return queryset.prefetch_related("records__enrollment__student")
        return queryset


class InternalExamViewSet(
    RunningSemesterMutationMixin, AllocationOwnerScopedQuerysetMixin, BaseAcademicViewSet
):
    """Internal assessments set on a class."""

    permission_classes = (InternalExamPermission,)
    queryset = (
        InternalExam.objects.filter(is_archived=False)
        .select_related("allocation__subject")
        .annotate(
            marked_count=Count("marks", filter=Q(marks__is_archived=False), distinct=True),
            absent_count=Count(
                "marks",
                filter=Q(marks__is_archived=False, marks__is_absent=True),
                distinct=True,
            ),
            passed_count=Count(
                "marks",
                filter=Q(
                    marks__is_archived=False,
                    marks__is_absent=False,
                    marks__marks_obtained__gte=F("pass_marks"),
                ),
                distinct=True,
            ),
            average_marks=Avg(
                "marks__marks_obtained",
                filter=Q(marks__is_archived=False, marks__is_absent=False),
            ),
        )
    )
    owner_scope_path = "allocation__teacher"
    list_serializer_class = InternalExamListSerializer
    create_serializer_class = InternalExamCreateSerializer
    patch_serializer_class = InternalExamPatchSerializer
    archive_message = "Exam archived successfully."
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ("allocation", "exam_type", "is_active")
    ordering = ("-exam_date",)
    ordering_fields = ("id", "exam_date")


class InternalExamMarkView(generics.GenericAPIView):
    """Read or write every mark for one exam."""

    permission_classes = (InternalExamMarkPermission,)
    queryset = InternalExamMark.objects.none()
    serializer_class = InternalExamMarkBulkSerializer

    def get_exam(self):
        queryset = scope_to_allocation_owner(
            InternalExam.objects.filter(is_archived=False).select_related(
                "allocation__batch_semester"
            ),
            self.request.user,
        )
        return get_object_or_404(queryset, pk=self.kwargs["exam_id"])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["exam"] = self.get_exam()
        return context

    @extend_schema(responses=InternalExamMarkReadSerializer(many=True))
    def get(self, request, exam_id):
        exam = self.get_exam()
        marks = exam.marks.filter(is_archived=False).select_related("enrollment__student")
        return Response(InternalExamMarkReadSerializer(marks, many=True).data)

    @extend_schema(responses=MessageResponseSerializer)
    @transaction.atomic
    def post(self, request, exam_id):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AssignmentViewSet(
    RunningSemesterMutationMixin, AllocationOwnerScopedQuerysetMixin, BaseAcademicViewSet
):
    """Assignments given to a class."""

    permission_classes = (AssignmentPermission,)
    queryset = (
        Assignment.objects.filter(is_archived=False)
        .select_related("allocation__subject")
        .annotate(
            evaluated_count=Count(
                "submissions",
                filter=Q(submissions__is_archived=False),
                distinct=True,
            ),
            done_count=Count(
                "submissions",
                filter=Q(submissions__is_archived=False, submissions__status="DONE"),
                distinct=True,
            ),
        )
    )
    owner_scope_path = "allocation__teacher"
    list_serializer_class = AssignmentListSerializer
    create_serializer_class = AssignmentCreateSerializer
    patch_serializer_class = AssignmentPatchSerializer
    archive_message = "Assignment archived successfully."
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ("allocation", "is_active")
    ordering = ("-assigned_date",)
    ordering_fields = ("id", "assigned_date", "due_date")


class AssignmentSubmissionView(generics.GenericAPIView):
    """Read or write every submission status for one assignment."""

    permission_classes = (AssignmentSubmissionPermission,)
    queryset = AssignmentSubmission.objects.none()
    serializer_class = AssignmentSubmissionBulkSerializer

    def get_assignment(self):
        queryset = scope_to_allocation_owner(
            Assignment.objects.filter(is_archived=False).select_related(
                "allocation__batch_semester"
            ),
            self.request.user,
        )
        return get_object_or_404(queryset, pk=self.kwargs["assignment_id"])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["assignment"] = self.get_assignment()
        return context

    @extend_schema(responses=AssignmentSubmissionReadSerializer(many=True))
    def get(self, request, assignment_id):
        assignment = self.get_assignment()
        submissions = assignment.submissions.filter(is_archived=False).select_related(
            "enrollment__student"
        )
        return Response(AssignmentSubmissionReadSerializer(submissions, many=True).data)

    @extend_schema(responses=MessageResponseSerializer)
    @transaction.atomic
    def post(self, request, assignment_id):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ClassPerformanceView(generics.GenericAPIView):
    """Read the full class roster and save the teacher's current 1-10 ratings."""

    permission_classes = (ClassPerformancePermission,)
    serializer_class = ClassPerformanceBulkSerializer

    def get_allocation(self):
        allocation_id = self.request.query_params.get("allocation")
        if not allocation_id:
            return None
        return get_object_or_404(
            scope_to_allocation_owner(
                SubjectAllocation.objects.filter(is_archived=False).select_related(
                    "batch_semester"
                ),
                self.request.user,
                path="teacher",
            ),
            pk=allocation_id,
        )

    @extend_schema(
        parameters=[OpenApiParameter(name="allocation", required=True, type=int)],
        responses=dict,
    )
    def get(self, request):
        allocation = self.get_allocation()
        if allocation is None:
            return Response(
                {"allocation": "This query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ratings = {
            rating.enrollment_id: rating
            for rating in ClassPerformanceRating.objects.filter(
                enrollment__allocation=allocation, is_archived=False
            )
        }
        enrollments = (
            SubjectEnrollment.objects.filter(allocation=allocation, is_archived=False)
            .select_related("student")
            .order_by(
                "student__first_name",
                "student__middle_name",
                "student__last_name",
                "student_id",
            )
        )
        return Response(
            [
                {
                    "enrollment": enrollment.id,
                    "student_id": enrollment.student_id,
                    "roll_number": enrollment.student.roll_number,
                    "full_name": enrollment.student.full_name,
                    "score": ratings[enrollment.id].score if enrollment.id in ratings else None,
                    "remarks": ratings[enrollment.id].remarks if enrollment.id in ratings else "",
                }
                for enrollment in enrollments
            ]
        )

    @extend_schema(responses=MessageResponseSerializer)
    @transaction.atomic
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)
