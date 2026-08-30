"""
Spreadsheet import: parse, validate every row, report, then write.

Onboarding a college means entering hundreds of students and a whole
curriculum, so both are importable from the sheet the college already keeps.

The flow is deliberately two-step. An upload validates and reports what it
would do; only an explicit second call writes, and it writes nothing unless
every row is clean. Discovering a bad 800-row sheet afterwards is exactly the
outcome this is built to avoid.
"""

import csv
import datetime
import io
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from django.db import transaction
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from src.libs.permissions import get_role_permissions

# A sheet larger than this is a mistake or an attack, not a college intake.
MAX_ROWS = 2000
MAX_FILE_BYTES = 5 * 1024 * 1024

CSV_SUFFIXES = (".csv",)
EXCEL_SUFFIXES = (".xlsx", ".xlsm")


def _canonical(header: str) -> str:
    """
    Reduce a heading to a comparable key.

    "Roll Number", "roll_number" and "rollNumber" are the same column as far as
    a college secretary is concerned, so they are the same column here.
    """
    return re.sub(r"[^a-z0-9]", "", str(header or "").lower())


def _cell(value: Any) -> str:
    """One spreadsheet cell as the trimmed text a serializer can parse."""
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        # Excel hands back 3.0 for a credit-hours column typed as 3.
        return str(int(value))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _read_csv(raw: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 decodes any byte string
        raise serializers.ValidationError({"file": "That file's text encoding is unreadable."})

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


def _read_excel(uploaded) -> list[list[Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - dependency is declared
        raise serializers.ValidationError(
            {"file": "Excel import is unavailable on this server. Upload a CSV instead."}
        ) from None

    try:
        workbook = load_workbook(uploaded, read_only=True, data_only=True)
    except Exception:
        raise serializers.ValidationError(
            {"file": "That file could not be read as a spreadsheet."}
        ) from None

    sheet = workbook.active
    if sheet is None:
        raise serializers.ValidationError({"file": "That workbook has no sheets."})
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def read_table(uploaded) -> tuple[dict[str, str], list[tuple[int, dict[str, str]]]]:
    """
    A spreadsheet as its headings plus numbered, non-empty rows.

    Headings come back as a canonical key to the spelling the college used, so
    a message can quote their own column name back at them. Row numbers are the
    ones they see in their file, so an error can be acted on without counting.
    """
    name = (getattr(uploaded, "name", "") or "").lower()
    if getattr(uploaded, "size", 0) > MAX_FILE_BYTES:
        raise serializers.ValidationError({"file": "That file is larger than 5 MB."})

    if name.endswith(CSV_SUFFIXES):
        table = _read_csv(uploaded.read())
    elif name.endswith(EXCEL_SUFFIXES):
        table = _read_excel(uploaded)
    else:
        raise serializers.ValidationError({"file": "Upload a .csv or .xlsx file."})

    table = [row for row in table if any(_cell(cell) for cell in row)]
    if not table:
        raise serializers.ValidationError({"file": "That file has no rows."})

    header = [_canonical(cell) for cell in table[0]]
    headings = {
        key: str(table[0][index]).strip()
        for index, key in enumerate(header)
        if key and key not in header[:index]
    }
    body = table[1:]
    if len(body) > MAX_ROWS:
        raise serializers.ValidationError(
            {"file": f"That file has more than {MAX_ROWS} rows. Split it and import in parts."}
        )

    rows: list[tuple[int, dict[str, str]]] = []
    for offset, raw in enumerate(body):
        values = {
            column: _cell(raw[index]) if index < len(raw) else ""
            for index, column in enumerate(header)
            if column
        }
        # +2 skips the header and counts from one, matching the spreadsheet.
        rows.append((offset + 2, values))

    return headings, rows


@dataclass
class RowOutcome:
    number: int
    action: str  # "create" | "update" | "error"
    identity: str
    errors: dict | None = None
    changes: list[str] = field(default_factory=list)


class SpreadsheetImporter:
    """
    Import one kind of record from a sheet, reusing that record's serializers.

    Every row goes through the same create and patch serializers the API uses,
    so an import cannot enter something the API would have rejected, and gets
    audit fields, linked-user handling and history for free.
    """

    #: Canonical column -> required. Order is the template's column order.
    columns: ClassVar[dict[str, bool]] = {}
    #: Columns whose value identifies an existing row.
    identity_columns: ClassVar[tuple[str, ...]] = ()

    def __init__(self, context: dict):
        self.context = context

    # Hooks -----------------------------------------------------------------

    def identity_key(self, values: dict[str, str]) -> str:
        """
        What makes two rows the same record.

        Kept separate from the label shown to the reader: two rows naming the
        same roll number with different names are still the same student, and
        deduplicating on a display string would let one of them through.
        """
        return "\u0000".join(
            values.get(column, "").strip().casefold() for column in self.identity_columns
        )

    def identity_of(self, values: dict[str, str]) -> str:
        """How this row is named back to the reader."""
        return " ".join(values.get(column, "") for column in self.identity_columns).strip()

    def find_existing(self, values: dict[str, str]):
        raise NotImplementedError

    def payload(self, values: dict[str, str]) -> dict:
        """
        The serializer payload for one row.

        Blank cells are omitted rather than sent as "", so importing a sheet
        that leaves a column empty never erases what is already recorded.
        """
        return {column: values[column] for column in self.columns if values.get(column)}

    def create_serializer(self, payload: dict):
        raise NotImplementedError

    def patch_serializer(self, instance, payload: dict):
        raise NotImplementedError

    # Engine ----------------------------------------------------------------

    def run(self, uploaded, *, commit: bool) -> dict:
        headings, rows = read_table(uploaded)

        # Headings are matched on letters and digits alone, so "Roll Number",
        # "roll_number" and "rollNumber" all reach the same field.
        alias = {_canonical(column): column for column in self.columns}
        recognised = [alias[key] for key in headings if key in alias]
        missing = [
            column
            for column, required in self.columns.items()
            if required and _canonical(column) not in headings
        ]
        if missing:
            raise serializers.ValidationError(
                {"file": f"That sheet is missing required columns: {', '.join(missing)}."}
            )

        outcomes: list[RowOutcome] = []
        pending: list[tuple[RowOutcome, Any]] = []
        seen: dict[str, int] = {}

        for number, raw in rows:
            values = {alias[key]: value for key, value in raw.items() if key in alias}
            identity = self.identity_of(values)
            key = self.identity_key(values)

            # A sheet that repeats an identity would otherwise have its last row
            # silently win, which is indistinguishable from losing a student.
            if key.strip("\u0000") and key in seen:
                outcomes.append(
                    RowOutcome(
                        number=number,
                        action="error",
                        identity=identity,
                        errors={"__all__": [f"Repeats row {seen[key]} of this file."]},
                    )
                )
                continue
            if key.strip("\u0000"):
                seen[key] = number

            payload = self.payload(values)
            instance = self.find_existing(values)
            serializer = (
                self.patch_serializer(instance, payload)
                if instance
                else self.create_serializer(payload)
            )

            if not serializer.is_valid():
                outcomes.append(
                    RowOutcome(
                        number=number,
                        action="error",
                        identity=identity,
                        errors=serializer.errors,
                    )
                )
                continue

            outcome = RowOutcome(
                number=number,
                action="update" if instance else "create",
                identity=identity,
                changes=self._changes(instance, serializer.validated_data) if instance else [],
            )
            outcomes.append(outcome)
            pending.append((outcome, serializer))

        summary = {
            "total": len(outcomes),
            "create": sum(1 for row in outcomes if row.action == "create"),
            "update": sum(1 for row in outcomes if row.action == "update"),
            "error": sum(1 for row in outcomes if row.action == "error"),
        }

        committed = False
        if commit:
            if summary["error"]:
                raise serializers.ValidationError(
                    {
                        "file": (
                            f"{summary['error']} of {summary['total']} rows have errors. "
                            "Nothing was imported. Fix them and upload again."
                        ),
                        "rows": self._serialise(outcomes),
                        "summary": summary,
                    }
                )
            with transaction.atomic():
                for _outcome, serializer in pending:
                    serializer.save()
            committed = True

        return {
            "committed": committed,
            "summary": summary,
            "columns": {
                "recognised": recognised,
                "ignored": sorted(
                    spelling for key, spelling in headings.items() if key not in alias
                ),
            },
            "rows": self._serialise(outcomes),
        }

    @staticmethod
    def _changes(instance, validated: dict) -> list[str]:
        """Fields whose value the sheet would actually alter."""
        changed = []
        for field_name, value in validated.items():
            if not hasattr(instance, field_name):
                continue
            if getattr(instance, field_name) != value:
                changed.append(field_name)
        return sorted(changed)

    @staticmethod
    def _serialise(outcomes: list[RowOutcome]) -> list[dict]:
        return [
            {
                "row": outcome.number,
                "action": outcome.action,
                "identity": outcome.identity,
                "errors": outcome.errors,
                "changes": outcome.changes,
            }
            for outcome in outcomes
        ]

    @classmethod
    def template_csv(cls, example: dict[str, str]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(list(cls.columns))
        writer.writerow([example.get(column, "") for column in cls.columns])
        return buffer.getvalue()


TRUTHY = {"true", "1", "yes", "on"}


class ImportPermission(BasePermission):
    """
    An import both creates and updates, so it requires both rights.

    Asking only for `add_` would let someone who may not edit records rewrite
    them wholesale by re-uploading a sheet.
    """

    resource: ClassVar[str] = ""

    def has_permission(self, request: Any, view: Any) -> bool:
        user = getattr(request, "user", None)
        if user is None or user.is_anonymous or not user.is_active:
            return False
        if user.is_superuser:
            return True
        held = set(get_role_permissions(request))
        return {f"add_{self.resource}", f"edit_{self.resource}"} <= held


class SpreadsheetImportView(APIView):
    """
    Upload a sheet: report by default, write only when told to.

    `POST` with a file answers with what the import would do, row by row, and
    writes nothing. `POST` with `commit=true` revalidates and, only if every
    row is clean, writes them in one transaction. `GET` hands back a template
    with the column headings and one example row.
    """

    importer_class: ClassVar[type[SpreadsheetImporter] | None] = None
    template_example: ClassVar[dict[str, str]] = {}
    template_filename: ClassVar[str] = "import-template.csv"

    def build_importer(self, request: Any) -> SpreadsheetImporter:
        raise NotImplementedError

    def get(self, request: Any, *args: Any, **kwargs: Any) -> HttpResponse:
        assert self.importer_class is not None
        response = HttpResponse(
            self.importer_class.template_csv(self.template_example),
            content_type="text/csv",
        )
        response["Content-Disposition"] = f'attachment; filename="{self.template_filename}"'
        return response

    def post(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise serializers.ValidationError({"file": "Attach a .csv or .xlsx file."})

        commit = str(request.data.get("commit", "")).strip().lower() in TRUTHY
        importer = self.build_importer(request)
        return Response(importer.run(uploaded, commit=commit))
