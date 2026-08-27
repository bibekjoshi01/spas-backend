from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

# Project Imports
from src.base.models import AuditInfoModel

from .constants import (
    MAX_SEMESTERS,
    SemesterChoices,
    SemesterStatus,
)


class Department(AuditInfoModel):
    history = HistoricalRecords()
    """An academic department of the college — owns programs and teachers."""

    name = models.CharField(_("name"), max_length=100)
    code = models.CharField(
        _("code"),
        max_length=20,
        help_text=_("Short identifier used in listings, e.g. CSIT."),
    )
    head = models.ForeignKey(
        "user.User",
        on_delete=models.SET_NULL,
        related_name="headed_departments",
        verbose_name=_("head of department"),
        null=True,
        blank=True,
        help_text=_("Whose authority covers everything under this department."),
    )

    class Meta:
        verbose_name = _("department")
        verbose_name_plural = _("departments")
        ordering = ("name",)
        constraints = (
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(is_archived=False),
                name="unique_active_department_name",
                violation_error_message=_("A department with that name already exists."),
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(is_archived=False),
                name="unique_active_department_code",
                violation_error_message=_("A department with that code already exists."),
            ),
        )

    def __str__(self):
        return self.name


class Program(AuditInfoModel):
    history = HistoricalRecords()
    """A degree program run by a department, e.g. B.Sc. CSIT."""

    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="programs",
        verbose_name=_("department"),
    )
    name = models.CharField(_("name"), max_length=150)
    code = models.CharField(
        _("code"),
        max_length=20,
        help_text=_("Short identifier, e.g. BSCCSIT."),
    )
    total_semesters = models.PositiveSmallIntegerField(
        _("total semesters"),
        default=MAX_SEMESTERS,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_SEMESTERS)],
        help_text=_("Number of semesters a student must clear to graduate."),
    )
    coordinator = models.ForeignKey(
        "user.User",
        on_delete=models.SET_NULL,
        related_name="coordinated_programs",
        verbose_name=_("coordinator"),
        null=True,
        blank=True,
        help_text=_("Teacher who allocates subjects and manages enrollment for this program."),
    )

    class Meta:
        verbose_name = _("program")
        verbose_name_plural = _("programs")
        ordering = ("name",)
        constraints = (
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(is_archived=False),
                name="unique_active_program_code",
                violation_error_message=_("A program with that code already exists."),
            ),
            models.UniqueConstraint(
                fields=["department", "name"],
                condition=models.Q(is_archived=False),
                name="unique_active_program_name_per_department",
                violation_error_message=_("That department already has a program with this name."),
            ),
            models.CheckConstraint(
                condition=models.Q(total_semesters__gte=1, total_semesters__lte=MAX_SEMESTERS),
                name="program_total_semesters_in_range",
                violation_error_message=_("A program must run for between 1 and 8 semesters."),
            ),
        )
        indexes = (models.Index(fields=["department"]),)

    def __str__(self):
        return self.name


class Batch(AuditInfoModel):
    history = HistoricalRecords()
    """
    One intake cohort of a program, identified by its entry year (2079, 2080…).

    A batch belongs to exactly one program, so a student's program and
    department are reachable from the batch and are never stored twice.
    """

    program = models.ForeignKey(
        Program,
        on_delete=models.PROTECT,
        related_name="batches",
        verbose_name=_("program"),
    )
    year = models.PositiveSmallIntegerField(
        _("entry year"),
        help_text=_("Year the batch was admitted, in the calendar the college uses."),
    )

    class Meta:
        verbose_name = _("batch")
        verbose_name_plural = _("batches")
        ordering = (
            "-year",
            "program__name",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["program", "year"],
                condition=models.Q(is_archived=False),
                name="unique_active_batch_per_program_year",
                violation_error_message=_("That program already has a batch for this year."),
            ),
        )

    def __str__(self):
        return f"{self.program.code} {self.year}"


