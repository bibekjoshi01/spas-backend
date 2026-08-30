from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

# Project Imports
from src.base.models import AuditInfoModel

from .constants import (
    DEFAULT_ELIGIBILITY_THRESHOLD,
    AssignmentStatus,
    AttendanceStatus,
    InternalExamType,
)

# The parent rows here (session, exam, assignment) validate on save. Their leaf
# rows define clean() but do not call full_clean() in save(), because they are
# written a roster at a time; the serializer validates the batch instead.

# Attendance
# ------------------------------------------------------------------------------------


class AttendanceSession(AuditInfoModel):
    history = HistoricalRecords()
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

        if not (self.date and self.allocation_id):
            return

        semester = self.allocation.batch_semester
        if semester.start_date and self.date < semester.start_date:
            raise ValidationError(
                {"date": _("Attendance cannot be recorded before the semester starts.")}
            )
        if semester.end_date and self.date > semester.end_date:
            raise ValidationError(
                {"date": _("Attendance cannot be recorded after the semester ends.")}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.allocation.subject.code} — {self.date}"


class AttendanceRecord(AuditInfoModel):
    history = HistoricalRecords()
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

    history = HistoricalRecords()

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

    history = HistoricalRecords()

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

    history = HistoricalRecords()

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

    history = HistoricalRecords()

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


# Class performance
# ------------------------------------------------------------------------------------


class ClassPerformanceRating(AuditInfoModel):
    """A teacher's current holistic 1-10 rating for one student in one class."""

    history = HistoricalRecords()
    enrollment = models.ForeignKey(
        "students.SubjectEnrollment",
        on_delete=models.CASCADE,
        related_name="class_performance_ratings",
        verbose_name=_("enrollment"),
    )
    score = models.PositiveSmallIntegerField(
        _("score"),
        help_text=_("Overall class performance from 1 (needs support) to 10 (exceptional)."),
    )
    remarks = models.TextField(_("remarks"), blank=True)

    class Meta:
        verbose_name = _("class performance rating")
        verbose_name_plural = _("class performance ratings")
        ordering = ("enrollment",)
        constraints = (
            models.UniqueConstraint(
                fields=["enrollment"],
                condition=models.Q(is_archived=False),
                name="unique_active_class_performance_per_enrollment",
                violation_error_message=_("That student already has a class performance rating."),
            ),
            models.CheckConstraint(
                condition=models.Q(score__gte=1) & models.Q(score__lte=10),
                name="class_performance_score_between_1_and_10",
                violation_error_message=_("Class performance must be between 1 and 10."),
            ),
        )
        indexes = (models.Index(fields=["score"]),)

    def clean(self):
        super().clean()
        if self.score is not None and not 1 <= self.score <= 10:
            raise ValidationError({"score": _("Class performance must be between 1 and 10.")})

    def __str__(self):
        return f"{self.enrollment.student.roll_number} — {self.score}/10"


class PerformanceWeightConfiguration(AuditInfoModel):
    """
    The college's performance policy: how the overall score is composed, and
    the attendance bar a student has to clear.

    One row per tenant. The attendance requirement lives here rather than in
    code because affiliating universities do not agree on it — 75% is the
    common rule, but colleges apply anything from 70% upward.
    """

    history = HistoricalRecords()
    singleton_key = models.BooleanField(default=True, unique=True, editable=False)
    attendance_weight = models.PositiveSmallIntegerField(default=20)
    class_performance_weight = models.PositiveSmallIntegerField(default=10)
    assignment_weight = models.PositiveSmallIntegerField(default=30)
    assessment_weight = models.PositiveSmallIntegerField(default=40)
    attendance_eligibility_threshold = models.DecimalField(
        _("attendance eligibility threshold"),
        max_digits=5,
        decimal_places=2,
        default=DEFAULT_ELIGIBILITY_THRESHOLD,
        help_text=_("Minimum attendance percentage a student must hold to count as eligible."),
    )

    class Meta:
        verbose_name = _("performance weight configuration")
        verbose_name_plural = _("performance weight configuration")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(attendance_weight__gte=0)
                    & models.Q(attendance_weight__lte=100)
                    & models.Q(class_performance_weight__gte=0)
                    & models.Q(class_performance_weight__lte=100)
                    & models.Q(assignment_weight__gte=0)
                    & models.Q(assignment_weight__lte=100)
                    & models.Q(assessment_weight__gte=0)
                    & models.Q(assessment_weight__lte=100)
                ),
                name="performance_weights_each_between_0_and_100",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(attendance_eligibility_threshold__gte=0)
                    & models.Q(attendance_eligibility_threshold__lte=100)
                ),
                name="attendance_eligibility_threshold_between_0_and_100",
                violation_error_message=_(
                    "The attendance requirement must be between 0 and 100 percent."
                ),
            ),
        )

    def clean(self):
        super().clean()
        total = (
            self.attendance_weight
            + self.class_performance_weight
            + self.assignment_weight
            + self.assessment_weight
        )
        if total != 100:
            raise ValidationError(_("Performance weights must total exactly 100%."))

    @classmethod
    def current(cls) -> "PerformanceWeightConfiguration":
        """
        This college's policy.

        Returns an unsaved instance carrying the shipped defaults when the
        settings screen has never been opened, so a read never writes an
        audited row and never has to special-case a missing configuration.
        """
        return cls.objects.filter(singleton_key=True).first() or cls()

    @property
    def eligibility_threshold(self) -> float:
        """The attendance requirement, as a float to compare against percentages."""
        return float(self.attendance_eligibility_threshold)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return _("Performance policy")
