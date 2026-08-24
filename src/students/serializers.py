from django.db import transaction
from rest_framework import serializers

# Project Imports
from src.academics.models import BatchSemester, SubjectAllocation
from src.academics.serializers import (
    AuditedModelSerializer,
    BatchBriefSerializer,
    BatchSemesterBriefSerializer,
    SubjectBriefSerializer,
    created,
    updated,
)
from src.libs.get_context import get_user_by_context

from .constants import SemesterEnrollmentStatus
from .models import SemesterEnrollment, Student, SubjectEnrollment


class StudentBriefSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Student
        fields = ("id", "roll_number", "full_name")


# Student
# ------------------------------------------------------------------------------------


class StudentListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    batch = BatchBriefSerializer(read_only=True)

    class Meta:
        model = Student
        fields: tuple[str, ...] = (
            "id",
            "uuid",
            "roll_number",
            "registration_number",
            "full_name",
            "batch",
            "gender",
            "email",
            "phone_no",
            "status",
            "is_active",
        )


class StudentRetrieveSerializer(StudentListSerializer):
    class Meta(StudentListSerializer.Meta):
        fields = (
            *StudentListSerializer.Meta.fields,
            "first_name",
            "middle_name",
            "last_name",
            "date_of_birth",
        )


class StudentCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = Student
        fields = (
            "batch",
            "roll_number",
            "registration_number",
            "first_name",
            "middle_name",
            "last_name",
            "gender",
            "date_of_birth",
            "email",
            "phone_no",
        )

    to_representation = created("Student")


class StudentPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Student
        fields = (
            "roll_number",
            "registration_number",
            "first_name",
            "middle_name",
            "last_name",
            "gender",
            "date_of_birth",
            "email",
            "phone_no",
            "status",
            "is_active",
        )

    to_representation = updated("Student")


# Semester enrollment
# ------------------------------------------------------------------------------------


class SemesterEnrollmentListSerializer(serializers.ModelSerializer):
    student = StudentBriefSerializer(read_only=True)
    batch_semester = BatchSemesterBriefSerializer(read_only=True)

    class Meta:
        model = SemesterEnrollment
        fields = ("id", "uuid", "student", "batch_semester", "status", "is_active")


class SemesterEnrollmentCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = SemesterEnrollment
        fields = ("student", "batch_semester", "status")

    to_representation = created("Enrollment")


class SemesterEnrollmentPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = SemesterEnrollment
        fields = ("status", "is_active")

    to_representation = updated("Enrollment")


class SemesterEnrollmentBulkSerializer(serializers.Serializer):
    """
    Move a group of students into a semester in one call.

    This is how a batch is promoted: the coordinator picks the next
    BatchSemester and the students, and one request writes an enrollment each.
    Students already enrolled are skipped rather than erroring, so the call is
    safe to repeat.
    """

    batch_semester = serializers.PrimaryKeyRelatedField(
        queryset=BatchSemester.objects.filter(is_archived=False)
    )
    students = serializers.PrimaryKeyRelatedField(
        queryset=Student.objects.filter(is_archived=False),
        many=True,
        allow_empty=False,
    )
    status = serializers.ChoiceField(
        choices=SemesterEnrollmentStatus.choices(),
        default=SemesterEnrollmentStatus.ACTIVE.value,
    )

    def validate(self, attrs):
        semester = attrs["batch_semester"]
        wrong_program = [
            student.roll_number
            for student in attrs["students"]
            if student.batch.program_id != semester.batch.program_id
        ]
        if wrong_program:
            raise serializers.ValidationError(
                {
                    "students": (
                        "These students belong to a different program: "
                        f"{', '.join(sorted(wrong_program))}."
                    )
                }
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = get_user_by_context(self.context)
        semester = validated_data["batch_semester"]

        already = set(
            SemesterEnrollment.objects.filter(
                batch_semester=semester, is_archived=False
            ).values_list("student_id", flat=True)
        )

        created_rows = [
            SemesterEnrollment.objects.create(
                student=student,
                batch_semester=semester,
                status=validated_data["status"],
                created_by=user,
            )
            for student in validated_data["students"]
            if student.pk not in already
        ]

        return {
            "created": len(created_rows),
            "skipped": len(validated_data["students"]) - len(created_rows),
        }

    def to_representation(self, instance):
        return {
            "message": f"{instance['created']} students enrolled.",
            "created": instance["created"],
            "skipped": instance["skipped"],
        }


# Subject enrollment
# ------------------------------------------------------------------------------------


class SubjectEnrollmentListSerializer(serializers.ModelSerializer):
    student = StudentBriefSerializer(read_only=True)
    subject = SubjectBriefSerializer(source="allocation.subject", read_only=True)

    class Meta:
        model = SubjectEnrollment
        fields = ("id", "uuid", "student", "allocation", "subject", "is_retake", "is_active")


class SubjectEnrollmentCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = SubjectEnrollment
        fields = ("student", "allocation", "is_retake")

    to_representation = created("Registration")


class SubjectEnrollmentBulkSerializer(serializers.Serializer):
    """
    Register a roster onto one class.

    The usual call after allocating a subject: hand it the allocation and the
    students, and every one of them becomes markable for attendance, exams and
    assignments.
    """

    allocation = serializers.PrimaryKeyRelatedField(
        queryset=SubjectAllocation.objects.filter(is_archived=False)
    )
    students = serializers.PrimaryKeyRelatedField(
        queryset=Student.objects.filter(is_archived=False),
        many=True,
        allow_empty=False,
    )
    is_retake = serializers.BooleanField(default=False)

    def validate(self, attrs):
        allocation = attrs["allocation"]
        wrong_program = [
            student.roll_number
            for student in attrs["students"]
            if student.batch.program_id != allocation.subject.program_id
        ]
        if wrong_program:
            raise serializers.ValidationError(
                {
                    "students": (
                        "These students belong to a different program: "
                        f"{', '.join(sorted(wrong_program))}."
                    )
                }
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = get_user_by_context(self.context)
        allocation = validated_data["allocation"]

        already = set(
            SubjectEnrollment.objects.filter(allocation=allocation, is_archived=False).values_list(
                "student_id", flat=True
            )
        )

        created_rows = [
            SubjectEnrollment.objects.create(
                student=student,
                allocation=allocation,
                is_retake=validated_data["is_retake"],
                created_by=user,
            )
            for student in validated_data["students"]
            if student.pk not in already
        ]

        return {
            "created": len(created_rows),
            "skipped": len(validated_data["students"]) - len(created_rows),
        }

    def to_representation(self, instance):
        return {
            "message": f"{instance['created']} students registered.",
            "created": instance["created"],
            "skipped": instance["skipped"],
        }
