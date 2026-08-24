"""
Fill a college with a believable staff, curriculum and term of records.

The point is to exercise authority: two departments with different heads, a
coordinator per programme and teachers who each hold a couple of classes, so
that signing in as any of them shows a different, correctly-bounded slice.
"""

import random
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django_tenants.utils import schema_context

from src.academics.constants import SemesterStatus
from src.academics.models import (
    Batch,
    BatchSemester,
    Department,
    Program,
    Subject,
    SubjectAllocation,
    Teacher,
)
from src.performance.constants import AssignmentStatus, AttendanceStatus
from src.performance.models import (
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    InternalExam,
    InternalExamMark,
)
from src.students.constants import SemesterEnrollmentStatus
from src.students.models import SemesterEnrollment, Student, SubjectEnrollment
from src.user.models import User, UserRole

PASSWORD = "Password@123"

DEPARTMENTS = [
    {
        "name": "Computer Science and IT",
        "code": "CSIT",
        "head": ("bikash.rana", "Bikash", "Rana"),
        "programs": [
            {
                "name": "B.Sc. Computer Science and Information Technology",
                "code": "BSCCSIT",
                "coordinator": ("sarita.koirala", "Sarita", "Koirala"),
                "subjects": [
                    ("CSC201", "Data Structures and Algorithms", 3),
                    ("CSC202", "Database Management Systems", 3),
                    ("CSC203", "Computer Networks", 3),
                    ("CSC204", "Operating Systems", 3),
                ],
            }
        ],
    },
    {
        "name": "Management",
        "code": "MGMT",
        "head": ("nabin.shrestha", "Nabin", "Shrestha"),
        "programs": [
            {
                "name": "Bachelor of Business Administration",
                "code": "BBA",
                "coordinator": ("pooja.thapa", "Pooja", "Thapa"),
                "subjects": [
                    ("MGT201", "Organisational Behaviour", 3),
                    ("MGT202", "Business Statistics", 3),
                ],
            }
        ],
    },
]

TEACHERS = [
    ("ram.gurung", "Ram", "Gurung", "CSIT", "LECTURER"),
    ("sita.adhikari", "Sita", "Adhikari", "CSIT", "ASSISTANT_PROFESSOR"),
    ("hari.poudel", "Hari", "Poudel", "MGMT", "LECTURER"),
]

FIRST_NAMES = [
    "Aayush",
    "Bibek",
    "Chandra",
    "Deepa",
    "Elina",
    "Gaurav",
    "Ishwor",
    "Jyoti",
    "Kiran",
    "Laxmi",
    "Nabina",
    "Ojash",
    "Prakash",
    "Rajesh",
    "Sabina",
    "Tara",
    "Umesh",
    "Yamuna",
    "Anita",
    "Dipesh",
    "Sunita",
    "Manoj",
    "Rekha",
    "Suman",
]
LAST_NAMES = ["Adhikari", "Bhandari", "Gurung", "Karki", "Lama", "Poudel", "Rai", "Sharma"]


