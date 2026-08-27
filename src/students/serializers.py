from django.db import transaction
from django.utils.text import slugify
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
from src.libs.scoping import has_program_authority
from src.user.models import User, UserRole

from .constants import SemesterEnrollmentStatus
from .models import SemesterEnrollment, Student, SubjectEnrollment


def validate_program_scope(context, program_id: int) -> None:
    if not has_program_authority(get_user_by_context(context), program_id):
        raise serializers.ValidationError("That program is outside your authority.")


def validate_student_identity(attrs, *, instance=None):
    """Validate identifiers shared by student and linked-user records."""
    batch = attrs.get("batch") or (instance.batch if instance else None)
    roll_number = attrs.get("roll_number")
    if roll_number is not None:
        roll_number = roll_number.strip()
        attrs["roll_number"] = roll_number
        if not roll_number:
            raise serializers.ValidationError({"roll_number": "Roll number is required."})
        if roll_number.isdigit() and int(roll_number) == 0:
            raise serializers.ValidationError(
                {"roll_number": "Roll number must be greater than zero."}
            )
        duplicate = Student.objects.filter(
            batch=batch,
            roll_number__iexact=roll_number,
            is_archived=False,
        )
        if instance:
            duplicate = duplicate.exclude(pk=instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError(
                {"roll_number": "That roll number is already registered in this batch."}
            )

    registration_number = attrs.get("registration_number")
    if registration_number:
        registration_number = registration_number.strip()
        attrs["registration_number"] = registration_number
        duplicate = Student.objects.filter(
            registration_number__iexact=registration_number,
            is_archived=False,
        )
        if instance:
            duplicate = duplicate.exclude(pk=instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError(
                {"registration_number": "That registration number is already in use."}
            )

    email = attrs.get("email")
    if email:
        duplicate = User.objects.filter(email__iexact=email, is_archived=False)
        if instance:
            duplicate = duplicate.exclude(pk=instance.user_id)
        if duplicate.exists():
            raise serializers.ValidationError({"email": "That email address is already in use."})

    return attrs


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

    def validate(self, attrs):
        validate_program_scope(self.context, attrs["batch"].program_id)
        return validate_student_identity(attrs)

    @transaction.atomic
    def create(self, validated_data):
        actor = get_user_by_context(self.context)
        base_username = (
            slugify(
                validated_data.get("registration_number")
                or f"student-{validated_data['batch'].id}-{validated_data['roll_number']}"
            )[:24]
            or "student"
        )
        username = base_username
        suffix = 1
        while User.objects.filter(username__iexact=username).exists():
            suffix += 1
            username = f"{base_username[: 29 - len(str(suffix))]}-{suffix}"

        email = validated_data.get("email") or f"{username}@student.local"
        user = User.objects.create_user(
            username=username,
            email=email,
            password=None,
            first_name=validated_data["first_name"],
            middle_name=validated_data.get("middle_name", ""),
            last_name=validated_data["last_name"],
            full_name=" ".join(
                part
                for part in (
                    validated_data["first_name"],
                    validated_data.get("middle_name", ""),
                    validated_data["last_name"],
                )
                if part
            ),
            created_by=actor,
            include_system_role=False,
        )
        student_role = UserRole.objects.filter(codename="STUDENT").first()
        if student_role:
            user.roles.add(student_role)

        validated_data["user"] = user
        return super().create(validated_data)

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

    def validate(self, attrs):
        return validate_student_identity(attrs, instance=self.instance)

    @transaction.atomic
    def update(self, instance, validated_data):
        student = super().update(instance, validated_data)
        user = student.user
        user.first_name = student.first_name
        user.middle_name = student.middle_name
        user.last_name = student.last_name
        user.full_name = student.full_name
        if student.email:
            user.email = student.email
        user.save(update_fields=("first_name", "middle_name", "last_name", "full_name", "email"))
        return student

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

    def validate(self, attrs):
        validate_program_scope(self.context, attrs["batch_semester"].batch.program_id)
        return super().validate(attrs)

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
        validate_program_scope(self.context, semester.batch.program_id)
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

    def validate(self, attrs):
        validate_program_scope(self.context, attrs["allocation"].subject.program_id)
        return super().validate(attrs)

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
        validate_program_scope(self.context, allocation.subject.program_id)
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
