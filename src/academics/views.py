from django.db import transaction
from django.db.models import Count, Q
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

# Project Imports
from src.base.schemas import MessageResponseSerializer
from src.libs.permissions import TeacherScopedQuerysetMixin

from .models import (
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
    Teacher,
)
from .permissions import (
    BatchPermission,
    BatchSemesterPermission,
    DepartmentPermission,
    ProgramPermission,
    SubjectAllocationPermission,
    SubjectPermission,
    TeacherPermission,
)
from .serializers import (
    BatchCreateSerializer,
    BatchListSerializer,
    BatchPatchSerializer,
    BatchSemesterCreateSerializer,
    BatchSemesterListSerializer,
    BatchSemesterPatchSerializer,
    DepartmentCreateSerializer,
    DepartmentListSerializer,
    DepartmentPatchSerializer,
    ProgramCreateSerializer,
    ProgramListSerializer,
    ProgramPatchSerializer,
    SubjectAllocationCreateSerializer,
    SubjectAllocationListSerializer,
    SubjectAllocationPatchSerializer,
    SubjectCreateSerializer,
    SubjectListSerializer,
    SubjectPatchSerializer,
    TeacherCreateSerializer,
    TeacherListSerializer,
    TeacherPatchSerializer,
)


def active_count(relation: str) -> Count:
    """Count only the rows of `relation` that are not archived."""
    return Count(relation, filter=Q(**{f"{relation}__is_archived": False}), distinct=True)


class BaseAcademicViewSet(ModelViewSet):
    """
    Shared behaviour for every resource in this module.

    Writes are transactional, delete archives rather than removes, and the
    serializer is chosen by method so each action has exactly the fields it
    needs.
    """

    http_method_names: tuple[str, ...] = (
        "get",
        "head",
        "post",
        "patch",
        "options",
        "delete",
    )
    list_serializer_class: type | None = None
    create_serializer_class: type | None = None
    patch_serializer_class: type | None = None
    archive_message = "Record archived successfully."

    def get_serializer_class(self):
        if self.request.method == "POST":
            return self.create_serializer_class
        if self.request.method == "PATCH":
            return self.patch_serializer_class
        return self.list_serializer_class

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(request=None, responses=MessageResponseSerializer)
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_archived = True
        instance.updated_by = request.user
        instance.save()
        return Response({"message": self.archive_message})


class DepartmentViewSet(BaseAcademicViewSet):
    """Departments of the college."""

    permission_classes = (DepartmentPermission,)
    queryset = Department.objects.filter(is_archived=False).annotate(
        program_count=active_count("programs")
    )
    list_serializer_class = DepartmentListSerializer
    create_serializer_class = DepartmentCreateSerializer
    patch_serializer_class = DepartmentPatchSerializer
    archive_message = "Department archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("name",)
    ordering_fields = ("id", "name", "code")


class TeacherViewSet(BaseAcademicViewSet):
    """Teaching staff and the department they belong to."""

    permission_classes = (TeacherPermission,)
    queryset = Teacher.objects.filter(is_archived=False).select_related("user", "department")
    list_serializer_class = TeacherListSerializer
    create_serializer_class = TeacherCreateSerializer
    patch_serializer_class = TeacherPatchSerializer
    archive_message = "Teacher archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("department", "designation", "is_active")
    search_fields = ("user__full_name", "user__username", "user__email", "employee_code")
    ordering = ("user__first_name",)
    ordering_fields = ("id", "employee_code")


class ProgramViewSet(BaseAcademicViewSet):
    """Degree programs run by a department."""

    permission_classes = (ProgramPermission,)
    queryset = Program.objects.filter(is_archived=False).select_related(
        "department", "coordinator__user"
    )
    list_serializer_class = ProgramListSerializer
    create_serializer_class = ProgramCreateSerializer
    patch_serializer_class = ProgramPatchSerializer
    archive_message = "Program archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("department", "coordinator", "is_active")
    search_fields = ("name", "code")
    ordering = ("name",)
    ordering_fields = ("id", "name", "code")


class BatchViewSet(BaseAcademicViewSet):
    """Intake cohorts of a program."""

    permission_classes = (BatchPermission,)
    queryset = (
        Batch.objects.filter(is_archived=False)
        .select_related("program")
        .annotate(student_count=active_count("students"))
    )
    list_serializer_class = BatchListSerializer
    create_serializer_class = BatchCreateSerializer
    patch_serializer_class = BatchPatchSerializer
    archive_message = "Batch archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("program", "program__department", "year", "is_active")
    search_fields = ("program__name", "program__code")
    ordering = ("-year",)
    ordering_fields = ("id", "year")


class BatchSemesterViewSet(BaseAcademicViewSet):
    """
    The semesters a batch has sat, is sitting, or will sit.

    Moving a batch forward is a POST here followed by enrolling its students.
    """

    permission_classes = (BatchSemesterPermission,)
    queryset = BatchSemester.objects.filter(is_archived=False).select_related("batch__program")
    list_serializer_class = BatchSemesterListSerializer
    create_serializer_class = BatchSemesterCreateSerializer
    patch_serializer_class = BatchSemesterPatchSerializer
    archive_message = "Semester archived successfully."
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ("batch", "batch__program", "semester", "status", "is_active")
    ordering = ("batch", "semester")
    ordering_fields = ("id", "semester", "start_date")


class SubjectViewSet(BaseAcademicViewSet):
    """Curriculum subjects, each fixed to the semester it is taught in."""

    permission_classes = (SubjectPermission,)
    queryset = Subject.objects.filter(is_archived=False).select_related("program")
    list_serializer_class = SubjectListSerializer
    create_serializer_class = SubjectCreateSerializer
    patch_serializer_class = SubjectPatchSerializer
    archive_message = "Subject archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("program", "semester", "is_elective", "is_active")
    search_fields = ("code", "name")
    ordering = ("program", "semester", "code")
    ordering_fields = ("id", "code", "semester")


class SubjectAllocationViewSet(TeacherScopedQuerysetMixin, BaseAcademicViewSet):
    """
    Classes: a subject taught to a batch-semester by a teacher.

    A user who may allocate sees every class; a teacher who may only view sees
    the classes allocated to them.
    """

    permission_classes = (SubjectAllocationPermission,)
    queryset = (
        SubjectAllocation.objects.filter(is_archived=False)
        .select_related("subject", "teacher__user", "batch_semester__batch__program")
        .annotate(enrolled_count=active_count("enrollments"))
    )
    teacher_scope_path = "teacher__user"
    manage_permission = "add_subject_allocation"
    list_serializer_class = SubjectAllocationListSerializer
    create_serializer_class = SubjectAllocationCreateSerializer
    patch_serializer_class = SubjectAllocationPatchSerializer
    archive_message = "Allocation archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = (
        "teacher",
        "subject",
        "batch_semester",
        "batch_semester__batch",
        "batch_semester__status",
        "is_active",
    )
    search_fields = ("subject__code", "subject__name")
    ordering = ("batch_semester", "subject__code")
    ordering_fields = ("id",)
