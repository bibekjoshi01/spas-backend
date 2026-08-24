from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

# Project Imports
from src.academics.views import BaseAcademicViewSet
from src.libs.permissions import TeacherScopedQuerysetMixin
from src.libs.scoping import AuthorityScopedMixin

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


class StudentViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Students, addressed by their admission batch."""

    department_path = "batch__program__department_id"
    program_path = "batch__program_id"
    permission_classes = (StudentPermission,)
    queryset = Student.objects.filter(is_archived=False).select_related("batch__program")
    list_serializer_class = StudentListSerializer
    create_serializer_class = StudentCreateSerializer
    patch_serializer_class = StudentPatchSerializer
    archive_message = "Student archived successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
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
    )
    ordering = ("batch", "roll_number")
    ordering_fields = ("id", "roll_number", "first_name")

    def get_serializer_class(self):
        if self.request.method in ("GET", "HEAD") and self.action != "list":
            return StudentRetrieveSerializer
        return super().get_serializer_class()


class SemesterEnrollmentViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Which semester each student is sitting. Promotion writes rows here."""

    department_path = "batch_semester__batch__program__department_id"
    program_path = "batch_semester__batch__program_id"
    permission_classes = (SemesterEnrollmentPermission,)
    queryset = SemesterEnrollment.objects.filter(is_archived=False).select_related(
        "student", "batch_semester__batch__program"
    )
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
    ordering = ("student",)
    ordering_fields = ("id",)


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


class SubjectEnrollmentViewSet(TeacherScopedQuerysetMixin, BaseAcademicViewSet):
    """
    Class rosters.

    A teacher who may only view sees the rosters of their own classes.
    """

    permission_classes = (SubjectEnrollmentPermission,)
    queryset = SubjectEnrollment.objects.filter(is_archived=False).select_related(
        "student", "allocation__subject"
    )
    teacher_scope_path = "allocation__teacher__user"
    list_serializer_class = SubjectEnrollmentListSerializer
    create_serializer_class = SubjectEnrollmentCreateSerializer
    patch_serializer_class = SubjectEnrollmentCreateSerializer
    archive_message = "Registration removed successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("allocation", "student", "is_retake", "is_active")
    search_fields = ("student__roll_number", "student__first_name", "student__last_name")
    ordering = ("student",)
    ordering_fields = ("id",)


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
