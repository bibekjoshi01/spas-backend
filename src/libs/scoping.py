"""
Row-level authority.

Permissions say *what* a user may do; these say *which rows* they may do it to.
Authority is data, not a role name: a department head is whoever a Department
points at, and a coordinator is whoever a Program points at. That keeps the
head of Computer Science out of Management's records without anyone
maintaining a list of exceptions.

Authority grows: a coordinator covers their programme, a head covers every
programme in their department, and a superuser covers the college.
"""

from django.db.models import Q, QuerySet

from src.academics.models import Department, Program


class ManagementScope:
    """The departments and programmes one user's authority covers."""

    def __init__(
        self,
        department_ids: set[int],
        program_ids: set[int],
        *,
        unlimited: bool,
        by_programme: bool = False,
    ):
        self.department_ids = department_ids
        self.program_ids = program_ids
        self.unlimited = unlimited
        # A coordinator's department is context, not authority: they need its
        # teachers to allocate a subject and its name to label a screen, but
        # anything reachable by programme must match the programme instead, or
        # they would see the whole department's batches and students.
        self.by_programme = by_programme

    @property
    def is_empty(self) -> bool:
        return not self.unlimited and not self.department_ids and not self.program_ids


def management_scope(user) -> ManagementScope:
    if user is None or user.is_anonymous:
        return ManagementScope(set(), set(), unlimited=False)

    if user.is_superuser:
        return ManagementScope(set(), set(), unlimited=True)

    departments = set(
        Department.objects.filter(head=user, is_archived=False).values_list("id", flat=True)
    )
    programs = set(
        Program.objects.filter(coordinator=user, is_archived=False).values_list("id", flat=True)
    )

    if departments:
        # A head reaches every programme in their department without the
        # department having to name them one by one.
        programs |= set(
            Program.objects.filter(department_id__in=departments, is_archived=False).values_list(
                "id", flat=True
            )
        )
        return ManagementScope(departments, programs, unlimited=False)

    if programs:
        context = set(
            Program.objects.filter(id__in=programs).values_list("department_id", flat=True)
        )
        return ManagementScope(context, programs, unlimited=False, by_programme=True)

    return ManagementScope(set(), set(), unlimited=False)


def has_department_authority(user, department_id: int) -> bool:
    scope = management_scope(user)
    return scope.unlimited or department_id in scope.department_ids


def has_program_authority(user, program_id: int) -> bool:
    scope = management_scope(user)
    return scope.unlimited or program_id in scope.program_ids


def scope_by_authority(
    queryset: QuerySet,
    user,
    *,
    department_path: str | None,
    program_path: str | None,
) -> QuerySet:
    """
    Narrow a queryset to the rows the user has authority over.

    `department_path` and `program_path` are how this model reaches a
    department and a programme; either may be None where the model has no such
    route. A user with no authority at all sees nothing rather than everything.
    """
    scope = management_scope(user)

    if scope.unlimited:
        return queryset

    if scope.is_empty:
        return queryset.none()

    condition = Q()

    if program_path and scope.program_ids:
        condition |= Q(**{f"{program_path}__in": scope.program_ids})

    # Skipped for a coordinator on anything the programme already reaches,
    # so their department does not widen what they can see.
    department_is_authority = not (scope.by_programme and program_path)
    if department_path and scope.department_ids and department_is_authority:
        condition |= Q(**{f"{department_path}__in": scope.department_ids})

    if not condition:
        return queryset.none()

    return queryset.filter(condition).distinct()


class AuthorityScopedMixin:
    """
    Applies row-level authority to a viewset.

    Set the two paths to how this model reaches a department and a programme.
    """

    department_path: str | None = None
    program_path: str | None = None

    def get_queryset(self):
        return scope_by_authority(
            super().get_queryset(),
            getattr(self.request, "user", None),
            department_path=self.department_path,
            program_path=self.program_path,
        )
