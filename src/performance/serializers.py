from django.db import transaction
from rest_framework import serializers

# Project Imports
from src.academics.models import SubjectAllocation
from src.academics.serializers import AuditedModelSerializer, created, updated
from src.libs.get_context import get_user_by_context
from src.students.models import SubjectEnrollment

from .constants import AssignmentStatus, AttendanceStatus
from .models import (
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    InternalExam,
    InternalExamMark,
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


class AttendanceSessionCreateSerializer(RosterEntryMixin, serializers.Serializer):
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

    @transaction.atomic
    def create(self, validated_data):
        user = get_user_by_context(self.context)
        allocation = validated_data["allocation"]
        entries = validated_data["entries"]
        by_id = self.resolve_enrollments(allocation, entries)

        session, _ = AttendanceSession.objects.get_or_create(
            allocation=allocation,
            date=validated_data["date"],
            period=validated_data["period"],
            is_archived=False,
            defaults={"created_by": user},
        )

        for entry in entries:
            AttendanceRecord.objects.update_or_create(
                session=session,
                enrollment=by_id[entry["enrollment"]],
                is_archived=False,
                defaults={"status": entry["status"], "created_by": user},
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


class InternalExamCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = InternalExam
        fields = ("allocation", "title", "exam_type", "full_marks", "pass_marks", "exam_date")

    to_representation = created("Exam")


class InternalExamPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = InternalExam
        fields = ("title", "exam_type", "full_marks", "pass_marks", "exam_date", "is_active")

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


class AssignmentCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = Assignment
        fields = ("allocation", "title", "assigned_date", "due_date")

    to_representation = created("Assignment")


class AssignmentPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Assignment
        fields = ("title", "assigned_date", "due_date", "is_active")

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
                    "created_by": user,
                },
            )

        return {"count": len(entries)}

    def to_representation(self, instance):
        return {"message": f"{instance['count']} submissions saved.", "saved": instance["count"]}
