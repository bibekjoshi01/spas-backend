# Architecture and domain model

## Multi-tenancy

SPAS is a schema-per-tenant SaaS application. Each college has a PostgreSQL
schema and a domain. `django-tenants` resolves the request host before URL
resolution, so tenant IDs are not sent in API payloads.

| Area | Responsibility |
|---|---|
| Public schema | Platform control plane, health endpoints, colleges, and platform users |
| Tenant schema | College users, academics, students, attendance, exams, and assignments |
| Backend host | Resolves the college schema from the request hostname |
| Frontend host | Reads the college slug and derives the tenant API host |

The public schema uses `config.platform_urls`. Tenant requests use
`config.tenant_urls`. In development, `<college>.localhost` works without an
`/etc/hosts` entry in modern browsers and on macOS.

## Domain model

The modules have a one-way dependency: `performance → students → academics`.

- A department owns programs. Staff identities are tenant users whose assigned
  roles determine whether they can act as teachers, department heads, or
  program coordinators; there is no separate teacher record.
- A program owns batches, curriculum subjects, and semester progression.
- A batch is the permanent admission cohort; promotion never changes it.
- A batch semester records a cohort's tenure in a semester.
- A subject is a curriculum definition.
- A `SubjectAllocation` is the actual class: one subject, batch semester, and
  teacher user. The selected user must hold the `TEACHER` role.
- A semester enrollment records where a student studies in a semester.
- A student has a protected one-to-one user identity with the internal
  `STUDENT` role. Student identities are excluded from Administration account
  lists and start with login disabled, but the link supports future login.
- A subject enrollment is the student's roster row for a class.
- Attendance, internal exams, and assignments belong to a subject allocation;
  their student records point to subject enrollments.

## API and authorization

Tenant endpoints are under `/api/v1/internal`. API conventions are documented
in [api-design.md](api-design.md).

Authorization uses permission codenames such as `view_student` and
`add_attendance_session`. Roles provide defaults, but backend queryset scoping
and permission checks remain the security boundary. Every non-student account
receives the internal `SYSTEM-USER` role. Administrators may add or remove
assignable roles such as `DEPARTMENT-HEAD`, `PROGRAM-COORDINATOR`, and
`TEACHER`; superuser role assignments are immutable. `STUDENT` and
`SYSTEM-USER` are managed by the system rather than the account form.

HOD and coordinator authority follows the live foreign-key assignment. Saving
or clearing either assignment synchronizes its role automatically, and the old
assignee immediately loses that scope when they hold no other matching
assignment. An HOD sees their department hierarchy; a coordinator sees only
their assigned programs and descendants. Batch creation, editing, and
archiving are reserved for superusers, while authorized management roles may
still read batches in their scope.

In development, authenticated users can access `/api/schema/` and `/api/docs/`
on a tenant host.

## Repository layout

```text
config/          settings, URL configurations, Celery, and logging
tenants/         tenant models and registration commands
control_plane/   public-schema college and platform-user management
src/academics/   departments, programs, batches, semesters, subjects, classes
src/students/    students, semester enrollments, and class rosters
src/performance/ attendance, exams, assignments, and analytics
src/user/        tenant authentication, roles, and permissions
src/libs/        shared middleware, permissions, and tenant-aware services
spas-frontend/   React frontend, maintained as its own repository
```
