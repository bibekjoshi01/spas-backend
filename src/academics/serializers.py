from rest_framework import serializers

# Project Imports
from src.libs.get_context import get_user_by_context

from .models import (
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
    Teacher,
)


class AuditedModelSerializer(serializers.ModelSerializer):
    """Stamps the acting user on create and update, so no view has to."""

    def create(self, validated_data):
        validated_data["created_by"] = get_user_by_context(self.context)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data["updated_by"] = get_user_by_context(self.context)
        return super().update(instance, validated_data)


def created(name: str):
    def to_representation(self, instance):
        return {"message": f"{name} created successfully.", "id": instance.id}

    return to_representation


def updated(name: str):
    def to_representation(self, instance):
        return {"message": f"{name} updated successfully.", "id": instance.id}

    return to_representation


# Brief serializers — the nested shapes a list row carries so the frontend
# never has to make a second call to render a table.
# ------------------------------------------------------------------------------------


class DepartmentBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ("id", "name", "code")


class ProgramBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Program
        fields = ("id", "name", "code")


class TeacherBriefSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Teacher
        fields = ("id", "full_name", "username", "designation")


class SubjectBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ("id", "code", "name", "semester", "credit_hours")


class BatchBriefSerializer(serializers.ModelSerializer):
    program = ProgramBriefSerializer(read_only=True)

    class Meta:
        model = Batch
        fields = ("id", "year", "program")


class BatchSemesterBriefSerializer(serializers.ModelSerializer):
    batch = BatchBriefSerializer(read_only=True)

    class Meta:
        model = BatchSemester
        fields = ("id", "semester", "status", "batch")


# Department
# ------------------------------------------------------------------------------------


class DepartmentListSerializer(serializers.ModelSerializer):
    program_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Department
        fields = ("id", "uuid", "name", "code", "is_active", "program_count")


class DepartmentCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = Department
        fields = ("name", "code")

    to_representation = created("Department")


class DepartmentPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Department
        fields = ("name", "code", "is_active")

    to_representation = updated("Department")


# Teacher
# ------------------------------------------------------------------------------------


class TeacherListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.CharField(source="user.email", read_only=True)
    department = DepartmentBriefSerializer(read_only=True)

    class Meta:
        model = Teacher
        fields = (
            "id",
            "uuid",
            "full_name",
            "username",
            "email",
            "department",
            "designation",
            "employee_code",
            "is_active",
        )


class TeacherCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = Teacher
        fields = ("user", "department", "employee_code", "designation")

    def validate_user(self, value):
        if Teacher.objects.filter(user=value).exists():
            raise serializers.ValidationError("This user already has a teacher profile.")
        return value

    to_representation = created("Teacher")


class TeacherPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Teacher
        fields = ("department", "employee_code", "designation", "is_active")

    to_representation = updated("Teacher")


# Program
# ------------------------------------------------------------------------------------


class ProgramListSerializer(serializers.ModelSerializer):
    department = DepartmentBriefSerializer(read_only=True)
    coordinator = TeacherBriefSerializer(read_only=True)

    class Meta:
        model = Program
        fields = (
            "id",
            "uuid",
            "name",
            "code",
            "total_semesters",
            "department",
            "coordinator",
            "is_active",
        )


class ProgramCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = Program
        fields = ("department", "name", "code", "total_semesters", "coordinator")

    to_representation = created("Program")


class ProgramPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Program
        fields = ("department", "name", "code", "total_semesters", "coordinator", "is_active")

    to_representation = updated("Program")


# Batch
# ------------------------------------------------------------------------------------


class BatchListSerializer(serializers.ModelSerializer):
    program = ProgramBriefSerializer(read_only=True)
    student_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Batch
        fields = ("id", "uuid", "year", "program", "student_count", "is_active")


class BatchCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = Batch
        fields = ("program", "year")

    to_representation = created("Batch")


class BatchPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Batch
        fields = ("year", "is_active")

    to_representation = updated("Batch")


# Batch semester
# ------------------------------------------------------------------------------------


class BatchSemesterListSerializer(serializers.ModelSerializer):
    batch = BatchBriefSerializer(read_only=True)

    class Meta:
        model = BatchSemester
        fields = (
            "id",
            "uuid",
            "batch",
            "semester",
            "status",
            "start_date",
            "end_date",
            "is_active",
        )


class BatchSemesterCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = BatchSemester
        fields = ("batch", "semester", "status", "start_date", "end_date")

    to_representation = created("Semester")


class BatchSemesterPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = BatchSemester
        fields = ("status", "start_date", "end_date", "is_active")

    to_representation = updated("Semester")


# Subject
# ------------------------------------------------------------------------------------


class SubjectListSerializer(serializers.ModelSerializer):
    program = ProgramBriefSerializer(read_only=True)

    class Meta:
        model = Subject
        fields = (
            "id",
            "uuid",
            "code",
            "name",
            "program",
            "semester",
            "credit_hours",
            "is_elective",
            "is_active",
        )


class SubjectCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = Subject
        fields = ("program", "semester", "code", "name", "credit_hours", "is_elective")

    to_representation = created("Subject")


class SubjectPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Subject
        fields = ("semester", "code", "name", "credit_hours", "is_elective", "is_active")

    to_representation = updated("Subject")


# Subject allocation
# ------------------------------------------------------------------------------------


class SubjectAllocationListSerializer(serializers.ModelSerializer):
    subject = SubjectBriefSerializer(read_only=True)
    teacher = TeacherBriefSerializer(read_only=True)
    batch_semester = BatchSemesterBriefSerializer(read_only=True)
    enrolled_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = SubjectAllocation
        fields = (
            "id",
            "uuid",
            "subject",
            "teacher",
            "batch_semester",
            "enrolled_count",
            "is_active",
        )


class SubjectAllocationCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = SubjectAllocation
        fields = ("batch_semester", "subject", "teacher")

    to_representation = created("Allocation")


class SubjectAllocationPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = SubjectAllocation
        fields = ("teacher", "is_active")

    to_representation = updated("Allocation")
