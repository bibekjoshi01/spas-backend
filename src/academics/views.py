import datetime

from django.db import models, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from simple_history.utils import bulk_update_with_history

# Project Imports
from src.base.schemas import MessageResponseSerializer
from src.libs.imports import ImportPermission, SpreadsheetImportView
from src.libs.permissions import (
    AllocationOwnerScopedQuerysetMixin,
    get_role_permissions,
    is_staff_account,
)
from src.libs.scoping import AuthorityScopedMixin, has_program_authority, management_scope
from src.students.constants import StudentStatus
from src.students.models import Student
from src.students.permissions import StudentPortalPermission
from src.user.models import User

from . import calendar as academic_calendar
from .constants import BatchStatus, CalendarSystem, SemesterStatus
from .filters import AcademicCalendarEntryFilter
from .imports import TEMPLATE_EXAMPLE as SUBJECT_TEMPLATE_EXAMPLE
from .imports import SubjectImporter
from .models import (
    AcademicCalendarConfiguration,
    AcademicCalendarEntry,
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
)
from .permissions import (
    AcademicCalendarEntryPermission,
    BatchPermission,
    BatchSemesterPermission,
    DepartmentPermission,
    ProgramPermission,
    SubjectAllocationPermission,
    SubjectPermission,
)
from .serializers import (
    AcademicCalendarConfigurationSerializer,
    AcademicCalendarEntryCreateSerializer,
    AcademicCalendarEntryListSerializer,
    AcademicCalendarEntryPatchSerializer,
    BatchCreateSerializer,
    BatchGraduationPreviewSerializer,
    BatchListSerializer,
    BatchPatchSerializer,
    BatchSemesterCreateSerializer,
    BatchSemesterListSerializer,
    BatchSemesterPatchSerializer,
    CalendarYearSerializer,
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


def archive_blockers(instance, accessors: tuple[str, ...]) -> tuple[list[str], int]:
    """
    The live children standing between `instance` and being archived.

    Each phrase is already written for the reader ("2 subjects"), taken from the
    child model's own verbose name so the message never drifts from the schema.
    The total comes back too, because one blocking row reads "it depends" and
    two read "they depend".
    """
    blocked, total = [], 0
    for accessor in accessors:
        manager = getattr(instance, accessor)
        count = manager.filter(is_archived=False).count()
        if count:
            meta = manager.model._meta
            noun = meta.verbose_name if count == 1 else meta.verbose_name_plural
            blocked.append(f"{count} {noun}")
            total += count
    return blocked, total


def joined(parts: list[str]) -> str:
    """ "a", "a and b", "a, b and c" — the blockers as one readable phrase."""
    if len(parts) <= 2:
        return " and ".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


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
    #: Child relations that must hold no live rows before this one is archived.
    #: Archiving is how a row is removed, so it is only offered at the leaf:
    #: cascading it would retire years of structure from a single click, and
    #: un-archiving could not tell which children were already archived before.
    archive_blockers: tuple[str, ...] = ()

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
        blocked, total = archive_blockers(instance, self.archive_blockers)
        if blocked:
            noun = instance._meta.verbose_name
            verb = "depends" if total == 1 else "depend"
            them = "it" if total == 1 else "them"
            raise ValidationError(
                f"{joined(blocked)} still {verb} on this {noun}. Archive {them} first, "
                f"or deactivate the {noun} to retire it without touching {them}."
            )
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
    archive_blockers = ("programs",)
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
    queryset = Program.objects.filter(
        is_archived=False, department__is_archived=False
    ).select_related("department", "coordinator")
    list_serializer_class = ProgramListSerializer
    create_serializer_class = ProgramCreateSerializer
    patch_serializer_class = ProgramPatchSerializer
    archive_message = "Program archived successfully."
    archive_blockers = ("subjects", "batches")
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


class BatchGraduationMixin:
    """
    Graduating a cohort: one named action, previewed before it is taken.

    This is the one place the system writes a status across hundreds of rows at
    once, so it is deliberately not a field on a form. The preview says exactly
    what will change, the write is transactional, and it can be undone — which
    together make a bulk change a reviewable one rather than a leap.
    """

    def _graduation_facts(self, batch) -> dict:
        semesters = batch.semesters.filter(is_archived=False)
        total = semesters.count()
        completed = semesters.filter(status=SemesterStatus.COMPLETED.value).count()

        students = batch.students.filter(is_archived=False)
        studying = students.filter(status=StudentStatus.STUDYING.value).count()

        blocker = None
        if batch.status == BatchStatus.GRADUATED.value:
            blocker = "This batch has already graduated."
        elif not total:
            blocker = "This batch has no semesters yet."
        elif completed < total:
            running = total - completed
            blocker = (
                f"{running} semester{'s' if running != 1 else ''} "
                f"{'are' if running != 1 else 'is'} still open. "
                "Mark every semester completed first."
            )

        return {
            "batch": str(batch),
            "semesters_total": total,
            "semesters_completed": completed,
            "can_graduate": blocker is None,
            "blocker": blocker,
            "students_total": students.count(),
            "students_to_graduate": studying,
            "students_already_left": students.count() - studying,
        }

    @staticmethod
    def _graduate_students(queryset, actor, *, undo):
        students = list(queryset)
        now = timezone.now()
        for student in students:
            student.status = StudentStatus.STUDYING.value if undo else StudentStatus.GRADUATED.value
            student.graduated_by_batch = not undo
            student.updated_by = actor
            student.updated_at = now
        bulk_update_with_history(
            students,
            Student,
            ["status", "graduated_by_batch", "updated_by", "updated_at"],
            default_user=actor,
            default_date=now,
            default_change_reason="Batch graduation reversed" if undo else "Batch graduation",
        )
        return len(students)

    @extend_schema(responses=BatchGraduationPreviewSerializer)
    @action(detail=True, methods=["get"], url_path="graduation-preview")
    def graduation_preview(self, request, pk=None):
        return Response(self._graduation_facts(self.get_object()))

    @extend_schema(request=None, responses=MessageResponseSerializer)
    @action(detail=True, methods=["post"], url_path="graduate")
    @transaction.atomic
    def graduate(self, request, pk=None):
        batch = self.get_object()
        batch = Batch.objects.select_for_update().get(pk=batch.pk)
        facts = self._graduation_facts(batch)
        if not facts["can_graduate"]:
            raise ValidationError({"detail": facts["blocker"]})

        # Only the students still studying are promoted. Anyone who dropped out
        # or transferred left before the cohort finished, and recording them as
        # graduates would be a lie the college would have to explain later.
        graduated = self._graduate_students(
            batch.students.select_for_update().filter(
                is_archived=False, status=StudentStatus.STUDYING.value
            ),
            request.user,
            undo=False,
        )
        batch.status = BatchStatus.GRADUATED.value
        batch.graduated_on = timezone.localdate()
        batch.updated_by = request.user
        batch.updated_at = timezone.now()
        models.Model.save(
            batch,
            update_fields=("status", "graduated_on", "updated_by", "updated_at"),
        )

        return Response(
            {
                "message": (
                    f"{batch} graduated. "
                    f"{graduated} student{'s' if graduated != 1 else ''} marked graduated."
                )
            }
        )

    @extend_schema(request=None, responses=MessageResponseSerializer)
    @action(detail=True, methods=["post"], url_path="undo-graduation")
    @transaction.atomic
    def undo_graduation(self, request, pk=None):
        """
        Puts a cohort back, for the graduation entered against the wrong batch.

        Only students this system marked graduated are returned to studying;
        one recorded as graduated before the cohort was is left alone, since
        nothing here knows it was this action that set them.
        """
        batch = self.get_object()
        batch = Batch.objects.select_for_update().get(pk=batch.pk)
        if batch.status != BatchStatus.GRADUATED.value:
            raise ValidationError({"detail": "This batch has not graduated."})

        restored = self._graduate_students(
            batch.students.select_for_update().filter(
                is_archived=False,
                status=StudentStatus.GRADUATED.value,
                graduated_by_batch=True,
            ),
            request.user,
            undo=True,
        )
        batch.status = BatchStatus.RUNNING.value
        batch.graduated_on = None
        batch.updated_by = request.user
        batch.updated_at = timezone.now()
        models.Model.save(
            batch,
            update_fields=("status", "graduated_on", "updated_by", "updated_at"),
        )
        return Response({"message": f"{batch} reopened. {restored} students returned to studying."})


class BatchViewSet(AuthorityScopedMixin, BatchGraduationMixin, BaseAcademicViewSet):
    """Intake cohorts of a program."""

    department_path = "program__department_id"
    program_path = "program_id"
    permission_classes = (BatchPermission,)
    queryset = (
        Batch.objects.filter(
            is_archived=False,
            program__is_archived=False,
            program__department__is_archived=False,
        )
        .select_related("program")
        .annotate(student_count=active_count("students"))
    )
    list_serializer_class = BatchListSerializer
    create_serializer_class = BatchCreateSerializer
    patch_serializer_class = BatchPatchSerializer
    archive_message = "Batch archived successfully."
    archive_blockers = ("students", "semesters")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("program", "program__department", "year", "status", "is_active")
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
    queryset = BatchSemester.objects.filter(
        is_archived=False,
        batch__is_archived=False,
        batch__program__is_archived=False,
        batch__program__department__is_archived=False,
    ).select_related("batch__program")
    list_serializer_class = BatchSemesterListSerializer
    create_serializer_class = BatchSemesterCreateSerializer
    patch_serializer_class = BatchSemesterPatchSerializer
    archive_message = "Semester archived successfully."
    archive_blockers = ("allocations", "enrollments")
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ("batch", "batch__program", "semester", "status", "is_active")
    ordering = ("batch", "semester")
    ordering_fields = ("id", "semester", "start_date")


class SubjectViewSet(AuthorityScopedMixin, BaseAcademicViewSet):
    """Curriculum subjects, each fixed to the semester it is taught in."""

    department_path = "program__department_id"
    program_path = "program_id"
    permission_classes = (SubjectPermission,)
    queryset = Subject.objects.filter(
        is_archived=False,
        program__is_archived=False,
        program__department__is_archived=False,
    ).select_related("program")
    list_serializer_class = SubjectListSerializer
    create_serializer_class = SubjectCreateSerializer
    patch_serializer_class = SubjectPatchSerializer
    archive_message = "Subject archived successfully."
    archive_blockers = ("allocations",)
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
    archive_blockers = (
        "enrollments",
        "attendance_sessions",
        "internal_exams",
        "assignments",
    )
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


class SubjectImportPermission(ImportPermission):
    resource = "subject"


class SubjectImportView(SpreadsheetImportView):
    """Bulk curriculum intake for one program, from a CSV or Excel sheet."""

    permission_classes = (SubjectImportPermission,)
    importer_class = SubjectImporter
    template_example = SUBJECT_TEMPLATE_EXAMPLE
    template_filename = "subject-import-template.csv"

    def build_importer(self, request):
        raw = request.data.get("program")
        try:
            program_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError(
                {"program": "Choose the program this curriculum belongs to."}
            ) from None

        program = Program.objects.filter(pk=program_id, is_archived=False).first()
        # Out of scope reads as absent, so an import cannot enumerate programs.
        if program is None or not has_program_authority(request.user, program.pk):
            raise NotFound("No such program.")
        if not program.is_active:
            raise ValidationError(
                {"program": "That program is inactive. Reactivate it before importing."}
            )

        return SubjectImporter({"request": request}, program)


def resolve_calendar_request(params) -> tuple[str, int, int, int]:
    """The system and year asked for, refused as field errors when unusable."""
    system = (params.get("system") or CalendarSystem.BS.value).upper()
    if system not in {choice.value for choice in CalendarSystem}:
        raise ValidationError({"system": "Choose either BS or AD."})

    minimum, maximum = academic_calendar.year_bounds(system)
    raw_year = params.get("year")
    try:
        year = (
            int(raw_year) if raw_year not in (None, "") else academic_calendar.current_year(system)
        )
    except (TypeError, ValueError) as error:
        raise ValidationError({"year": "That is not a year."}) from error
    if not minimum <= year <= maximum:
        raise ValidationError({"year": f"Choose a year between {minimum} and {maximum}."})
    return system, year, minimum, maximum


def build_calendar_year(system: str, year: int, minimum: int, maximum: int, user=None) -> dict:
    """
    One year, laid out month by month with its entries attached.

    Staff and the student portal read the same function, so the two can never
    show a different calendar.
    """
    configuration = AcademicCalendarConfiguration.current()
    weekend_days = configuration.weekend_days or []
    months = academic_calendar.build_year(system, year, weekend_days)

    first = months[0].days[0].date
    last = months[-1].days[-1].date
    from .calendar_agenda import calendar_agenda

    agenda = calendar_agenda(user, first, last) if user else {}
    entries: dict[datetime.date, list] = {}
    for entry in AcademicCalendarEntry.objects.filter(
        is_archived=False, is_active=True, date__gte=first, date__lte=last
    ):
        entries.setdefault(entry.date, []).append(entry)

    return {
        "system": system,
        "year": year,
        "min_year": minimum,
        "max_year": maximum,
        "weekend_days": sorted(weekend_days),
        # The palette travels with the year so every reader is painted the
        # same, including a student, who cannot reach the settings endpoint.
        "theme": {
            "accent_color": configuration.theme_accent_color,
            "holiday_color": configuration.theme_holiday_color,
            "event_color": configuration.theme_event_color,
            "download_band_color": configuration.theme_download_band_color,
            "show_gregorian_dates": configuration.show_gregorian_dates,
        },
        "months": [
            {
                "index": month.index,
                "name": month.name,
                "name_nepali": month.name_nepali,
                "days": [
                    {
                        "date": day.date,
                        "day": day.day,
                        "day_label": day.day_label,
                        "weekday": day.weekday,
                        "is_weekend": day.is_weekend,
                        "milestones": agenda.get(day.date, []),
                        "entries": AcademicCalendarEntryListSerializer(
                            entries.get(day.date, []), many=True
                        ).data,
                    }
                    for day in month.days
                ],
            }
            for month in months
        ],
    }


class ReadCalendarWriteSuperuser(BasePermission):
    """
    Everyone who teaches or manages reads the calendar; only a superuser says
    what it contains.

    The read is gated on the codename rather than on merely being signed in,
    because a student account is signed in too and belongs on the portal
    endpoint instead — which applies the portal's own conditions first.
    """

    def has_permission(self, request, view):
        if not is_staff_account(request.user):
            return False
        if request.user.is_superuser:
            return True
        if request.method in SAFE_METHODS:
            return "view_academic_calendar" in get_role_permissions(request)
        return False


class AcademicCalendarConfigurationView(generics.GenericAPIView):
    """Read and update the single tenant-scoped calendar policy."""

    permission_classes = (ReadCalendarWriteSuperuser,)
    serializer_class = AcademicCalendarConfigurationSerializer

    def get(self, request):
        # A read must not create an audited row, so this does not get_or_create.
        return Response(
            AcademicCalendarConfigurationSerializer(AcademicCalendarConfiguration.current()).data
        )

    @transaction.atomic
    def put(self, request):
        configuration, _created = AcademicCalendarConfiguration.objects.get_or_create(
            singleton_key=True,
            defaults={"created_by": request.user},
        )
        serializer = AcademicCalendarConfigurationSerializer(
            configuration, data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)


class AcademicCalendarYearView(generics.GenericAPIView):
    """
    One academic year, laid out month by month with its entries attached.

    The grid is built here rather than in the browser because the Bikram Sambat
    calendar is a published table, not a formula, and independent copies of it
    disagree. One table, on the server, means the date a holiday was saved
    against and the cell it appears in can never drift apart.
    """

    permission_classes = (ReadCalendarWriteSuperuser,)
    serializer_class = CalendarYearSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("system", str, description="BS or AD. Defaults to BS."),
            OpenApiParameter("year", int, description="Year in that system. Defaults to today's."),
        ],
        responses=CalendarYearSerializer,
    )
    def get(self, request):
        return Response(
            build_calendar_year(*resolve_calendar_request(request.query_params), user=request.user)
        )