class BatchSemester(AuditInfoModel):
    history = HistoricalRecords()
    """
    One semester of one batch — the tenure everything else hangs from.

    This is what makes progression possible without touching a student row:
    moving a batch forward means creating the next BatchSemester and enrolling
    its students into it.
    """

    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        related_name="semesters",
        verbose_name=_("batch"),
    )
    semester = models.PositiveSmallIntegerField(_("semester"), choices=SemesterChoices.choices)
    start_date = models.DateField(_("start date"), null=True, blank=True)
    end_date = models.DateField(_("end date"), null=True, blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=SemesterStatus.choices(),
        default=SemesterStatus.UPCOMING.value,
    )

    class Meta:
        verbose_name = _("batch semester")
        verbose_name_plural = _("batch semesters")
        ordering = (
            "batch",
            "semester",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["batch", "semester"],
                condition=models.Q(is_archived=False),
                name="unique_active_semester_per_batch",
                violation_error_message=_("That batch has already sat this semester."),
            ),
            models.UniqueConstraint(
                fields=["batch"],
                condition=models.Q(
                    status=SemesterStatus.RUNNING.value,
                    is_archived=False,
                ),
                name="unique_running_semester_per_batch",
                violation_error_message=_(
                    "This batch already has a semester running. Mark that one completed first."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(start_date__isnull=True)
                | models.Q(end_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="batch_semester_dates_ordered",
                violation_error_message=_("The end date cannot fall before the start date."),
            ),
        )
        indexes = (models.Index(fields=["status"]),)

    def clean(self):
        super().clean()

        if self.batch_id and self.semester > self.batch.program.total_semesters:
            raise ValidationError(
                {
                    "semester": _("This program only runs for %(total)s semesters.")
                    % {"total": self.batch.program.total_semesters}
                }
            )

        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": _("End date cannot fall before the start date.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.batch} — semester {self.semester}"


class Subject(AuditInfoModel):
    history = HistoricalRecords()
    """
    A subject in a program's curriculum, at the semester that curriculum puts it.

    A code is unique per (program, semester) rather than per program, so a
    syllabus revision that moves a subject can be entered as a second row while
    both curricula are running. The semester of record for any recorded data is
    always allocation.batch_semester.semester, never this field.
    """

    program = models.ForeignKey(
        Program,
        on_delete=models.PROTECT,
        related_name="subjects",
        verbose_name=_("program"),
    )
    semester = models.PositiveSmallIntegerField(_("semester"), choices=SemesterChoices.choices)
    code = models.CharField(_("code"), max_length=20, help_text=_("e.g. CSC 201."))
    name = models.CharField(_("name"), max_length=150)
    credit_hours = models.PositiveSmallIntegerField(_("credit hours"), default=3)
    is_elective = models.BooleanField(
        _("elective"),
        default=False,
        help_text=_("Elective subjects are taken by some students of the semester, not all."),
    )

    class Meta:
        verbose_name = _("subject")
        verbose_name_plural = _("subjects")
        ordering = (
            "program",
            "semester",
            "code",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["program", "code", "semester"],
                condition=models.Q(is_archived=False),
                name="unique_active_subject_code_per_program_semester",
                violation_error_message=_(
                    "That program already has a subject with this code in this semester."
                ),
            ),
        )
        indexes = (models.Index(fields=["program", "semester"]),)

    def clean(self):
        super().clean()

        if self.program_id and self.semester > self.program.total_semesters:
            raise ValidationError(
                {
                    "semester": _("This program only runs for %(total)s semesters.")
                    % {"total": self.program.total_semesters}
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.name}"


class SubjectAllocation(AuditInfoModel):
    history = HistoricalRecords()
    """
    A subject being taught to a batch in a given semester, by one teacher.

    This is the class: the department head or program coordinator creates it,
    and it is the anchor for attendance, internal exams and assignments. It is
    also the boundary of a teacher's visibility — a teacher sees exactly the
    data hanging off their own allocations.
    """

    batch_semester = models.ForeignKey(
        BatchSemester,
        on_delete=models.CASCADE,
        related_name="allocations",
        verbose_name=_("batch semester"),
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name=_("subject"),
    )
    teacher = models.ForeignKey(
        "user.User",
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name=_("teacher"),
    )
    start_time = models.TimeField(_("start time"), null=True, blank=True)
    end_time = models.TimeField(_("end time"), null=True, blank=True)

    class Meta:
        verbose_name = _("subject allocation")
        verbose_name_plural = _("subject allocations")
        ordering = (
            "batch_semester",
            "subject__code",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["batch_semester", "subject"],
                condition=models.Q(is_archived=False),
                name="unique_active_allocation_per_semester_subject",
                violation_error_message=_(
                    "That subject is already allocated for this batch semester."
                ),
            ),
        )
        indexes = (
            models.Index(fields=["teacher"]),
            models.Index(fields=["batch_semester"]),
        )

    def clean(self):
        super().clean()

        if not (self.batch_semester_id and self.subject_id):
            return

        if self.subject.program_id != self.batch_semester.batch.program_id:
            raise ValidationError(
                {"subject": _("This subject belongs to a different program than the batch.")}
            )

        if self.subject.semester != self.batch_semester.semester:
            raise ValidationError(
                {"subject": _("This subject is not taught in that semester of the program.")}
            )

        if bool(self.start_time) != bool(self.end_time):
            raise ValidationError(
                {"start_time": _("Provide both start and end time, or leave both empty.")}
            )
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": _("End time must be after start time.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.subject.code} — {self.batch_semester}"
