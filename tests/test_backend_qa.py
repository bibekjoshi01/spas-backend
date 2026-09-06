"""Backend QA: role boundaries, identity, and academic consistency."""

from src.academics.models import Program, Subject, SubjectAllocation
from src.performance.models import AttendanceSession
from src.students.models import Student, StudentPortalConfiguration
from src.user.models import Permission, User, UserRole
from tests.test_performance_api import ACADEMICS, PERFORMANCE, WorkflowTestCase
from tests.test_user_api import BASE, UserAPITestCase


class AccountQARegressionTests(UserAPITestCase):
    def test_mixed_case_username_and_email_can_sign_in(self):
        user = self.make_user("MixedCase", "TEACHER")
        assert self.login("MixedCase")["id"] == user.pk
        assert self.login("mixedcase@COLLEGE.edu")["id"] == user.pk

    def test_case_distinct_legacy_usernames_do_not_sign_into_another_account(self):
        upper = self.make_user("Legacy", "TEACHER")
        lower = self.make_user("legacy", "TEACHER")
        assert self.login("Legacy")["id"] == upper.pk
        assert self.login("legacy")["id"] == lower.pk
        response = self.client.post(
            f"{BASE}/account/login",
            {"persona": "LEGACY", "password": self.password},
            format="json",
        )
        assert response.status_code == 400

    def test_recovery_never_substitutes_a_disabled_exact_identity(self):
        from src.user.password_reset import find_recoverable_user

        disabled = self.make_user("DisabledCase", "TEACHER")
        other = self.make_user("disabledcase", "TEACHER")
        disabled.is_active = False
        disabled.save()
        assert find_recoverable_user("DisabledCase") is None
        assert find_recoverable_user("DISABLEDCASE") is None
        assert find_recoverable_user("disabledcase") == other

    def test_logout_rejects_another_accounts_refresh_token(self):
        owner = self.make_user("owner", "TEACHER")
        token = owner.tokens["refresh"]
        self.make_user("caller", "TEACHER")
        self.authenticate("caller")
        response = self.client.post(f"{BASE}/account/logout", {"refresh": token}, format="json")
        assert response.status_code == 400
        self.client.credentials()
        response = self.client.post(
            f"{BASE}/account/token/refresh", {"refresh": token}, format="json"
        )
        assert response.status_code == 200

    def test_delegated_account_archiver_cannot_archive_a_superuser(self):
        caller = self.make_user("archiver")
        role = UserRole.objects.create(
            id=900, name="Account archiver", codename="ARCHIVER", created_by=self.admin
        )
        role.permissions.add(Permission.objects.get(codename="delete_user"))
        caller.roles.add(role)
        self.authenticate("archiver")
        response = self.client.delete(f"{BASE}/users/{self.admin.pk}")
        assert response.status_code == 400
        self.admin.refresh_from_db()
        assert self.admin.is_active and not self.admin.is_archived

    def test_long_composed_names_fit_account_and_audit_schema(self):
        self.authenticate_as_admin()
        names = {"firstName": "A" * 100, "middleName": "C" * 100, "lastName": "B" * 100}
        combined = " ".join(names.values())
        response = self.client.post(
            f"{BASE}/users",
            {
                "username": "long-name",
                "email": "long-name@college.edu",
                "password": self.password,
                **names,
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        account = User.objects.get(pk=response.data["id"])
        assert account.full_name == combined
        assert account.history.first().full_name == combined
        response = self.client.patch(f"{BASE}/account/me", names, format="json")
        assert response.status_code == 200
        self.admin.refresh_from_db()
        assert self.admin.full_name == combined

    def test_failed_login_does_not_disclose_whether_account_exists(self):
        self.make_user("known-account", "TEACHER")
        responses = [
            self.client.post(
                f"{BASE}/account/login",
                {"persona": name, "password": "IncorrectPassword!234"},
                format="json",
            )
            for name in ("known-account", "unknown-account")
        ]
        assert responses[0].status_code == responses[1].status_code == 400
        assert responses[0].json() == responses[1].json()

    def test_password_reset_token_is_bound_to_its_college(self):
        import pytest
        from django.core import signing
        from django.db import connection
        from django_tenants.utils import schema_context
        from rest_framework.exceptions import ValidationError

        from src.user.password_reset import (
            RESET_TOKEN_SALT,
            create_password_reset_request,
            reset_password_with_token,
            verify_password_reset_code,
        )

        user = self.make_user("recover-me", "TEACHER")
        reset, code = create_password_reset_request(user, None)
        token = verify_password_reset_code(user.username, code)
        assert (
            signing.loads(token, salt=RESET_TOKEN_SALT)["tenant_schema"] == connection.schema_name
        )
        with schema_context("public"), pytest.raises(ValidationError):
            reset_password_with_token(token, "NewSecurePassword!456")
        reset.refresh_from_db()
        assert reset.consumed_at is None
        legacy = signing.dumps({"request_id": reset.pk}, salt=RESET_TOKEN_SALT)
        with pytest.raises(ValidationError):
            reset_password_with_token(legacy, "NewSecurePassword!456")
        reset_password_with_token(token, "NewSecurePassword!456")
        user.refresh_from_db()
        assert user.check_password("NewSecurePassword!456")

    def test_logout_rejects_refresh_token_from_another_college(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        account = self.make_user("logout-tenant", "TEACHER")
        self.authenticate(account.username)
        token = RefreshToken(account.tokens["refresh"])
        token["tenant_schema"] = "another-college"
        response = self.client.post(
            f"{BASE}/account/logout", {"refresh": str(token)}, format="json"
        )
        assert response.status_code == 400


class AcademicQARegressionTests(WorkflowTestCase):
    def test_revoked_teacher_role_cannot_read_dashboard(self):
        self.as_teacher()
        role = UserRole.objects.get(codename="TEACHER")
        role.is_active = False
        role.save()
        response = self.client.get(f"{PERFORMANCE}/analytics/overview")
        assert response.status_code == 403

    def test_student_with_accidental_staff_roles_cannot_use_staff_apis(self):
        student = Student.objects.get(pk=self.students[0])
        account = student.user
        account.roles.add(UserRole.objects.get(codename="TEACHER"))
        account.is_superuser = True
        account.must_change_password = False
        account.save()
        StudentPortalConfiguration.objects.create(login_enabled=True, created_by=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {account.tokens['access']}")
        for endpoint in (
            f"{BASE}/users",
            f"{ACADEMICS}/departments",
            f"{ACADEMICS}/calendar/year",
            f"{PERFORMANCE}/attendance-sessions",
            f"{PERFORMANCE}/analytics/overview",
            f"{PERFORMANCE}/analytics/attendance-attention",
        ):
            assert self.client.get(endpoint).status_code == 403, endpoint
        for endpoint in (f"{PERFORMANCE}/attendance-sessions", f"{ACADEMICS}/subjects/import"):
            assert self.client.post(endpoint, {}, format="json").status_code == 403, endpoint
        assert self.client.get(f"{PERFORMANCE}/student-portal/overview").status_code == 200

    def test_program_cannot_shrink_below_existing_semesters(self):
        response = self.client.patch(
            f"{ACADEMICS}/programs/{self.program}", {"totalSemesters": 2}, format="json"
        )
        assert response.status_code == 400
        assert "totalSemesters" in response.json()
        assert Program.objects.get(pk=self.program).total_semesters == 8
        response = self.client.patch(
            f"{ACADEMICS}/programs/{self.program}", {"totalSemesters": 6}, format="json"
        )
        assert response.status_code == 200

    def test_allocated_subject_cannot_change_semester(self):
        subject = SubjectAllocation.objects.get(pk=self.allocation).subject
        response = self.client.patch(
            f"{ACADEMICS}/subjects/{subject.pk}", {"semester": 4}, format="json"
        )
        assert response.status_code == 400
        assert "semester" in response.json()
        subject.refresh_from_db()
        assert subject.semester == 3

    def test_archived_attendance_still_protects_class_identity(self):
        allocation = SubjectAllocation.objects.get(pk=self.allocation)
        session = AttendanceSession.objects.create(
            allocation=allocation, date="2026-01-09", created_by=self.admin
        )
        session.is_archived = True
        session.save()
        subject = Subject.objects.create(
            program_id=self.program,
            semester=3,
            code="NEW",
            name="New subject",
            created_by=self.admin,
        )
        response = self.client.patch(
            f"{ACADEMICS}/allocations/{self.allocation}", {"subject": subject.pk}, format="json"
        )
        assert response.status_code == 400
        allocation.refresh_from_db()
        assert allocation.subject_id != subject.pk

    def test_malformed_query_ids_are_field_errors(self):
        self.as_teacher()
        for value in ("abc", "²", "0", "-1", "9" * 1001):
            for endpoint in ("roster", "class-performance"):
                response = self.client.get(f"{PERFORMANCE}/{endpoint}", {"allocation": value})
                assert response.status_code == 400, (endpoint, value, response.data)
                assert "allocation" in response.json()
        self.authenticate_as_admin()
        for value in ("abc", "²", "9" * 1001):
            response = self.client.get(
                f"{PERFORMANCE}/analytics/attendance-attention", {"program": value}
            )
            assert response.status_code == 400
            assert "program" in response.json()
            response = self.client.get(
                f"{PERFORMANCE}/analytics/batch-semester-report", {"batch_semester": value}
            )
            assert response.status_code == 400
            response = self.client.get(
                "/api/v1/internal/audit-mod/trail", {"resource": "subject", "actor": value}
            )
            assert response.status_code == 400

    def test_revoked_management_role_cannot_read_attention(self):
        coordinator = self.make_user("qa-head", "DEPARTMENT-HEAD")
        from src.academics.models import Department

        department = Department.objects.get(pk=self.department)
        department.head = coordinator
        department.save()
        self.authenticate("qa-head")
        assert self.client.get(f"{PERFORMANCE}/analytics/attendance-attention").status_code == 200
        role = UserRole.objects.get(codename="DEPARTMENT-HEAD")
        role.is_active = False
        role.save()
        assert self.client.get(f"{PERFORMANCE}/analytics/attendance-attention").status_code == 403

    def test_assessment_scale_cannot_drop_below_recorded_marks(self):
        enrollments = self.enroll_roster()
        exam = self.post(
            f"{PERFORMANCE}/internal-exams",
            {
                "allocation": self.allocation,
                "title": "QA assessment",
                "fullMarks": 100,
                "passMarks": 40,
            },
        )["id"]
        self.post(
            f"{PERFORMANCE}/internal-exams/{exam}/marks",
            {"entries": [{"enrollment": enrollments[0], "marksObtained": "90"}]},
        )
        response = self.client.patch(
            f"{PERFORMANCE}/internal-exams/{exam}",
            {"fullMarks": 50, "passMarks": 20},
            format="json",
        )
        assert response.status_code == 400
        assert "fullMarks" in response.json()
        response = self.client.patch(
            f"{PERFORMANCE}/internal-exams/{exam}", {"fullMarks": 95}, format="json"
        )
        assert response.status_code == 200
        from src.performance.models import InternalExam

        saved = InternalExam.objects.get(pk=exam)
        assert saved.full_marks == 95
        assert saved.history.first().history_user == self.teacher_user

    def test_stale_grade_form_rechecks_current_assessment_scale(self):
        from types import SimpleNamespace

        import pytest
        from rest_framework.exceptions import ValidationError

        from src.performance.models import InternalExam, InternalExamMark
        from src.performance.serializers import InternalExamMarkBulkSerializer

        enrollments = self.enroll_roster()
        exam = InternalExam.objects.create(
            allocation_id=self.allocation,
            title="Scale correction",
            full_marks=100,
            pass_marks=40,
            created_by=self.teacher_user,
        )
        serializer = InternalExamMarkBulkSerializer(
            data={"entries": [{"enrollment": enrollments[0], "marks_obtained": "90"}]},
            context={"request": SimpleNamespace(user=self.teacher_user), "exam": exam},
        )
        assert serializer.is_valid(), serializer.errors
        current = InternalExam.objects.get(pk=exam.pk)
        current.full_marks = 50
        current.save()
        with pytest.raises(ValidationError):
            serializer.save()
        assert not InternalExamMark.objects.filter(exam=exam).exists()

    def test_student_long_name_stays_consistent_with_linked_account(self):
        response = self.client.post(
            "/api/v1/internal/students-mod/students",
            {
                "batch": self.batch,
                "rollNumber": "LONG-NAME",
                "firstName": "A" * 100,
                "middleName": "C" * 100,
                "lastName": "B" * 100,
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        student = Student.objects.select_related("user").get(pk=response.data["id"])
        assert len(student.full_name) == 302
        assert student.user.full_name == student.full_name
