"""Spreadsheet import: preview, upsert, and refusal to write a bad sheet."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from src.academics.models import Subject
from src.students.models import Student
from tests.test_performance_api import ACADEMICS, STUDENTS, WorkflowTestCase

STUDENT_IMPORT = f"{STUDENTS}/students/import"
SUBJECT_IMPORT = f"{ACADEMICS}/subjects/import"


def csv_upload(rows, name="students.csv"):
    text = "\n".join(",".join(str(cell) for cell in row) for row in rows)
    return SimpleUploadedFile(name, text.encode(), content_type="text/csv")


def xlsx_upload(rows, name="students.xlsx"):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return SimpleUploadedFile(
        name,
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class StudentImportTests(WorkflowTestCase):
    @staticmethod
    def get_test_schema_name():
        return "test_imports_api"

    @staticmethod
    def get_test_tenant_domain():
        return "imports-api.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Imports College"
        tenant.subdomain = "imports"
        return tenant

    def upload(self, upload, *, commit=False, batch=None, expected=status.HTTP_200_OK):
        response = self.client.post(
            STUDENT_IMPORT,
            {
                "file": upload,
                "batch": batch if batch is not None else self.batch,
                "commit": "true" if commit else "false",
            },
            format="multipart",
        )
        assert response.status_code == expected, response.data
        return response.data

    def test_preview_reports_every_row_and_writes_nothing(self):
        before = Student.objects.count()
        data = self.upload(
            csv_upload(
                [
                    ["roll_number", "first_name", "last_name", "phone_no"],
                    ["10", "Ramesh", "Thapa", "9800000010"],
                    ["11", "Sita", "Gurung", "9800000011"],
                ]
            )
        )

        assert data["committed"] is False
        assert data["summary"] == {"total": 2, "create": 2, "update": 0, "error": 0}
        assert [row["row"] for row in data["rows"]] == [2, 3]
        assert {row["action"] for row in data["rows"]} == {"create"}
        assert Student.objects.count() == before

    def test_commit_writes_the_previewed_rows(self):
        upload = csv_upload(
            [
                ["roll_number", "first_name", "last_name"],
                ["10", "Ramesh", "Thapa"],
                ["11", "Sita", "Gurung"],
            ]
        )
        data = self.upload(upload, commit=True)

        assert data["committed"] is True
        assert data["summary"]["create"] == 2
        created = Student.objects.filter(batch=self.batch, roll_number__in=("10", "11"))
        assert created.count() == 2
        # The linked student identity the API would have built comes too.
        assert all(student.user_id for student in created)
        assert all(student.user.roles.filter(codename="STUDENT").exists() for student in created)

    def test_an_existing_roll_number_is_updated_not_duplicated(self):
        existing = Student.objects.get(pk=self.students[0])
        assert existing.phone_no == ""

        preview = self.upload(
            csv_upload(
                [
                    ["roll_number", "first_name", "last_name", "phone_no"],
                    [existing.roll_number, existing.first_name, existing.last_name, "9800000099"],
                ]
            )
        )
        assert preview["summary"] == {"total": 1, "create": 0, "update": 1, "error": 0}
        assert preview["rows"][0]["changes"] == ["phone_no"]

        self.upload(
            csv_upload(
                [
                    ["roll_number", "first_name", "last_name", "phone_no"],
                    [existing.roll_number, existing.first_name, existing.last_name, "9800000099"],
                ]
            ),
            commit=True,
        )

        existing.refresh_from_db()
        assert existing.phone_no == "9800000099"
        assert (
            Student.objects.filter(batch=self.batch, roll_number=existing.roll_number).count() == 1
        )

    def test_a_blank_cell_does_not_erase_a_recorded_value(self):
        existing = Student.objects.get(pk=self.students[0])
        existing.phone_no = "9800000001"
        existing.save(update_fields=["phone_no"])

        self.upload(
            csv_upload(
                [
                    ["roll_number", "first_name", "last_name", "phone_no"],
                    [existing.roll_number, existing.first_name, existing.last_name, ""],
                ]
            ),
            commit=True,
        )

        existing.refresh_from_db()
        assert existing.phone_no == "9800000001"

    def test_a_repeated_identity_inside_one_file_is_an_error(self):
        data = self.upload(
            csv_upload(
                [
                    ["roll_number", "first_name", "last_name"],
                    ["20", "First", "Entry"],
                    ["20", "Second", "Entry"],
                ]
            )
        )

        assert data["summary"]["error"] == 1
        assert data["rows"][1]["action"] == "error"
        assert "Repeats row 2" in str(data["rows"][1]["errors"])

    def test_commit_writes_nothing_when_any_row_is_invalid(self):
        before = Student.objects.count()
        upload = csv_upload(
            [
                ["roll_number", "first_name", "last_name", "date_of_birth"],
                ["30", "Good", "Row", "2005-01-01"],
                ["31", "Bad", "Row", "not-a-date"],
            ]
        )

        response = self.client.post(
            STUDENT_IMPORT,
            {"file": upload, "batch": self.batch, "commit": "true"},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Student.objects.count() == before
        assert not Student.objects.filter(roll_number="30").exists()

    def test_a_missing_required_column_is_refused(self):
        response = self.client.post(
            STUDENT_IMPORT,
            {
                "file": csv_upload([["first_name", "last_name"], ["No", "Roll"]]),
                "batch": self.batch,
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "roll_number" in str(response.data)

    def test_headings_are_matched_however_the_college_spells_them(self):
        data = self.upload(
            csv_upload(
                [
                    ["Roll Number", "First Name", "Last Name", "Phone No"],
                    ["40", "Spelled", "Differently", "9800000040"],
                ]
            )
        )

        assert data["summary"] == {"total": 1, "create": 1, "update": 0, "error": 0}

    def test_excel_workbooks_import_too(self):
        data = self.upload(
            xlsx_upload(
                [
                    ["roll_number", "first_name", "last_name"],
                    ["50", "Excel", "Student"],
                ]
            ),
            commit=True,
        )

        assert data["summary"]["create"] == 1
        assert Student.objects.filter(batch=self.batch, roll_number="50").exists()

    def test_an_unsupported_file_type_is_refused(self):
        response = self.client.post(
            STUDENT_IMPORT,
            {
                "file": SimpleUploadedFile(
                    "students.pdf", b"%PDF-1.4", content_type="application/pdf"
                ),
                "batch": self.batch,
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_batch_outside_the_callers_authority_is_not_found(self):
        other_department = self.post(
            f"{ACADEMICS}/departments", {"name": "Management", "code": "BBA"}
        )["id"]
        other_program = self.post(
            f"{ACADEMICS}/programs",
            {"department": other_department, "name": "BBA", "code": "BBAP"},
        )["id"]
        other_batch = self.post(f"{ACADEMICS}/batches", {"program": other_program, "year": 2080})[
            "id"
        ]

        coordinator = self.make_user("import-coordinator", "PROGRAM-COORDINATOR")
        assert (
            self.client.patch(
                f"{ACADEMICS}/programs/{self.program}",
                {"coordinator": coordinator.pk},
                format="json",
            ).status_code
            == status.HTTP_200_OK
        )

        self.client.credentials()
        self.authenticate(coordinator.username)

        response = self.client.post(
            STUDENT_IMPORT,
            {
                "file": csv_upload(
                    [["roll_number", "first_name", "last_name"], ["60", "Out", "Ofscope"]]
                ),
                "batch": other_batch,
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not Student.objects.filter(roll_number="60").exists()

    def test_a_teacher_may_not_import_students(self):
        self.as_teacher()
        response = self.client.post(
            STUDENT_IMPORT,
            {
                "file": csv_upload(
                    [["roll_number", "first_name", "last_name"], ["70", "Not", "Allowed"]]
                ),
                "batch": self.batch,
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not Student.objects.filter(roll_number="70").exists()

    def test_the_template_lists_the_columns(self):
        response = self.client.get(STUDENT_IMPORT)

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/csv"
        header = response.content.decode().splitlines()[0]
        assert header.startswith("roll_number,first_name")


class SubjectImportTests(WorkflowTestCase):
    @staticmethod
    def get_test_schema_name():
        return "test_subject_imports_api"

    @staticmethod
    def get_test_tenant_domain():
        return "subject-imports.test.localhost"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Subject Imports College"
        tenant.subdomain = "subject-imports"
        return tenant

    def upload(self, rows, *, commit=False, expected=status.HTTP_200_OK):
        response = self.client.post(
            SUBJECT_IMPORT,
            {
                "file": csv_upload(rows, name="subjects.csv"),
                "program": self.program,
                "commit": "true" if commit else "false",
            },
            format="multipart",
        )
        assert response.status_code == expected, response.data
        return response.data

    def test_a_whole_curriculum_imports_in_one_sheet(self):
        data = self.upload(
            [
                ["code", "name", "semester", "credit_hours"],
                ["CSC101", "Introduction to IT", 1, 3],
                ["CSC102", "C Programming", 1, 4],
                ["CSC301", "Operating Systems", 5, 3],
            ],
            commit=True,
        )

        assert data["summary"] == {"total": 3, "create": 3, "update": 0, "error": 0}
        assert Subject.objects.filter(program=self.program, code="CSC102").get().credit_hours == 4

    def test_an_existing_code_in_the_same_semester_is_updated(self):
        # The fixture already has CSC201 in semester 3.
        data = self.upload(
            [
                ["code", "name", "semester", "credit_hours"],
                ["CSC201", "Data Structures and Algorithms", 3, 4],
            ],
            commit=True,
        )

        assert data["summary"]["update"] == 1
        subject = Subject.objects.get(program=self.program, code="CSC201", semester=3)
        assert subject.name == "Data Structures and Algorithms"
        assert subject.credit_hours == 4
        assert Subject.objects.filter(program=self.program, code="CSC201").count() == 1

    def test_a_semester_beyond_the_program_is_reported_per_row(self):
        data = self.upload(
            [
                ["code", "name", "semester"],
                ["CSC999", "Impossible Subject", 99],
            ]
        )

        assert data["summary"]["error"] == 1
        assert "semester" in str(data["rows"][0]["errors"])
