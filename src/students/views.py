from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

# Project Imports
from src.academics.models import Batch
from src.academics.views import BaseAcademicViewSet
from src.libs.imports import ImportPermission, SpreadsheetImportView
from src.libs.permissions import get_permissions_for_user, scope_to_allocation_owner
from src.libs.scoping import AuthorityScopedMixin, has_program_authority, scope_by_authority

from .imports import TEMPLATE_EXAMPLE as STUDENT_TEMPLATE_EXAMPLE
from .imports import StudentImporter
from .models import SemesterEnrollment, Student, SubjectEnrollment
from .permissions import (
    SemesterEnrollmentPermission,
    StudentPermission,
    SubjectEnrollmentPermission,
)
from .serializers import (
    SemesterEnrollmentBulkSerializer,
    SemesterEnrollmentCreateSerializer,
    SemesterEnrollmentListSerializer,
    SemesterEnrollmentPatchSerializer,
    StudentCreateSerializer,
    StudentListSerializer,
    StudentPatchSerializer,
    StudentRetrieveSerializer,
    SubjectEnrollmentBulkSerializer,
    SubjectEnrollmentCreateSerializer,
    SubjectEnrollmentListSerializer,
)


class StudentNameOrderingFilter(OrderingFilter):
    """Expose the computed full name as one stable API ordering field."""

    def get_ordering(self, request, queryset, view):
        requested = request.query_params.get(self.ordering_param)
        if requested in ("full_name", "-full_name"):
            prefix = "-" if requested.startswith("-") else ""
            return (
                f"{prefix}first_name",
                f"{prefix}middle_name",
                f"{prefix}last_name",
                "id",
            )
        return super().get_ordering(request, queryset, view)


class StudentViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Students, addressed by their admission batch."""

    department_path = "batch__program__department_id"
    program_path = "batch__program_id"
    permission_classes = (StudentPermission,)
    queryset = Student.objects.filter(
        is_archived=False,
        batch__is_archived=False,
        batch__program__is_archived=False,
        batch__program__department__is_archived=False,
    ).select_related("batch__program")
    list_serializer_class = StudentListSerializer
    create_serializer_class = StudentCreateSerializer
    patch_serializer_class = StudentPatchSerializer
    archive_message = "Student archived successfully."
    # A student who has left is marked DROPPED_OUT or deactivated; archiving is
    # for records that should not exist, so it waits until the enrollments are
    # gone rather than quietly taking their history out of every listing.
    archive_blockers = ("semester_enrollments", "subject_enrollments")
    filter_backends = (DjangoFilterBackend, SearchFilter, StudentNameOrderingFilter)
    filterset_fields = (
        "batch",
        "batch__program",
        "batch__program__department",
        "status",
        "gender",
        "is_active",
    )
    search_fields = (
        "roll_number",
        "registration_number",
        "first_name",
        "last_name",
        "email",
        "phone_no",
        "alternate_phone_no",
    )
    ordering = ("first_name", "middle_name", "last_name", "id")
    ordering_fields = (
        "id",
        "roll_number",
        "full_name",
        "first_name",
        "middle_name",
        "last_name",
    )

    def get_serializer_class(self):
        if self.request.method in ("GET", "HEAD") and self.action != "list":
            return StudentRetrieveSerializer
        return super().get_serializer_class()

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        student = self.get_object()
        student.user.is_active = False
        student.user.updated_by = request.user
        student.user.save(update_fields=["is_active", "updated_by", "updated_at"])
        return super().destroy(request, *args, **kwargs)


class SemesterEnrollmentViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Which semester each student is sitting. Promotion writes rows here."""

    department_path = "batch_semester__batch__program__department_id"
    program_path = "batch_semester__batch__program_id"
    permission_classes = (SemesterEnrollmentPermission,)
    queryset = SemesterEnrollment.objects.filter(
        is_archived=False,
        student__is_archived=False,
        batch_semester__is_archived=False,
        batch_semester__batch__is_archived=False,
        batch_semester__batch__program__is_archived=False,
    ).select_related("student", "batch_semester__batch__program")
    list_serializer_class = SemesterEnrollmentListSerializer
    create_serializer_class = SemesterEnrollmentCreateSerializer
    patch_serializer_class = SemesterEnrollmentPatchSerializer
    archive_message = "Enrollment archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = (
        "student",
        "batch_semester",
        "batch_semester__batch",
        "batch_semester__semester",
        "status",
        "is_active",
    )
    search_fields = ("student__roll_number", "student__first_name", "student__last_name")
    ordering = (
        "student__first_name",
        "student__middle_name",
        "student__last_name",
        "id",
    )
    ordering_fields = ("id", "student__first_name", "student__last_name")


class SemesterEnrollmentBulkView(generics.CreateAPIView):
    """Promote a group of students into one semester."""

    permission_classes = (SemesterEnrollmentPermission,)
    serializer_class = SemesterEnrollmentBulkSerializer

    @extend_schema(responses=SemesterEnrollmentBulkSerializer)
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SubjectEnrollmentViewSet(BaseAcademicViewSet):
    """
    Class rosters.

    A teacher who may only view sees the rosters of their own classes.
    """

    permission_classes = (SubjectEnrollmentPermission,)
    queryset = SubjectEnrollment.objects.filter(
        is_archived=False,
        student__is_archived=False,
        allocation__is_archived=False,
        allocation__subject__is_archived=False,
        allocation__batch_semester__is_archived=False,
    ).select_related("student", "allocation__subject")
    list_serializer_class = SubjectEnrollmentListSerializer
    create_serializer_class = SubjectEnrollmentCreateSerializer
    patch_serializer_class = SubjectEnrollmentCreateSerializer
    archive_message = "Registration removed successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("allocation", "student", "is_retake", "is_active")
    search_fields = ("student__roll_number", "student__first_name", "student__last_name")
    ordering = (
        "student__first_name",
        "student__middle_name",
        "student__last_name",
        "id",
    )
    ordering_fields = ("id", "student__first_name", "student__last_name")

    def get_queryset(self):
        queryset = super().get_queryset()
        user = getattr(self.request, "user", None)

        if user and (
            user.is_superuser or "add_subject_allocation" in get_permissions_for_user(user)
        ):
            return scope_by_authority(
                queryset,
                user,
                department_path="allocation__subject__program__department_id",
                program_path="allocation__subject__program_id",
            )

        return scope_to_allocation_owner(queryset, user, path="allocation__teacher")


class SubjectEnrollmentBulkView(generics.CreateAPIView):
    """Register a group of students onto one class."""

    permission_classes = (SubjectEnrollmentPermission,)
    serializer_class = SubjectEnrollmentBulkSerializer

    @extend_schema(responses=SubjectEnrollmentBulkSerializer)
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class StudentImportPermission(ImportPermission):
    resource = "student"


class StudentImportView(SpreadsheetImportView):
    """Bulk student intake for one batch, from a CSV or Excel sheet."""

    permission_classes = (StudentImportPermission,)
    importer_class = StudentImporter
    template_example = STUDENT_TEMPLATE_EXAMPLE
    template_filename = "student-import-template.csv"

    def build_importer(self, request):
        raw = request.data.get("batch")
        try:
            batch_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({"batch": "Choose the batch these students belong to."}) from None

        batch = Batch.objects.filter(pk=batch_id, is_archived=False).first()
        # A batch outside the caller's authority is answered as absent rather
        # than forbidden, so an import cannot be used to enumerate the college.
        if batch is None or not has_program_authority(request.user, batch.program_id):
            raise NotFound("No such batch.")

        return StudentImporter({"request": request}, batch)