class StudentPortalCalendarYearView(generics.GenericAPIView):
    """
    The same calendar, for a student.

    Students read it through the portal rather than the staff endpoint so that
    the portal's own conditions — login enabled for the college, still
    studying, temporary password already replaced — decide whether they see
    anything, exactly as they do for the rest of a student's record.
    """

    permission_classes = (StudentPortalPermission,)
    serializer_class = CalendarYearSerializer
    pagination_class = None

    @extend_schema(
        operation_id="student_portal_calendar_year",
        parameters=[
            OpenApiParameter("system", str, description="BS or AD. Defaults to BS."),
            OpenApiParameter("year", int, description="Year in that system. Defaults to today's."),
        ],
        responses=CalendarYearSerializer,
    )
    def get(self, request):
        return Response(
            build_calendar_year(*resolve_calendar_request(request.query_params), user=request.user)
        )


class AcademicCalendarEntryViewSet(BaseAcademicViewSet):
    """Holidays and events marked on the college calendar."""

    permission_classes = (AcademicCalendarEntryPermission,)
    queryset = AcademicCalendarEntry.objects.filter(is_archived=False)
    list_serializer_class = AcademicCalendarEntryListSerializer
    create_serializer_class = AcademicCalendarEntryCreateSerializer
    patch_serializer_class = AcademicCalendarEntryPatchSerializer
    archive_message = "Calendar entry removed successfully."
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_class = AcademicCalendarEntryFilter
    search_fields = ("title", "note")
    ordering = ("date", "title")
    ordering_fields = ("date", "kind", "title")
