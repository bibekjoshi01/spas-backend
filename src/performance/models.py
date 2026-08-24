from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Project Imports
from src.base.models import AuditInfoModel

from .constants import AssignmentStatus, AttendanceStatus, InternalExamType

# The parent rows here (session, exam, assignment) validate on save. Their leaf
# rows define clean() but do not call full_clean() in save(), because they are
# written a roster at a time; the serializer validates the batch instead.

# Attendance
# ------------------------------------------------------------------------------------


class AttendanceSession(AuditInfoModel):
    """
    One class held for one allocation.

    Sessions are recorded even when nobody is marked, because attendance
    percentage is meaningless without knowing how many classes were held.
    """

    allocation = models.ForeignKey(
        "academics.SubjectAllocation",
        on_delete=models.CASCADE,
        related_name="attendance_sessions",
        verbose_name=_("allocation"),
    )
    date = models.DateField(_("date"))
    period = models.PositiveSmallIntegerField(
        _("period"),
        default=1,
        help_text=_("Class period on that date. Leave at 1 unless the subject met twice."),
    )

    class Meta:
        verbose_name = _("attendance session")
        verbose_name_plural = _("attendance sessions")
        ordering = (
            "-date",
            "period",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["allocation", "date", "period"],
                condition=models.Q(is_archived=False),
                name="unique_active_session_per_allocation_date",
                violation_error_message=_(
                    "Attendance for that date and period is already recorded."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(period__gte=1),
                name="attendance_session_period_positive",
                violation_error_message=_("The class period must be 1 or more."),
            ),
        )
        indexes = (models.Index(fields=["allocation", "date"]),)

    def clean(self):
        super().clean()

        if self.date and self.date > timezone.localdate():
            raise ValidationError({"date": _("A class cannot be recorded for a future date.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.allocation.subject.code} — {self.date}"


class AttendanceRecord(AuditInfoModel):
    """How one enrolled student was marked in one session."""

    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        related_name="records",
        verbose_name=_("session"),
    )
    enrollment = models.ForeignKey(
        "students.SubjectEnrollment",
        on_delete=models.CASCADE,
        related_name="attendance_records",
        verbose_name=_("enrollment"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=AttendanceStatus.choices(),
        default=AttendanceStatus.PRESENT.value,
    )

    class Meta:
        verbose_name = _("attendance record")
        verbose_name_plural = _("attendance records")
        ordering = (
            "session",
            "enrollment",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["session", "enrollment"],
                condition=models.Q(is_archived=False),
                name="unique_active_attendance_per_session_student",
                violation_error_message=_("That student is already marked for this class."),
            ),
        )
        indexes = (models.Index(fields=["enrollment", "status"]),)

    def clean(self):
        super().clean()

        if not (self.session_id and self.enrollment_id):
            return

        if self.session.allocation_id != self.enrollment.allocation_id:
            raise ValidationError(
                {"enrollment": _("That student is not enrolled in this subject.")}
            )

    def __str__(self):
        return f"{self.enrollment.student.roll_number} — {self.status}"


# Internal exams
# ------------------------------------------------------------------------------------


class InternalExam(AuditInfoModel):
    """One internal assessment set by the teacher of an allocation."""

    allocation = models.ForeignKey(
        "academics.SubjectAllocation",
        on_delete=models.CASCADE,
        related_name="internal_exams",
        verbose_name=_("allocation"),
    )
    title = models.CharField(_("title"), max_length=100)
    exam_type = models.CharField(
        _("exam type"),
        max_length=20,
        choices=InternalExamType.choices(),
        default=InternalExamType.OTHER.value,
    )
    full_marks = models.PositiveSmallIntegerField(_("full marks"))
    pass_marks = models.PositiveSmallIntegerField(
        _("pass marks"),
        null=True,
        blank=True,
        help_text=_("Leave empty when the exam has no pass line."),
    )
    exam_date = models.DateField(_("exam date"), null=True, blank=True)

    class Meta:
        verbose_name = _("internal exam")
        verbose_name_plural = _("internal exams")
        ordering = (
            "exam_date",
            "id",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["allocation", "title"],
                condition=models.Q(is_archived=False),
                name="unique_active_exam_title_per_allocation",
                violation_error_message=_("This class already has an exam with that title."),
            ),
            models.CheckConstraint(
                condition=models.Q(full_marks__gt=0),
                name="internal_exam_full_marks_positive",
                violation_error_message=_("Full marks must be greater than zero."),
            ),
            models.CheckConstraint(
                condition=models.Q(pass_marks__isnull=True)
                | models.Q(pass_marks__lte=models.F("full_marks")),
                name="internal_exam_pass_marks_within_full",
                violation_error_message=_("Pass marks cannot exceed full marks."),
            ),
        )
        indexes = (models.Index(fields=["allocation", "exam_type"]),)

    def clean(self):
        super().clean()

        if self.pass_marks is not None and self.full_marks and self.pass_marks > self.full_marks:
            raise ValidationError({"pass_marks": _("Pass marks cannot exceed full marks.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.allocation.subject.code} — {self.title}"


class InternalExamMark(AuditInfoModel):
    """What one enrolled student scored in one internal exam."""

    exam = models.ForeignKey(
        InternalExam,
        on_delete=models.CASCADE,
        related_name="marks",
        verbose_name=_("exam"),
    )
    enrollment = models.ForeignKey(
        "students.SubjectEnrollment",
        on_delete=models.CASCADE,
        related_name="internal_marks",
        verbose_name=_("enrollment"),
    )
    marks_obtained = models.DecimalField(
        _("marks obtained"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Empty when the student was absent."),
    )
    is_absent = models.BooleanField(_("absent"), default=False)

    class Meta:
        verbose_name = _("internal exam mark")
        verbose_name_plural = _("internal exam marks")
        ordering = (
            "exam",
            "enrollment",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["exam", "enrollment"],
                condition=models.Q(is_archived=False),
                name="unique_active_mark_per_exam_student",
                violation_error_message=_("That student already has a mark for this exam."),
            ),
            models.CheckConstraint(
                condition=models.Q(marks_obtained__isnull=True) | models.Q(marks_obtained__gte=0),
                name="internal_mark_not_negative",
                violation_error_message=_("Marks cannot be negative."),
            ),
            models.CheckConstraint(
                condition=models.Q(is_absent=False) | models.Q(marks_obtained__isnull=True),
                name="internal_mark_absent_has_no_score",
                violation_error_message=_("A student marked absent cannot also have marks."),
            ),
        )
        indexes = (models.Index(fields=["enrollment"]),)

    def clean(self):
        super().clean()

        if not (self.exam_id and self.enrollment_id):
            return

        if self.exam.allocation_id != self.enrollment.allocation_id:
            raise ValidationError(
                {"enrollment": _("That student is not enrolled in this subject.")}
            )

        if self.is_absent and self.marks_obtained is not None:
            raise ValidationError({"marks_obtained": _("An absent student has no marks.")})

        if self.marks_obtained is not None and self.marks_obtained > self.exam.full_marks:
            raise ValidationError(
                {"marks_obtained": _("Marks cannot exceed the full marks of the exam.")}
            )

    def __str__(self):
        return f"{self.enrollment.student.roll_number} — {self.marks_obtained}"


# Assignments
# ------------------------------------------------------------------------------------


class Assignment(AuditInfoModel):
    """One assignment given to an allocation."""

    allocation = models.ForeignKey(
        "academics.SubjectAllocation",
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("allocation"),
    )
    title = models.CharField(_("title"), max_length=150)
    assigned_date = models.DateField(_("assigned date"))
    due_date = models.DateField(_("due date"), null=True, blank=True)

    class Meta:
        verbose_name = _("assignment")
        verbose_name_plural = _("assignments")
        ordering = (
            "-assigned_date",
            "id",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["allocation", "title"],
                condition=models.Q(is_archived=False),
                name="unique_active_assignment_title_per_allocation",
                violation_error_message=_("This class already has an assignment with that title."),
            ),
            models.CheckConstraint(
                condition=models.Q(due_date__isnull=True)
                | models.Q(due_date__gte=models.F("assigned_date")),
                name="assignment_due_after_assigned",
                violation_error_message=_("The due date cannot fall before the assigned date."),
            ),
        )
        indexes = (models.Index(fields=["allocation"]),)

    def clean(self):
        super().clean()

        if self.due_date and self.assigned_date and self.due_date < self.assigned_date:
            raise ValidationError({"due_date": _("Due date cannot fall before the assigned date.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.allocation.subject.code} — {self.title}"


class AssignmentSubmission(AuditInfoModel):
    """How far one enrolled student got with one assignment."""

    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name=_("assignment"),
    )
    enrollment = models.ForeignKey(
        "students.SubjectEnrollment",
        on_delete=models.CASCADE,
        related_name="assignment_submissions",
        verbose_name=_("enrollment"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=AssignmentStatus.choices(),
        default=AssignmentStatus.NOT_DONE.value,
    )
    remarks = models.TextField(_("remarks"), blank=True)

    class Meta:
        verbose_name = _("assignment submission")
        verbose_name_plural = _("assignment submissions")
        ordering = (
            "assignment",
            "enrollment",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["assignment", "enrollment"],
                condition=models.Q(is_archived=False),
                name="unique_active_submission_per_assignment_student",
                violation_error_message=_("That student already has a status for this assignment."),
            ),
        )
        indexes = (models.Index(fields=["enrollment", "status"]),)

    def clean(self):
        super().clean()

        if not (self.assignment_id and self.enrollment_id):
            return

        if self.assignment.allocation_id != self.enrollment.allocation_id:
            raise ValidationError(
                {"enrollment": _("That student is not enrolled in this subject.")}
            )

    def __str__(self):
        return f"{self.enrollment.student.roll_number} — {self.status}"
