# SPAS Backend Engineering Guide

## Scope

This repository is the Django backend for SPAS, a multi-tenant academic
administration and student-performance system. Models, permissions, queryset
scopes, serializers, and migrations are the source of truth.

The supported production surface is tenant and account administration, roles
and permissions, academic structure, students and enrollments, class rosters,
attendance, assessments, assignments, and holistic class-performance ratings.

## Domain language

- A tenant is one college and owns all data in its PostgreSQL schema.
- A department owns programs. A program owns batches and curriculum subjects.
- A batch is a permanent admission cohort; promotion never changes a student's batch.
- `BatchSemester` represents a cohort's tenure in one semester.
- `Subject` is curriculum. `SubjectAllocation` is the actual class: one
  subject, batch semester, and teacher account.
- `SemesterEnrollment` records semester progression. `SubjectEnrollment` is a
  class roster membership.
- `AttendanceSession` represents a held class; `AttendanceRecord` points to a
  subject enrollment.
- Assessments and assignments belong to a subject allocation; marks,
  submissions, and the current 1-10 class-performance rating point to subject
  enrollments and retain correction history.
- Every student has a linked system-managed user with the `STUDENT` role, but
  student accounts stay out of staff-account and assignable-role APIs.

## Authorization and isolation

Permissions answer what an account may do. Queryset and serializer scope answer
which rows it may do it to. Both checks are mandatory.

- Superuser/admin: tenant-wide administrative authority.
- Department head: the department they currently head and its programs.
- Program coordinator: programs they currently coordinate.
- Teacher: subject allocations where `allocation.teacher == request.user`.
- Student: no management or staff API visibility unless a dedicated student
  API is explicitly introduced.

Rules:

- Use `ModelPermission` mappings for HTTP-method authorization.
- Use `AuthorityScopedMixin`, `scope_by_authority`, or
  `scope_to_allocation_owner` for row-level reads, updates, and archives.
- Apply the same authority validation to POST and bulk-write foreign keys.
  A scoped list queryset alone is insufficient.
- Return 404 for out-of-scope object lookups where possible; do not disclose
  whether another scope's record exists.
- Never trust tenant, department, program, allocation, student, enrollment, or
  staff IDs supplied by a client.
- Superuser bypass applies only inside the current tenant schema.
- Frontend filtering is never a security boundary.

When adding a relation, document how it reaches a department/program and add
cross-scope read and write regression tests.

## Data integrity and lifecycle

- Put universal invariants in models and mirror important ones in serializers
  for stable field-level HTTP 400 responses.
- Use database constraints for uniqueness, ranges, and conditional active-row
  invariants.
- Use `PROTECT` for referenced history and soft archive business records.
- Never physically delete academic or attendance history through the API.
- Preserve `created_by`; set `updated_by` on corrections, bulk updates, and archives.
- Core academic, student, enrollment, attendance, and role entities retain
  `django-simple-history` snapshots attributed by `HistoryRequestMiddleware`.
- Bulk writes are transactional, validate every row before mutation, and are
  idempotent where retrying is safe.
- Avoid queryset `update()` for audited mutations unless history is created by
  another supported mechanism.

Semester policy:

- Only `RUNNING` semesters accept attendance mutations.
- `UPCOMING` and `COMPLETED` semesters remain readable but immutable.
- Optional semester start/end dates constrain attendance when present.
- Future attendance is always invalid.
- Historical classes remain visible to their authorized owner.

## API conventions

Follow [docs/api-design.md](docs/api-design.md).

- Internal endpoints live under `/api/v1/internal/<module>/`.
- JSON is camelCase at the HTTP boundary and snake_case in Python.
- Lists use the standard pagination envelope; `limit=0` means all authorized rows.
- Use DRF filtering, search, and ordering backends instead of ad-hoc filtering.
- Use established message/id response envelopes.
- Return actionable field errors with 400, authentication failures with 401,
  permission failures with 403, and scoped misses with 404.
- Keep list serializers compact and retrieve serializers detailed.
- Do not add API fields silently; update types, documentation, and tests.

## Accounts and roles

- Every staff login has the system-managed `SYSTEM-USER` role.
- `STUDENT` and `SYSTEM-USER` are not directly assignable.
- A superuser's roles cannot be changed through the normal account API.
- Authority roles follow live department-head and coordinator assignments;
  remove obsolete authority when no remaining assignment requires it.
- Archiving a student disables its linked account.
- Account archive is soft, timestamped, audited, and cannot target the caller.
- Password-reset responses must not reveal whether an account exists.

## Django implementation conventions

- Keep feature code in its existing app under `src/`.
- Prefer focused serializers and shared validation helpers over duplicated view logic.
- Request-independent invariants belong in models; request authority belongs in
  permissions, scoping helpers, serializers, or services.
- Use `select_related`, `prefetch_related`, and annotated counts to avoid N+1 queries.
- Wrap multi-row writes and lifecycle changes in `transaction.atomic`.
- Apply scoping declaratively in `get_queryset()`.
- Do not hide validation or integrity errors behind broad exception handling.
- Use timezone-aware Django utilities and `timezone.localdate()` for academic dates.
- Keep Ruff formatting, import order, and existing naming conventions.

## Migrations and fixtures

- Do not edit released migrations. An uncommitted migration graph may be
  corrected before release only when the reason is documented.
- Data migrations use historical models from `apps.get_model()` and tolerate
  absent seeded roles where practical.
- Preserve upgrade paths for databases that applied earlier feature migrations
  in a different order.
- Fixtures are stable by codename. Removing roles or permissions requires a
  data migration and tests, not only fixture deletion.
- Do not apply production migrations automatically; provide a reviewed plan
  and use the deployment backup/migration process.

## Tests and release checks

Authorization work requires positive and negative coverage for allowed scope,
cross-scope reads, guessed cross-scope IDs, lifecycle behavior, validation
shape, and audit attribution.

Run narrow tests while developing, then before handoff:

```bash
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py migrate --plan
venv/bin/python manage.py check
venv/bin/ruff check .
venv/bin/pytest -q
```

Database tests require configured PostgreSQL. Do not weaken production models
or tests to avoid local database setup.

## Working tree and handoff

- Preserve unrelated user changes and untracked files.
- Do not use destructive Git commands to clean the tree.
- Review generated migrations and staged diffs before committing.
- Keep backend and frontend commits in their respective repositories.
- Update [docs/qa-production-readiness.md](docs/qa-production-readiness.md)
  when the supported surface or release checks change.
- Report migrations, tests, assumptions, and remaining warnings.
