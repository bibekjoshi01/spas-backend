"""Bulk student intake from the sheet a college already keeps."""

from typing import ClassVar

from src.libs.imports import SpreadsheetImporter

from .models import Student
from .serializers import StudentCreateSerializer, StudentPatchSerializer

TEMPLATE_EXAMPLE = {
    "roll_number": "042",
    "registration_number": "2081-1-3-042",
    "first_name": "Ramesh",
    "middle_name": "",
    "last_name": "Thapa",
    "gender": "MALE",
    "date_of_birth": "2005-04-17",
    "email": "ramesh.thapa@example.edu",
    "phone_no": "9800000042",
    "alternate_phone_no": "",
}


class StudentImporter(SpreadsheetImporter):
    """
    One sheet, one batch.

    The batch is chosen in the request rather than named in a column: a college
    keeps one sheet per intake anyway, and it means the caller's authority over
    that batch is settled once instead of re-derived per row.
    """

    columns: ClassVar[dict[str, bool]] = {
        "roll_number": True,
        "first_name": True,
        "middle_name": False,
        "last_name": True,
        "registration_number": False,
        "gender": False,
        "date_of_birth": False,
        "email": False,
        "phone_no": False,
        "alternate_phone_no": False,
    }
    identity_columns: ClassVar[tuple[str, ...]] = ("roll_number",)

    def __init__(self, context: dict, batch):
        super().__init__(context)
        self.batch = batch

    def identity_of(self, values: dict[str, str]) -> str:
        roll = values.get("roll_number", "")
        name = " ".join(
            part for part in (values.get("first_name"), values.get("last_name")) if part
        )
        return f"{roll} — {name}".strip(" —") if name else roll

    def find_existing(self, values: dict[str, str]):
        roll = values.get("roll_number", "").strip()
        if not roll:
            return None
        return Student.objects.filter(
            batch=self.batch, roll_number__iexact=roll, is_archived=False
        ).first()

    def create_serializer(self, payload: dict):
        return StudentCreateSerializer(
            data={**payload, "batch": self.batch.pk}, context=self.context
        )

    def patch_serializer(self, instance, payload: dict):
        return StudentPatchSerializer(instance, data=payload, partial=True, context=self.context)
