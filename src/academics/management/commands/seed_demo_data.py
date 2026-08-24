"""Fill a college with a term's worth of believable data to look at."""

import random
from datetime import timedelta

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

SUBJECTS = [
    ("CSC201", "Data Structures and Algorithms", 3),
    ("CSC202", "Database Management Systems", 3),
    ("CSC203", "Computer Networks", 3),
    ("CSC204", "Operating Systems", 3),
]

TEACHERS = [
    ("rshrestha", "Ram", "Shrestha", "LECTURER"),
    ("sthapa", "Sita", "Thapa", "ASSISTANT_PROFESSOR"),
]

FIRST_NAMES = [
    "Aayush",
    "Bibek",
    "Chandra",
    "Deepa",
    "Elina",
    "Gaurav",
    "Hari",
    "Ishwor",
    "Jyoti",
    "Kiran",
    "Laxmi",
    "Manish",
    "Nabin",
    "Ojash",
    "Prakash",
    "Rajesh",
    "Sabina",
    "Tara",
    "Umesh",
    "Yamuna",
    "Anita",
    "Bikash",
    "Dipesh",
    "Sunita",
]
LAST_NAMES = ["Adhikari", "Bhandari", "Gurung", "Karki", "Lama", "Poudel", "Rai", "Sharma"]


class Command(BaseCommand):
    help = "Seed one college with departments, classes, students and a term of records."

    def add_arguments(self, parser):
        parser.add_argument("schema_name")
        parser.add_argument("--students", type=int, default=24)
        parser.add_argument("--weeks", type=int, default=6)

    def handle(self, *args, **options):
        schema_name = options["schema_name"]

        with schema_context(schema_name):
            self._seed(options)

    def _seed(self, options):
        random.seed(20790)

        admin = User.objects.filter(is_superuser=True).order_by("pk").first()
        if admin is None:
            self.stderr.write("No admin in this schema. Run register_college first.")
            return

        if SubjectAllocation.objects.exists():
            self.stdout.write("This college already has classes — nothing to do.")
            return

        department = Department.objects.create(
            name="Computer Science and IT", code="CSIT", created_by=admin
        )
        program = Program.objects.create(
            department=department,
            name="B.Sc. Computer Science and Information Technology",
            code="BSCCSIT",
            total_semesters=8,
            created_by=admin,
        )
        batch = Batch.objects.create(program=program, year=2079, created_by=admin)

        # Two finished semesters and the one running now, so progression is
        # visible rather than implied.
        semesters = {}
        for number, status in ((1, "COMPLETED"), (2, "COMPLETED"), (3, "RUNNING")):
            semesters[number] = BatchSemester.objects.create(
                batch=batch,
                semester=number,
                status=getattr(SemesterStatus, status).value,
                created_by=admin,
            )
        running = semesters[3]

        teacher_role = UserRole.objects.filter(codename="TEACHER").first()
        teachers = []
        for username, first, last, designation in TEACHERS:
            user = User.objects.filter(username=username).first()
            if user is None:
                user = User.objects.create_user(
                    username=username,
                    email=f"{username}@college.edu",
                    password="Teacher!2345",
                    first_name=first,
                    last_name=last,
                )
                user.full_name = f"{first} {last}"
                user.save(update_fields=["full_name"])
            if teacher_role:
                user.roles.add(teacher_role)

            teachers.append(
                Teacher.objects.create(
                    user=user,
                    department=department,
                    designation=designation,
                    created_by=admin,
                )
            )

        subjects = [
            Subject.objects.create(
                program=program,
                semester=3,
                code=code,
                name=name,
                credit_hours=credits,
                created_by=admin,
            )
            for code, name, credits in SUBJECTS
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
            first = FIRST_NAMES[index % len(FIRST_NAMES)]
            last = LAST_NAMES[index % len(LAST_NAMES)]
            students.append(
                Student.objects.create(
                    batch=batch,
                    roll_number=f"{index + 1:03d}",
                    registration_number=f"2079-1-3-{index + 1:04d}",
                    first_name=first,
                    last_name=last,
                    email=f"{first.lower()}.{last.lower()}@student.edu",
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

        enrollments = {
            allocation.id: [
                SubjectEnrollment.objects.create(
                    student=student, allocation=allocation, created_by=admin
                )
                for student in students
            ]
            for allocation in allocations
        }

        today = timezone.localdate()
        sessions = 0
        records = 0

        for allocation in allocations:
            roster = enrollments[allocation.id]

            # A student's habits persist, so attendance looks like people
            # rather than noise: a few are reliably absent.
            reliability = {enrollment.id: random.uniform(0.55, 0.99) for enrollment in roster}

            for week in range(options["weeks"], 0, -1):
                for offset in (0, 3):
                    date = today - timedelta(days=week * 7 - offset)
                    if date > today:
                        continue

                    session = AttendanceSession.objects.create(
                        allocation=allocation, date=date, created_by=admin
                    )
                    sessions += 1

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
                        records += 1

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

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(allocations)} classes, {len(students)} students, "
                f"{sessions} attendance sessions and {records} records."
            )
        )
        self.stdout.write(
            "Teachers can sign in as "
            + ", ".join(f"'{name}'" for name, *_ in TEACHERS)
            + " with password 'Teacher!2345'."
        )
