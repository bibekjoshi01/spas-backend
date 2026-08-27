from django.db import transaction
from rest_framework import serializers

# Project Imports
from src.academics.constants import SemesterStatus
from src.academics.models import SubjectAllocation
from src.academics.serializers import AuditedModelSerializer, created, updated
from src.libs.get_context import get_user_by_context
from src.libs.permissions import scope_to_allocation_owner
from src.students.models import SubjectEnrollment

from .constants import AssignmentStatus, AttendanceStatus
from .models import (
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    ClassPerformanceRating,
    InternalExam,
    InternalExamMark,
    PerformanceWeightConfiguration,
)


class PerformanceWeightConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerformanceWeightConfiguration
        fields = (
            "attendance_weight",
            "class_performance_weight",
            "assignment_weight",
            "assessment_weight",
            "updated_at",
        )
        read_only_fields = ("updated_at",)

    def validate(self, attrs):
        instance = self.instance
        values = {
            field: attrs.get(field, getattr(instance, field, None))
            for field in (
                "attendance_weight",
                "class_performance_weight",
                "assignment_weight",
                "assessment_weight",
            )
        }
        if any(value is None for value in values.values()):
            raise serializers.ValidationError("All four performance weights are required.")
        if sum(values.values()) != 100:
            raise serializers.ValidationError("Performance weights must total exactly 100%.")
        return attrs


class OwnAllocationMixin:
    """
    Refuses to write against a class the caller was not allocated.

    Holding add_attendance says a teacher may record attendance; it does not
    say whose class. Without this, any teacher could post marks onto another
    teacher's roster.
    """

    def validate_allocation(self, allocation):
        user = get_user_by_context(self.context)

        allowed = scope_to_allocation_owner(
            SubjectAllocation.objects.filter(pk=allocation.pk),
            user,
            path="teacher",
        ).exists()

        if not allowed:
            raise serializers.ValidationError("That class is not allocated to you.")

        validate_allocation_is_writable(allocation)

        return allocation


def validate_allocation_is_writable(allocation: SubjectAllocation) -> None:
    """Performance records are immutable outside a running semester."""
    status = allocation.batch_semester.status
    if status == SemesterStatus.COMPLETED.value:
        raise serializers.ValidationError(
            "This semester is completed. Historical class records are read-only."
        )
    if status == SemesterStatus.UPCOMING.value:
        raise serializers.ValidationError(
            "This semester has not started. Class records are read-only."
        )


class RosterEntryMixin:
    """
    Shared validation for anything written a roster at a time.

    Every entry names a SubjectEnrollment. Each one is checked against the
    parent's allocation, which is the guarantee that a mark can never land on a
    student who is not taking the subject.
    """

    def resolve_enrollments(self, allocation: SubjectAllocation, entries: list[dict]) -> dict:
        enrollment_ids = [entry["enrollment"] for entry in entries]

        if len(set(enrollment_ids)) != len(enrollment_ids):
            raise serializers.ValidationError(
                {"entries": "The same student appears more than once."}
            )

        found = SubjectEnrollment.objects.filter(
            id__in=enrollment_ids, allocation=allocation, is_archived=False
        )
        by_id = {enrollment.id: enrollment for enrollment in found}

        missing = sorted(set(enrollment_ids) - set(by_id))
        if missing:
            raise serializers.ValidationError(
                {"entries": f"These students are not registered on this class: {missing}."}
            )

        return by_id


# Attendance
# ------------------------------------------------------------------------------------


class AttendanceEntrySerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    status = serializers.ChoiceField(choices=AttendanceStatus.choices())


