from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BatchSemesterViewSet,
    BatchViewSet,
    DepartmentViewSet,
    ProgramViewSet,
    SubjectAllocationViewSet,
    SubjectImportView,
    SubjectViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register("departments", DepartmentViewSet, basename="department")
router.register("programs", ProgramViewSet, basename="program")
router.register("batches", BatchViewSet, basename="batch")
router.register("batch-semesters", BatchSemesterViewSet, basename="batch-semester")
router.register("subjects", SubjectViewSet, basename="subject")
router.register("allocations", SubjectAllocationViewSet, basename="subject-allocation")

urlpatterns = [
    path("subjects/import", SubjectImportView.as_view(), name="subject-import"),
    path("", include(router.urls)),
]