class Command(BaseCommand):
    help = "Seed a college with staff, curriculum, students and a term of records."

    def add_arguments(self, parser):
        parser.add_argument("schema_name")
        parser.add_argument("--students", type=int, default=20)
        parser.add_argument("--weeks", type=int, default=6)
        parser.add_argument(
            "--creds-file",
            default="creds.md",
            help="Where to write the sign-in details. Relative to the repo root.",
        )

    def handle(self, *args, **options):
        with schema_context(options["schema_name"]):
            rows = self._seed(options)

        if rows:
            self._write_creds(options, rows)

    # ------------------------------------------------------------------

    def _account(self, username, first, last, role_codename, admin):
        user = User.objects.filter(username=username).first()

        if user is None:
            user = User.objects.create_user(
                username=username,
                email=f"{username}@college.edu",
                password=PASSWORD,
                first_name=first,
                last_name=last,
            )
            user.full_name = f"{first} {last}"
            user.save(update_fields=["full_name"])

        for codename in (role_codename, "SYSTEM-USER"):
            role = UserRole.objects.filter(codename=codename).first()
            if role:
                user.roles.add(role)

        return user

    def _seed(self, options):
        random.seed(20810)

        admin = User.objects.filter(is_superuser=True).order_by("pk").first()
        if admin is None:
            self.stderr.write("No admin in this schema. Run register_college first.")
            return None

        if SubjectAllocation.objects.exists():
            self.stdout.write("This college already has classes — nothing to do.")
            return None

        credentials = [("Principal", admin.username, "superuser — the whole college")]
        allocations = []
        teachers_by_department = {}

        for spec in DEPARTMENTS:
            department = Department.objects.create(
                name=spec["name"], code=spec["code"], created_by=admin
            )

            username, first, last = spec["head"]
            head_user = self._account(username, first, last, "DEPARTMENT-HEAD", admin)
            head = Teacher.objects.create(
                user=head_user,
                department=department,
                designation="ASSOCIATE_PROFESSOR",
                created_by=admin,
            )
            department.head = head
            department.save(update_fields=["head"])
            credentials.append(
                ("Department head", username, f"{spec['code']} — that department only")
            )

            # Teachers belong to a department before they hold any class.
            teachers_by_department[spec["code"]] = [
                Teacher.objects.create(
                    user=self._account(username, first, last, "TEACHER", admin),
                    department=department,
                    designation=designation,
                    created_by=admin,
                )
                for username, first, last, code, designation in TEACHERS
                if code == spec["code"]
            ]
            for username, _first, _last, code, _designation in TEACHERS:
                if code == spec["code"]:
                    credentials.append(("Teacher", username, f"{spec['code']} — own classes only"))

            for program_spec in spec["programs"]:
                username, first, last = program_spec["coordinator"]
                coordinator_user = self._account(
                    username, first, last, "PROGRAM-COORDINATOR", admin
                )
                coordinator = Teacher.objects.create(
                    user=coordinator_user,
                    department=department,
                    designation="ASSISTANT_PROFESSOR",
                    created_by=admin,
                )
                credentials.append(
                    (
                        "Programme coordinator",
                        username,
                        f"{program_spec['code']} — that programme only",
                    )
                )

                program = Program.objects.create(
                    department=department,
                    name=program_spec["name"],
                    code=program_spec["code"],
                    coordinator=coordinator,
                    created_by=admin,
                )

                allocations += self._build_program(
                    program, program_spec, teachers_by_department[spec["code"]], admin, options
                )

        self._record_term(allocations, admin, options)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(DEPARTMENTS)} departments, {len(allocations)} classes and "
                f"{Student.objects.count()} students."
            )
        )
        return credentials

    def _build_program(self, program, spec, teachers, admin, options):
        batch = Batch.objects.create(program=program, year=2079, created_by=admin)

        semesters = {}
        for number, status in ((1, "COMPLETED"), (2, "COMPLETED"), (3, "RUNNING")):
            semesters[number] = BatchSemester.objects.create(
                batch=batch,
                semester=number,
                status=getattr(SemesterStatus, status).value,
                created_by=admin,
            )
        running = semesters[3]

        subjects = [
            Subject.objects.create(
                program=program,
                semester=3,
                code=code,
                name=name,
                credit_hours=credits,
                created_by=admin,
            )
            for code, name, credits in spec["subjects"]
        ]

        allocations = [
            SubjectAllocation.objects.create(
                batch_semester=running,
                subject=subject,
                teacher=teachers[index % len(teachers)],
                created_by=admin,
            )
            for index, subject in enumerate(subjects)
        ]

        students = []
        for index in range(options["students"]):
            first = FIRST_NAMES[(index * 3 + len(program.code)) % len(FIRST_NAMES)]
            last = LAST_NAMES[index % len(LAST_NAMES)]
            students.append(
                Student.objects.create(
                    batch=batch,
                    roll_number=f"{index + 1:03d}",
                    registration_number=f"2079-{program.code}-{index + 1:04d}",
                    first_name=first,
                    last_name=last,
                    email=f"{first.lower()}.{last.lower()}@{program.code.lower()}.edu",
                    created_by=admin,
                )
            )

        for student in students:
            for semester in semesters.values():
                SemesterEnrollment.objects.create(
                    student=student,
                    batch_semester=semester,
                    status=(
                        SemesterEnrollmentStatus.ACTIVE.value
                        if semester == running
                        else SemesterEnrollmentStatus.COMPLETED.value
                    ),
                    created_by=admin,
                )

            for allocation in allocations:
                SubjectEnrollment.objects.create(
                    student=student, allocation=allocation, created_by=admin
                )

        return allocations

    def _record_term(self, allocations, admin, options):
        today = timezone.localdate()

        for allocation in allocations:
            roster = list(allocation.enrollments.filter(is_archived=False))

            # Habits persist, so attendance reads like people rather than noise.
            reliability = {e.id: random.uniform(0.55, 0.99) for e in roster}

            for week in range(options["weeks"], 0, -1):
                for offset in (0, 3):
                    date = today - timedelta(days=week * 7 - offset)
                    if date > today:
                        continue

                    session = AttendanceSession.objects.create(
                        allocation=allocation, date=date, created_by=admin
                    )
                    for enrollment in roster:
                        roll = random.random()
                        if roll < reliability[enrollment.id]:
                            status = AttendanceStatus.PRESENT.value
                        elif roll < reliability[enrollment.id] + 0.06:
                            status = AttendanceStatus.LATE.value
                        else:
                            status = AttendanceStatus.ABSENT.value

                        AttendanceRecord.objects.create(
                            session=session,
                            enrollment=enrollment,
                            status=status,
                            created_by=admin,
                        )

            exam = InternalExam.objects.create(
                allocation=allocation,
                title="First Term",
                exam_type="FIRST_TERM",
                full_marks=20,
                pass_marks=8,
                exam_date=today - timedelta(days=14),
                created_by=admin,
            )
            for enrollment in roster:
                InternalExamMark.objects.create(
                    exam=exam,
                    enrollment=enrollment,
                    marks_obtained=round(random.uniform(5, 20), 1),
                    created_by=admin,
                )

            assignment = Assignment.objects.create(
                allocation=allocation,
                title=f"{allocation.subject.code} lab report",
                assigned_date=today - timedelta(days=10),
                due_date=today - timedelta(days=3),
                created_by=admin,
            )
            for enrollment in roster:
                AssignmentSubmission.objects.create(
                    assignment=assignment,
                    enrollment=enrollment,
                    status=random.choices(
                        [
                            AssignmentStatus.DONE.value,
                            AssignmentStatus.PARTIAL.value,
                            AssignmentStatus.NOT_DONE.value,
                        ],
                        weights=[7, 2, 1],
                    )[0],
                    created_by=admin,
                )

    def _write_creds(self, options, rows):
        schema = options["schema_name"]
        path = Path(settings.BASE_DIR) / options["creds_file"]

        lines = [
            "# Demo sign-ins",
            "",
            f"Seeded into the **{schema}** college. Open "
            f"**http://{schema}.localhost:3000** and sign in.",
            "",
            f"Every account below uses the password `{PASSWORD}`.",
            "",
            "| Role | Username | Sees |",
            "|---|---|---|",
        ]
        lines += [f"| {role} | `{username}` | {scope} |" for role, username, scope in rows]
        lines += [
            "",
            "## What each one proves",
            "",
            "- **Principal** — a superuser. Every department, every programme, "
            "every account. Their *My Classes* is empty because nothing is "
            "allocated to them, which is the point: allocation, not rank, "
            "decides whose classes those are.",
            "- **Department head** — manages one department: its programmes, "
            "teachers, curriculum and students. Another department's records "
            "return 404, not a filtered-empty list.",
            "- **Programme coordinator** — manages one programme's batches, "
            "curriculum, allocations and students. Cannot create programmes.",
            "- **Teacher** — no management screens at all. Their sidebar is the "
            "workspace, and it shows only the classes allocated to them.",
            "",
            "Regenerate with:",
            "",
            "```bash",
            f"python manage.py seed_demo_data {schema}",
            "```",
            "",
        ]

        path.write_text("\n".join(lines))
        self.stdout.write(self.style.SUCCESS(f"Sign-in details written to {path.name}"))
