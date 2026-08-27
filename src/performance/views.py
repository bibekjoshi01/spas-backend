from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

# Project Imports
from src.academics.models import SubjectAllocation
from src.academics.views import BaseAcademicViewSet
from src.base.schemas import MessageResponseSerializer
from src.libs.permissions import AllocationOwnerScopedQuerysetMixin, scope_to_allocation_owner
from src.students.models import SubjectEnrollment

from .constants import AttendanceStatus
from .models import Assignment, AttendanceSession, InternalExam
from .permissions import AssignmentPermission, AttendancePermission, InternalExamPermission
from .serializers import (
    AssignmentCreateSerializer,
    AssignmentListSerializer,
    AssignmentPatchSerializer,
    AssignmentSubmissionBulkSerializer,
    AssignmentSubmissionReadSerializer,
    AttendanceSessionCreateSerializer,
    AttendanceSessionListSerializer,
    AttendanceSessionRetrieveSerializer,
    InternalExamCreateSerializer,
    InternalExamListSerializer,
    InternalExamMarkBulkSerializer,
    InternalExamMarkReadSerializer,
    InternalExamPatchSerializer,
    validate_allocation_is_writable,
)

PRESENT_STATUSES = (AttendanceStatus.PRESENT.value, AttendanceStatus.LATE.value)


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
    serializer_class = AssignmentSubmissionReadSerializer  # documentation only

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="allocation",
                required=True,
                type=int,
                description="The class whose roster is wanted.",
            )
        ],
        responses=AssignmentSubmissionReadSerializer(many=True),
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
            .order_by("student__roll_number")
        )

        return Response(
            [
                {
                    "enrollment": enrollment.id,
                    "student_id": enrollment.student_id,
                    "roll_number": enrollment.student.roll_number,
                    "full_name": enrollment.student.full_name,
                    "is_retake": enrollment.is_retake,
                }
                for enrollment in enrollments
            ]
        )


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
        .annotate(marked_count=Count("marks", filter=Q(marks__is_archived=False), distinct=True))
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

    permission_classes = (InternalExamPermission,)
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
            done_count=Count(
                "submissions",
                filter=Q(submissions__is_archived=False, submissions__status="DONE"),
                distinct=True,
            )
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

    permission_classes = (AssignmentPermission,)
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
