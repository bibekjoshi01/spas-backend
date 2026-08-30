"""Bulk curriculum intake: a program's subjects from one sheet."""

from typing import ClassVar

from src.libs.imports import SpreadsheetImporter

from .models import Subject
from .serializers import SubjectCreateSerializer, SubjectPatchSerializer

TEMPLATE_EXAMPLE = {
    "code": "CSC 201",
    "name": "Data Structures and Algorithms",
    "semester": "3",
    "credit_hours": "3",
    "is_elective": "false",
}


class SubjectImporter(SpreadsheetImporter):
    """
    One sheet, one program, every semester of its curriculum.

    A subject's identity is its code within a semester, matching the uniqueness
    the model enforces: a syllabus revision that moves a subject to a different
    semester is a new row, not an edit of the old one.
    """

    columns: ClassVar[dict[str, bool]] = {
        "code": True,
        "name": True,
        "semester": True,
        "credit_hours": False,
        "is_elective": False,
    }
    identity_columns: ClassVar[tuple[str, ...]] = ("code", "semester")

    def __init__(self, context: dict, program):
        super().__init__(context)
        self.program = program

    def identity_of(self, values: dict[str, str]) -> str:
        code = values.get("code", "")
        semester = values.get("semester", "")
        return f"{code} (semester {semester})" if semester else code

    def find_existing(self, values: dict[str, str]):
        code = values.get("code", "").strip()
        try:
            semester = int(values.get("semester", ""))
        except (TypeError, ValueError):
            return None
        if not code:
            return None
        return Subject.objects.filter(
            program=self.program, code__iexact=code, semester=semester, is_archived=False
        ).first()

    def create_serializer(self, payload: dict):
        return SubjectCreateSerializer(
            data={**payload, "program": self.program.pk}, context=self.context
        )

    def patch_serializer(self, instance, payload: dict):
        return SubjectPatchSerializer(instance, data=payload, partial=True, context=self.context)
