from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

# Project Imports
from src.base.models import AuditInfoModel
from src.user.constants import Genders

from .constants import SemesterEnrollmentStatus, StudentStatus


class Student(AuditInfoModel):
    """
    A student of the college.

    The batch here is the admission cohort and never changes. Where the student
    actually studies each semester is a SemesterEnrollment, so promotion,
    repetition and graduation never rewrite this row.
    """

    batch = models.ForeignKey(
        "academics.Batch",
        on_delete=models.PROTECT,
        related_name="students",
        verbose_name=_("batch"),
        help_text=_("Admission cohort. Program and department follow from it."),
    )
    roll_number = models.CharField(_("roll number"), max_length=20)
    registration_number = models.CharField(
        _("registration number"),
        max_length=30,
        blank=True,
        help_text=_("University registration number, where one has been issued."),
    )

    first_name = models.CharField(_("first name"), max_length=100)
    middle_name = models.CharField(_("middle name"), max_length=100, blank=True)
    last_name = models.CharField(_("last name"), max_length=100)

    gender = models.CharField(_("gender"), max_length=20, choices=Genders.choices(), blank=True)
    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True)
    email = models.EmailField(_("email address"), blank=True)
    phone_no = models.CharField(_("phone number"), max_length=15, blank=True)

    status = models.CharField(
        _("status"),
        max_length=20,
        choices=StudentStatus.choices(),
        default=StudentStatus.STUDYING.value,
    )

    class Meta:
        verbose_name = _("student")
        verbose_name_plural = _("students")
        ordering = (
            "batch",
            "roll_number",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["batch", "roll_number"],
                condition=models.Q(is_archived=False),
                name="unique_active_roll_number_per_batch",
                violation_error_message=_("That roll number is already taken in this batch."),
            ),
            models.UniqueConstraint(
                fields=["registration_number"],
                condition=models.Q(is_archived=False) & ~models.Q(registration_number=""),
                name="unique_active_student_registration_number",
                violation_error_message=_("Another student already has that registration number."),
            ),
        )
        indexes = (models.Index(fields=["batch", "status"]),)

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(part for part in parts if part)

    def __str__(self):
        return f"{self.roll_number} — {self.full_name}"


class SemesterEnrollment(AuditInfoModel):
    """
    A student studying one semester.

    One row per semester the student sits. A repeating student gets a second
    row against the semester they are repeating — which may belong to a junior
    batch — and their Student row is untouched.
    """

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="semester_enrollments",
        verbose_name=_("student"),
    )
    batch_semester = models.ForeignKey(
        "academics.BatchSemester",
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name=_("batch semester"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=SemesterEnrollmentStatus.choices(),
        default=SemesterEnrollmentStatus.ACTIVE.value,
    )

    class Meta:
        verbose_name = _("semester enrollment")
        verbose_name_plural = _("semester enrollments")
        ordering = (
            "student",
            "batch_semester",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["student", "batch_semester"],
                condition=models.Q(is_archived=False),
                name="unique_active_enrollment_per_student_semester",
                violation_error_message=_("That student is already enrolled in this semester."),
            ),
        )
        indexes = (
            models.Index(fields=["batch_semester", "status"]),
            models.Index(fields=["student"]),
        )

    def clean(self):
        super().clean()

        if not (self.student_id and self.batch_semester_id):
            return

        if self.batch_semester.batch.program_id != self.student.batch.program_id:
            raise ValidationError(
                {"batch_semester": _("That semester belongs to a different program.")}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.roll_number} — {self.batch_semester}"


class SubjectEnrollment(AuditInfoModel):
    """
    A student taking one allocated subject — the roster row.

    Every attendance mark, exam score and assignment status points here, so a
    record can only exist for a student who is actually taking the subject.
    Retakes point at the allocation of whichever batch is running the subject.
    """

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="subject_enrollments",
        verbose_name=_("student"),
    )
    allocation = models.ForeignKey(
        "academics.SubjectAllocation",
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name=_("allocation"),
    )
    is_retake = models.BooleanField(
        _("retake"),
        default=False,
        help_text=_("Student is repeating this subject from an earlier semester."),
    )

    class Meta:
        verbose_name = _("subject enrollment")
        verbose_name_plural = _("subject enrollments")
        ordering = (
            "allocation",
            "student",
        )
        constraints = (
            models.UniqueConstraint(
                fields=["student", "allocation"],
                condition=models.Q(is_archived=False),
                name="unique_active_enrollment_per_student_allocation",
                violation_error_message=_("That student is already registered on this class."),
            ),
        )
        indexes = (
            models.Index(fields=["allocation"]),
            models.Index(fields=["student"]),
        )

    def clean(self):
        super().clean()

        if not (self.student_id and self.allocation_id):
            return

        if self.allocation.subject.program_id != self.student.batch.program_id:
            raise ValidationError({"allocation": _("That subject belongs to a different program.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.roll_number} — {self.allocation.subject.code}"