class AttendanceRecordReadSerializer(serializers.ModelSerializer):
    student_id = serializers.IntegerField(source="enrollment.student_id", read_only=True)
    roll_number = serializers.CharField(source="enrollment.student.roll_number", read_only=True)
    full_name = serializers.CharField(source="enrollment.student.full_name", read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = ("id", "enrollment", "student_id", "roll_number", "full_name", "status")


class AttendanceSessionListSerializer(serializers.ModelSerializer):
    subject_code = serializers.CharField(source="allocation.subject.code", read_only=True)
    present_count = serializers.IntegerField(read_only=True)
    marked_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AttendanceSession
        fields = (
            "id",
            "uuid",
            "allocation",
            "subject_code",
            "date",
            "period",
            "marked_count",
            "present_count",
        )


class AttendanceSessionRetrieveSerializer(serializers.ModelSerializer):
    records = AttendanceRecordReadSerializer(many=True, read_only=True)

    class Meta:
        model = AttendanceSession
        fields = ("id", "uuid", "allocation", "date", "period", "records")


class AttendanceSessionCreateSerializer(
    OwnAllocationMixin, RosterEntryMixin, serializers.Serializer
):
    """
    Record one class and the whole roster's attendance in a single call.

    The teacher's screen submits once; re-submitting the same date updates the
    marks rather than creating a second session.
    """

    allocation = serializers.PrimaryKeyRelatedField(
        queryset=SubjectAllocation.objects.filter(is_archived=False)
    )
    date = serializers.DateField()
    period = serializers.IntegerField(default=1, min_value=1)
    entries = AttendanceEntrySerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        validate_allocation_is_writable(attrs["allocation"])
        semester = attrs["allocation"].batch_semester
        date = attrs["date"]

        if semester.start_date and date < semester.start_date:
            raise serializers.ValidationError(
                {"date": "Attendance cannot be recorded before the semester starts."}
            )
        if semester.end_date and date > semester.end_date:
            raise serializers.ValidationError(
                {"date": "Attendance cannot be recorded after the semester ends."}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = get_user_by_context(self.context)
        allocation = validated_data["allocation"]
        entries = validated_data["entries"]
        by_id = self.resolve_enrollments(allocation, entries)

        session, created_session = AttendanceSession.objects.get_or_create(
            allocation=allocation,
            date=validated_data["date"],
            period=validated_data["period"],
            is_archived=False,
            defaults={"created_by": user},
        )
        if not created_session:
            session.updated_by = user
            session.save(update_fields=("updated_by", "updated_at"))

        for entry in entries:
            AttendanceRecord.objects.update_or_create(
                session=session,
                enrollment=by_id[entry["enrollment"]],
                is_archived=False,
                defaults={"status": entry["status"], "updated_by": user},
                create_defaults={"status": entry["status"], "created_by": user},
            )

        return session

    def to_representation(self, instance):
        return {
            "message": "Attendance recorded successfully.",
            "id": instance.id,
            "marked": instance.records.filter(is_archived=False).count(),
        }


# Internal exams
# ------------------------------------------------------------------------------------


class InternalExamListSerializer(serializers.ModelSerializer):
    subject_code = serializers.CharField(source="allocation.subject.code", read_only=True)
    marked_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = InternalExam
        fields = (
            "id",
            "uuid",
            "allocation",
            "subject_code",
            "title",
            "exam_type",
            "full_marks",
            "pass_marks",
            "exam_date",
            "marked_count",
            "is_active",
        )


class InternalExamCreateSerializer(OwnAllocationMixin, AuditedModelSerializer):
    pass_marks = serializers.IntegerField(min_value=1, required=True)

    class Meta:
        model = InternalExam
        fields = ("allocation", "title", "exam_type", "full_marks", "pass_marks", "exam_date")

    to_representation = created("Exam")


class InternalExamPatchSerializer(AuditedModelSerializer):
    pass_marks = serializers.IntegerField(min_value=1, required=False)

    class Meta:
        model = InternalExam
        fields = ("title", "exam_type", "full_marks", "pass_marks", "exam_date", "is_active")

    def validate(self, attrs):
        validate_allocation_is_writable(self.instance.allocation)
        return super().validate(attrs)

    to_representation = updated("Exam")


class InternalExamMarkReadSerializer(serializers.ModelSerializer):
    roll_number = serializers.CharField(source="enrollment.student.roll_number", read_only=True)
    full_name = serializers.CharField(source="enrollment.student.full_name", read_only=True)

    class Meta:
        model = InternalExamMark
        fields = ("id", "enrollment", "roll_number", "full_name", "marks_obtained", "is_absent")


class InternalExamMarkEntrySerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    marks_obtained = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    is_absent = serializers.BooleanField(default=False)


class InternalExamMarkBulkSerializer(RosterEntryMixin, serializers.Serializer):
    """Enter or correct a whole exam's marks in one call."""

    entries = InternalExamMarkEntrySerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        exam: InternalExam = self.context["exam"]
        validate_allocation_is_writable(exam.allocation)

        for entry in attrs["entries"]:
            marks = entry.get("marks_obtained")

            if entry["is_absent"] and marks is not None:
                raise serializers.ValidationError(
                    {"entries": "An absent student cannot be given marks."}
                )
            if marks is not None and marks > exam.full_marks:
                raise serializers.ValidationError(
                    {"entries": f"Marks cannot exceed the full marks of {exam.full_marks}."}
                )
            if marks is not None and marks < 0:
                raise serializers.ValidationError({"entries": "Marks cannot be negative."})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = get_user_by_context(self.context)
        exam: InternalExam = self.context["exam"]
        entries = validated_data["entries"]
        by_id = self.resolve_enrollments(exam.allocation, entries)

        for entry in entries:
            InternalExamMark.objects.update_or_create(
                exam=exam,
                enrollment=by_id[entry["enrollment"]],
                is_archived=False,
                defaults={
                    "marks_obtained": entry.get("marks_obtained"),
                    "is_absent": entry["is_absent"],
                    "updated_by": user,
                },
                create_defaults={
                    "marks_obtained": entry.get("marks_obtained"),
                    "is_absent": entry["is_absent"],
                    "created_by": user,
                },
            )

        return {"count": len(entries)}

    def to_representation(self, instance):
        return {"message": f"{instance['count']} marks saved.", "saved": instance["count"]}


# Assignments
# ------------------------------------------------------------------------------------


class AssignmentListSerializer(serializers.ModelSerializer):
    subject_code = serializers.CharField(source="allocation.subject.code", read_only=True)
    done_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Assignment
        fields = (
            "id",
            "uuid",
            "allocation",
            "subject_code",
            "title",
            "assigned_date",
            "due_date",
            "done_count",
            "is_active",
        )


class AssignmentCreateSerializer(OwnAllocationMixin, AuditedModelSerializer):
    class Meta:
        model = Assignment
        fields = ("allocation", "title", "assigned_date", "due_date")

    to_representation = created("Assignment")


class AssignmentPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Assignment
        fields = ("title", "assigned_date", "due_date", "is_active")

    def validate(self, attrs):
        validate_allocation_is_writable(self.instance.allocation)
        return super().validate(attrs)

    to_representation = updated("Assignment")


class AssignmentSubmissionReadSerializer(serializers.ModelSerializer):
    roll_number = serializers.CharField(source="enrollment.student.roll_number", read_only=True)
    full_name = serializers.CharField(source="enrollment.student.full_name", read_only=True)

    class Meta:
        model = AssignmentSubmission
        fields = ("id", "enrollment", "roll_number", "full_name", "status", "remarks")


class AssignmentSubmissionEntrySerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    status = serializers.ChoiceField(choices=AssignmentStatus.choices())
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class AssignmentSubmissionBulkSerializer(RosterEntryMixin, serializers.Serializer):
    """Set done / partial / not done for a whole class in one call."""

    entries = AssignmentSubmissionEntrySerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        validate_allocation_is_writable(self.context["assignment"].allocation)
        return super().validate(attrs)

    @transaction.atomic
    def create(self, validated_data):
        user = get_user_by_context(self.context)
        assignment: Assignment = self.context["assignment"]
        entries = validated_data["entries"]
        by_id = self.resolve_enrollments(assignment.allocation, entries)

        for entry in entries:
            AssignmentSubmission.objects.update_or_create(
                assignment=assignment,
                enrollment=by_id[entry["enrollment"]],
                is_archived=False,
                defaults={
                    "status": entry["status"],
                    "remarks": entry.get("remarks", ""),
                    "updated_by": user,
                },
                create_defaults={
                    "status": entry["status"],
                    "remarks": entry.get("remarks", ""),
                    "created_by": user,
                },
            )

        return {"count": len(entries)}

    def to_representation(self, instance):
        return {"message": f"{instance['count']} submissions saved.", "saved": instance["count"]}


# Class performance
# ------------------------------------------------------------------------------------


class ClassPerformanceEntrySerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    score = serializers.IntegerField(min_value=1, max_value=10, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class ClassPerformanceBulkSerializer(OwnAllocationMixin, RosterEntryMixin, serializers.Serializer):
    """Save or clear holistic ratings without inventing scores for untouched students."""

    allocation = serializers.PrimaryKeyRelatedField(
        queryset=SubjectAllocation.objects.filter(is_archived=False)
    )
    entries = ClassPerformanceEntrySerializer(many=True, allow_empty=False)

    @transaction.atomic
    def create(self, validated_data):
        user = get_user_by_context(self.context)
        allocation = validated_data["allocation"]
        entries = validated_data["entries"]
        by_id = self.resolve_enrollments(allocation, entries)
        saved = cleared = 0

        for entry in entries:
            enrollment = by_id[entry["enrollment"]]
            if entry["score"] is None:
                rating = ClassPerformanceRating.objects.filter(
                    enrollment=enrollment, is_archived=False
                ).first()
                if rating:
                    rating.is_archived = True
                    rating.updated_by = user
                    rating.save(update_fields=("is_archived", "updated_by", "updated_at"))
                    cleared += 1
                continue

            ClassPerformanceRating.objects.update_or_create(
                enrollment=enrollment,
                is_archived=False,
                defaults={
                    "score": entry["score"],
                    "remarks": entry.get("remarks", ""),
                    "updated_by": user,
                },
                create_defaults={
                    "score": entry["score"],
                    "remarks": entry.get("remarks", ""),
                    "created_by": user,
                },
            )
            saved += 1

        return {"saved": saved, "cleared": cleared}

    def to_representation(self, instance):
        return {
            "message": f"{instance['saved']} class performance ratings saved.",
            **instance,
        }
