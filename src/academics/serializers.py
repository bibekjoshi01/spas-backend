import re

# Project Imports
from rest_framework import serializers

from src.libs.get_context import get_user_by_context
from src.libs.scoping import (
    has_department_authority,
    has_program_authority,
    management_scope,
)
from src.user.models import User, UserRole

from .calendar import MAX_BS_YEAR, MIN_BS_YEAR, to_bs_string
from .constants import HEX_COLOR_PATTERN, Weekday
from .models import (
    AcademicCalendarConfiguration,
    AcademicCalendarEntry,
    Batch,
    BatchSemester,
    ClassMeeting,
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


def validate_calendar_date(value):
    """
    A date the Bikram Sambat table can actually reach.

    Storage is Gregorian, but every reading of a date converts it, and the
    shipped conversion table stops at both ends. Refusing the date here gives a
    field error rather than a 500 the first time somebody types 1823.
    """
    try:
        to_bs_string(value)
    except (ValueError, KeyError, IndexError, OverflowError) as error:
        raise serializers.ValidationError(
            f"That date is outside the Nepali calendar this system covers "
            f"(BS {MIN_BS_YEAR}-{MAX_BS_YEAR})."
        ) from error
    return value


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


class ClassMeetingSerializer(serializers.ModelSerializer):
    """One weekly slot, both on the way in and on the way out."""

    class Meta:
        model = ClassMeeting
        fields = ("id", "weekday", "start_time", "end_time")
        read_only_fields = ("id",)

    def validate(self, attrs):
        start, end = attrs.get("start_time"), attrs.get("end_time")
        if bool(start) != bool(end):
            raise serializers.ValidationError(
                "Provide both start and end time, or leave both empty."
            )
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "End time must be after start time."})
        return attrs


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
        fields = (
            "id",
            "uuid",
            "year",
            "program",
            "student_count",
            "status",
            "graduated_on",
            "is_active",
        )


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
        # Status is not edited here. Graduating a cohort changes rows outside
        # this one, so it is a named action with its own endpoint rather than a
        # field anyone can set in passing.
        fields = ("year", "is_active")

    to_representation = updated("Batch")


class BatchGraduationPreviewSerializer(serializers.Serializer):
    """What graduating this batch is about to change, before it changes it."""

    batch = serializers.CharField()
    semesters_total = serializers.IntegerField()
    semesters_completed = serializers.IntegerField()
    can_graduate = serializers.BooleanField()
    blocker = serializers.CharField(allow_null=True)
    students_total = serializers.IntegerField()
    students_to_graduate = serializers.IntegerField()
    students_already_left = serializers.IntegerField()


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
        validate_selectable(value.program, "program")
        validate_selectable(value, "batch")
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
    meetings = ClassMeetingSerializer(many=True, read_only=True)

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
            "meetings",
            "enrolled_count",
            "is_active",
        )


class MeetingWriteMixin:
    """
    Replaces a class's timetable wholesale when `meetings` is supplied.

    The editor always sends the full week it is showing, so a slot the user
    removed has to disappear. Leaving the key out entirely means "not editing
    the timetable" and keeps whatever is already there, which is what a PATCH
    of only the teacher must not disturb.
    """

    def _write_meetings(self, allocation, meetings, actor):
        seen = set()
        for row in meetings:
            key = (row["weekday"], row.get("start_time"))
            if key in seen:
                raise serializers.ValidationError(
                    {"meetings": "That day already has a slot starting at the same time."}
                )
            seen.add(key)

        allocation.meetings.all().delete()
        ClassMeeting.objects.bulk_create(
            [
                ClassMeeting(
                    allocation=allocation,
                    weekday=row["weekday"],
                    start_time=row.get("start_time"),
                    end_time=row.get("end_time"),
                    created_by=actor,
                )
                for row in meetings
            ]
        )


class SubjectAllocationCreateSerializer(MeetingWriteMixin, AuditedModelSerializer):
    meetings = ClassMeetingSerializer(many=True, required=False)

    class Meta:
        model = SubjectAllocation
        fields = (
            "batch_semester",
            "subject",
            "teacher",
            "start_time",
            "end_time",
            "meetings",
        )

    def create(self, validated_data):
        meetings = validated_data.pop("meetings", None)
        allocation = super().create(validated_data)
        if meetings is not None:
            self._write_meetings(allocation, meetings, allocation.created_by)
        return allocation

    def validate_teacher(self, value):
        if not value.is_active or value.is_archived:
            raise serializers.ValidationError("Choose an active teacher account.")
        if not value.roles.filter(codename="TEACHER").exists():
            raise serializers.ValidationError("Assign the Teacher role first.")
        validate_teacher_scope(self.context, value)
        return value

    def validate(self, attrs):
        validate_program_scope(self.context, attrs["subject"].program)
        validate_program_scope(self.context, attrs["batch_semester"].batch.program)
        validate_selectable(attrs["subject"].program, "program")
        validate_selectable(attrs["batch_semester"].batch.program, "program")
        validate_selectable(attrs["batch_semester"].batch, "batch")
        validate_selectable(attrs["batch_semester"], "semester")
        validate_selectable(attrs["subject"], "subject")
        return super().validate(attrs)

    to_representation = created("Allocation")


