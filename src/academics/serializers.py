# Project Imports
from rest_framework import serializers

from src.libs.get_context import get_user_by_context
from src.libs.scoping import (
    has_department_authority,
    has_program_authority,
    management_scope,
)
from src.user.models import User, UserRole

from .models import (
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
)


class AuditedModelSerializer(serializers.ModelSerializer):
    """Stamps the acting user on create and update, so no view has to."""

    def create(self, validated_data):
        validated_data["created_by"] = get_user_by_context(self.context)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data["updated_by"] = get_user_by_context(self.context)
        return super().update(instance, validated_data)


def validate_department_scope(context, department) -> None:
    if not has_department_authority(get_user_by_context(context), department.id):
        raise serializers.ValidationError("That department is outside your authority.")


def validate_program_scope(context, program) -> None:
    if not has_program_authority(get_user_by_context(context), program.id):
        raise serializers.ValidationError("That program is outside your authority.")


def validate_selectable(value, noun: str) -> None:
    """
    Refuses a parent that has been switched off.

    Deactivating a department, program or subject is how the college retires it
    without losing what hangs off it, so the pickers stop offering it. The same
    rule is enforced here because a stale form or a direct call would otherwise
    walk straight past the filtered list.
    """
    if not value.is_active:
        raise serializers.ValidationError(
            f"{value} is inactive. Reactivate the {noun} before using it here."
        )


def validate_teacher_scope(context, teacher) -> None:
    user = get_user_by_context(context)
    scope = management_scope(user)
    if scope.unlimited:
        return
    visible = teacher.allocations.filter(
        subject__program_id__in=scope.program_ids,
        is_archived=False,
    ).exists()
    authority = teacher.headed_departments.filter(id__in=scope.department_ids).exists() or (
        teacher.coordinated_programs.filter(id__in=scope.program_ids).exists()
    )
    if not (visible or authority):
        raise serializers.ValidationError(
            "That teacher is outside your authority. An administrator must make their first allocation."
        )


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


class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "full_name", "username")


def sync_authority_role(*, current_user, previous_user, codename: str) -> None:
    """Keep authority roles aligned with the live department/program assignment."""
    role = UserRole.objects.filter(codename=codename).first()
    if role is None:
        return

    if current_user and not current_user.is_superuser:
        current_user.roles.add(role)

    if previous_user and previous_user != current_user and not previous_user.is_superuser:
        still_assigned = (
            Department.objects.filter(head=previous_user, is_archived=False).exists()
            if codename == "DEPARTMENT-HEAD"
            else Program.objects.filter(coordinator=previous_user, is_archived=False).exists()
        )
        if not still_assigned:
            previous_user.roles.remove(role)


def validate_authority_user(value):
    if value and (not value.is_active or value.roles.filter(codename="STUDENT").exists()):
        raise serializers.ValidationError("Choose an active staff account.")
    return value


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
    head = UserBriefSerializer(read_only=True)

    class Meta:
        model = Department
        fields = ("id", "uuid", "name", "code", "head", "is_active", "program_count")


class DepartmentCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = Department
        fields = ("name", "code", "head")

    validate_head = staticmethod(validate_authority_user)

    def create(self, validated_data):
        department = super().create(validated_data)
        sync_authority_role(
            current_user=department.head,
            previous_user=None,
            codename="DEPARTMENT-HEAD",
        )
        return department

    to_representation = created("Department")


class DepartmentPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Department
        fields = ("name", "code", "head", "is_active")

    validate_head = staticmethod(validate_authority_user)

    def update(self, instance, validated_data):
        previous_head = instance.head
        department = super().update(instance, validated_data)
        sync_authority_role(
            current_user=department.head,
            previous_user=previous_head,
            codename="DEPARTMENT-HEAD",
        )
        return department

    to_representation = updated("Department")


# Program
# ------------------------------------------------------------------------------------


