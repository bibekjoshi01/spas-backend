from django.db import models, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

# Project Imports
from src.base.schemas import MessageResponseSerializer
from src.libs.permissions import AllocationOwnerScopedQuerysetMixin
from src.libs.scoping import AuthorityScopedMixin, management_scope
from src.user.models import User

from .models import (
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
)
from .permissions import (
    BatchPermission,
    BatchSemesterPermission,
    DepartmentPermission,
    ProgramPermission,
    SubjectAllocationPermission,
    SubjectPermission,
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
    UserBriefSerializer,
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
        # Archiving must remain possible for legacy rows whose old domain data
        # no longer passes current validation. It changes lifecycle/audit state
        # only, so avoid revalidating unrelated fields through model.save().
        instance.is_archived = True
        instance.is_active = False
        instance.updated_by = request.user
        instance.updated_at = timezone.now()
        # Call Django's base save implementation to retain post-save audit
        # history while intentionally bypassing legacy domain full_clean().
        models.Model.save(
            instance,
            update_fields=(
                "is_archived",
                "is_active",
                "updated_by",
                "updated_at",
            ),
        )
        return Response({"message": self.archive_message})


class DepartmentViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Departments of the college."""

    department_path = "id"
    program_path = None
    permission_classes = (DepartmentPermission,)
    queryset = (
        Department.objects.filter(is_archived=False)
        .select_related("head")
        .annotate(program_count=active_count("programs"))
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


class ProgramViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Degree programs run by a department."""

    department_path = "department_id"
    program_path = "id"
    permission_classes = (ProgramPermission,)
    queryset = Program.objects.filter(is_archived=False).select_related("department", "coordinator")
    list_serializer_class = ProgramListSerializer
    create_serializer_class = ProgramCreateSerializer
    patch_serializer_class = ProgramPatchSerializer
    archive_message = "Program archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("department", "coordinator", "is_active")
    search_fields = ("name", "code")
    ordering = ("name",)
    ordering_fields = ("id", "name", "code")

    @action(detail=False, methods=("get",), url_path="assignment-candidates")
    def assignment_candidates(self, request):
        """Minimal staff identities for HOD/coordinator assignment controls."""
        users = (
            User.objects.filter(is_active=True, is_archived=False)
            .exclude(roles__codename="STUDENT")
            .distinct()
            .order_by("full_name", "username")
        )
        role = request.query_params.get("role")
        if role:
            users = users.filter(roles__codename=role).distinct()
        if not request.user.is_superuser:
            scope = management_scope(request.user)
            users = users.filter(
                Q(allocations__subject__program_id__in=scope.program_ids)
                | Q(headed_departments__id__in=scope.department_ids)
                | Q(coordinated_programs__id__in=scope.program_ids)
            ).distinct()
        data = UserBriefSerializer(users, many=True).data
        return Response({"count": len(data), "next": None, "previous": None, "results": data})


class BatchViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Intake cohorts of a program."""

    department_path = "program__department_id"
    program_path = "program_id"
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


class BatchSemesterViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """
    The semesters a batch has sat, is sitting, or will sit.

    Moving a batch forward is a POST here followed by enrolling its students.
    """

    department_path = "batch__program__department_id"
    program_path = "batch__program_id"
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


class SubjectViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Curriculum subjects, each fixed to the semester it is taught in."""

    department_path = "program__department_id"
    program_path = "program_id"
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


class SubjectAllocationViewSet(
    AuthorityScopedMixin, AllocationOwnerScopedQuerysetMixin, BaseAcademicViewSet
):
    """
    Classes: a subject taught to a batch-semester by a teacher.

    A user who may allocate sees every class; a teacher who may only view sees
    the classes allocated to them.
    """

    department_path = "subject__program__department_id"
    program_path = "subject__program_id"
    permission_classes = (SubjectAllocationPermission,)
    queryset = (
        SubjectAllocation.objects.filter(
            is_archived=False,
            subject__is_archived=False,
            subject__program__is_archived=False,
            subject__program__department__is_archived=False,
            batch_semester__is_archived=False,
            batch_semester__batch__is_archived=False,
            batch_semester__batch__program__is_archived=False,
        )
        .select_related("subject", "teacher", "batch_semester__batch__program")
        .annotate(enrolled_count=active_count("enrollments"))
    )
    owner_scope_path = "teacher"
    # The oversight listing: whoever may allocate sees every class.
    strict_scope = False
    list_serializer_class = SubjectAllocationListSerializer
    create_serializer_class = SubjectAllocationCreateSerializer
    patch_serializer_class = SubjectAllocationPatchSerializer
    archive_message = "Allocation archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = (
        "teacher",
        "subject",
        "subject__program",
        "batch_semester",
        "batch_semester__batch",
        "batch_semester__semester",
        "batch_semester__status",
        "is_active",
    )
    search_fields = ("subject__code", "subject__name")
    ordering = ("batch_semester", "subject__code")
    ordering_fields = ("id",)