class SubjectAllocationPatchSerializer(MeetingWriteMixin, AuditedModelSerializer):
    meetings = ClassMeetingSerializer(many=True, required=False)

    class Meta:
        model = SubjectAllocation
        fields = (
            "batch_semester",
            "subject",
            "teacher",
            "start_time",
            "end_time",
            "meetings",
            "is_active",
        )

    def update(self, instance, validated_data):
        meetings = validated_data.pop("meetings", None)
        allocation = super().update(instance, validated_data)
        if meetings is not None:
            self._write_meetings(allocation, meetings, get_user_by_context(self.context))
        return allocation

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
            relation.exists()
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
        if not value.is_active or value.is_archived:
            raise serializers.ValidationError("Choose an active teacher account.")
        if not value.roles.filter(codename="TEACHER").exists():
            raise serializers.ValidationError("Assign the Teacher role first.")
        validate_teacher_scope(self.context, value)
        return value

    to_representation = updated("Allocation")


# Academic calendar
# ------------------------------------------------------------------------------------


def normalise_hex_color(value: str) -> str:
    """
    `#1D4ED8` and `#1d4ed8` are the same colour; store one of them.

    A colour input emits lower case and a person typing emits either, so the
    value is folded here rather than leaving two spellings of one brand colour
    in the table.
    """
    text = (value or "").strip()
    if not re.fullmatch(HEX_COLOR_PATTERN, text):
        raise serializers.ValidationError(
            "Give the colour as a six-digit hex value, for example #1d4ed8."
        )
    return text.lower()


class AcademicCalendarConfigurationSerializer(AuditedModelSerializer):
    """Which weekdays the college does not teach on, and how it paints them."""

    class Meta:
        model = AcademicCalendarConfiguration
        fields = (
            "weekend_days",
            "theme_accent_color",
            "theme_holiday_color",
            "theme_event_color",
            "show_gregorian_dates",
        )

    def validate_theme_accent_color(self, value):
        return normalise_hex_color(value)

    def validate_theme_holiday_color(self, value):
        return normalise_hex_color(value)

    def validate_theme_event_color(self, value):
        return normalise_hex_color(value)

    def validate_weekend_days(self, value):
        days = list(value or [])
        if len(set(days)) != len(days):
            raise serializers.ValidationError("A weekday may only be listed once.")
        unknown = sorted(set(days) - set(Weekday.values))
        if unknown:
            raise serializers.ValidationError("That is not a day of the week.")
        if len(days) >= len(Weekday.values):
            raise serializers.ValidationError(
                "At least one day of the week must remain a teaching day."
            )
        return sorted(days)


class AcademicCalendarEntryListSerializer(serializers.ModelSerializer):
    """One marked date, with the Bikram Sambat reading of it alongside."""

    nepali_date = serializers.SerializerMethodField()

    class Meta:
        model = AcademicCalendarEntry
        fields = (
            "id",
            "uuid",
            "date",
            "nepali_date",
            "kind",
            "title",
            "note",
            "is_active",
        )

    def get_nepali_date(self, obj) -> str:
        return to_bs_string(obj.date)


class AcademicCalendarEntryCreateSerializer(AuditedModelSerializer):
    class Meta:
        model = AcademicCalendarEntry
        fields = ("date", "kind", "title", "note")

    def validate_date(self, value):
        return validate_calendar_date(value)

    to_representation = created("Calendar entry")


class AcademicCalendarEntryPatchSerializer(AuditedModelSerializer):
    class Meta:
        model = AcademicCalendarEntry
        fields = ("date", "kind", "title", "note", "is_active")

    def validate_date(self, value):
        return validate_calendar_date(value)

    to_representation = updated("Calendar entry")


class CalendarMilestoneSerializer(serializers.Serializer):
    key = serializers.CharField()
    kind = serializers.CharField()
    title = serializers.CharField()


class CalendarDaySerializer(serializers.Serializer):
    """One cell of the grid. Documented for the schema; built by the view."""

    date = serializers.DateField()
    day = serializers.IntegerField()
    day_label = serializers.CharField()
    weekday = serializers.IntegerField()
    is_weekend = serializers.BooleanField()
    entries = AcademicCalendarEntryListSerializer(many=True)
    milestones = CalendarMilestoneSerializer(many=True, read_only=True)


class CalendarMonthSerializer(serializers.Serializer):
    index = serializers.IntegerField()
    name = serializers.CharField()
    name_nepali = serializers.CharField()
    days = CalendarDaySerializer(many=True)


class CalendarThemeSerializer(serializers.Serializer):
    """The palette the college paints its calendar in."""

    accent_color = serializers.CharField()
    holiday_color = serializers.CharField()
    event_color = serializers.CharField()
    show_gregorian_dates = serializers.BooleanField()


class CalendarYearSerializer(serializers.Serializer):
    """A whole year, ready to draw."""

    theme = CalendarThemeSerializer()
    system = serializers.CharField()
    year = serializers.IntegerField()
    min_year = serializers.IntegerField()
    max_year = serializers.IntegerField()
    weekend_days = serializers.ListField(child=serializers.IntegerField())
    months = CalendarMonthSerializer(many=True)