class ProgramListSerializer(serializers.ModelSerializer):
    department = DepartmentBriefSerializer(read_only=True)
    coordinator = UserBriefSerializer(read_only=True)

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

    validate_coordinator = staticmethod(validate_authority_user)

    def validate_department(self, value):
        validate_department_scope(self.context, value)
        validate_selectable(value, "department")
        return value

    def create(self, validated_data):
        program = super().create(validated_data)
        sync_authority_role(
            current_user=program.coordinator,
            previous_user=None,
            codename="PROGRAM-COORDINATOR",
        )
        return program

    to_representation = created("Program")


class ProgramPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = Program
        fields = ("department", "name", "code", "total_semesters", "coordinator", "is_active")

    validate_coordinator = staticmethod(validate_authority_user)

    def validate_department(self, value):
        validate_department_scope(self.context, value)
        # Only a move *into* a retired department is refused. An edit form
        # resubmits the department it loaded, so a program already sitting under
        # a retired one stays editable and can be moved out.
        if value != self.instance.department:
            validate_selectable(value, "department")
        return value

    def update(self, instance, validated_data):
        previous_coordinator = instance.coordinator
        program = super().update(instance, validated_data)
        sync_authority_role(
            current_user=program.coordinator,
            previous_user=previous_coordinator,
            codename="PROGRAM-COORDINATOR",
        )
        return program

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

    def validate_program(self, value):
        validate_program_scope(self.context, value)
        validate_selectable(value, "program")
        return value

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

    def validate_batch(self, value):
        validate_program_scope(self.context, value.program)
        return value

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

    def validate_program(self, value):
        validate_program_scope(self.context, value)
        validate_selectable(value, "program")
        return value

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
    teacher = UserBriefSerializer(read_only=True)
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
            "start_time",
            "end_time",
            "enrolled_count",
            "is_active",
        )


class SubjectAllocationCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = SubjectAllocation
        fields = ("batch_semester", "subject", "teacher", "start_time", "end_time")

    def validate_teacher(self, value):
        if not value.roles.filter(codename="TEACHER").exists():
            raise serializers.ValidationError("Assign the Teacher role first.")
        validate_teacher_scope(self.context, value)
        return value

    def validate(self, attrs):
        validate_program_scope(self.context, attrs["subject"].program)
        validate_program_scope(self.context, attrs["batch_semester"].batch.program)
        validate_selectable(attrs["subject"], "subject")
        return super().validate(attrs)

    to_representation = created("Allocation")


class SubjectAllocationPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = SubjectAllocation
        fields = (
            "batch_semester",
            "subject",
            "teacher",
            "start_time",
            "end_time",
            "is_active",
        )

    def validate(self, attrs):
        subject = attrs.get("subject", self.instance.subject)
        batch_semester = attrs.get("batch_semester", self.instance.batch_semester)
        validate_program_scope(self.context, subject.program)
        validate_program_scope(self.context, batch_semester.batch.program)
        # Only a move to a different subject is refused. The edit form
        # resubmits the subject it loaded, so an allocation whose subject has
        # since been retired stays editable.
        if subject != self.instance.subject:
            validate_selectable(subject, "subject")
        changing_class_identity = (
            "batch_semester" in attrs and attrs["batch_semester"] != self.instance.batch_semester
        ) or ("subject" in attrs and attrs["subject"] != self.instance.subject)
        has_records = any(
            relation.filter(is_archived=False).exists()
            for relation in (
                self.instance.enrollments,
                self.instance.attendance_sessions,
                self.instance.internal_exams,
                self.instance.assignments,
            )
        )
        if changing_class_identity and has_records:
            raise serializers.ValidationError(
                "Batch semester and subject cannot change after roster or performance records exist."
            )
        return attrs

    def validate_teacher(self, value):
        if not value.roles.filter(codename="TEACHER").exists():
            raise serializers.ValidationError("Assign the Teacher role first.")
        validate_teacher_scope(self.context, value)
        return value

    to_representation = updated("Allocation")
