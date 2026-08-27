# Production-readiness QA checklist

This checklist covers the currently supported product surface: accounts and roles,
departments, programs, batches and semesters, curriculum, class allocations,
students and enrollments, class rosters, attendance, assessments, assignments,
and class-performance ratings.

## Authorization and isolation

- [x] Tenant middleware selects one schema before application queries run.
- [x] Every API requires an explicit model permission or authenticated dashboard access.
- [x] Superusers receive tenant-wide administrative scope.
- [x] Department heads are restricted to their assigned department and its programs.
- [x] Program coordinators are restricted to their assigned programs.
- [x] Teachers can read and change only their own class allocations.
- [x] Student accounts are excluded from staff account and role-management APIs.
- [x] Detail, update, and archive requests use the same scoped queryset as lists.
- [x] Create and bulk-create serializers validate referenced department/program IDs.
- [x] Cross-scope staff assignment candidates and teacher IDs are rejected.
- [x] Unauthorized class detail lookups return 404 to avoid existence disclosure.

## Data integrity and lifecycle

- [x] Student roll numbers are unique per batch and all-zero values are rejected.
- [x] Registration numbers and active account emails are unique.
- [x] Students have one linked, non-login student account and do not appear in staff accounts.
- [x] A batch has at most one running semester.
- [x] Semester end date cannot precede start date; both dates remain optional.
- [x] Attendance cannot be recorded in the future or outside configured semester dates.
- [x] Upcoming and completed semesters are read-only for performance records.
- [x] Completed classes remain visible to their scoped owner as historical records.
- [x] Archives are soft deletes and preserve related historical data.
- [x] Class identity cannot change after roster or performance records exist.
- [x] Duplicate bulk enrollments are idempotently skipped.
- [x] Class-performance ratings are nullable, constrained to 1-10, unique per active
  subject enrollment, and immutable outside a running semester.
- [x] Assessment marks and assignment submissions belong to students on the same class roster.

## Auditability

- [x] Core academic, student, enrollment, attendance, assessment, assignment, and
  class-performance rows retain snapshot history.
- [x] User profile and role-membership changes retain history.
- [x] Request middleware attributes history rows to the authenticated actor.
- [x] Rows retain `created_by`, `updated_by`, timestamps, active, and archived state.
- [x] Bulk attendance corrections set `updated_by` without overwriting `created_by`.
- [x] Archive operations emit history while bypassing unrelated legacy validation.

## Frontend behavior

- [x] Sidebar, routes, and actions use the same permission vocabulary as the API.
- [x] Teacher workspace is hidden from non-teachers; management areas are hidden from teachers.
- [x] Management filters and API results remain within the caller's authority scope.
- [x] Current, upcoming, and previous classes are visually separated.
- [x] Upcoming and previous classes expose read-only views instead of mutation controls.
- [x] Attendance history uses a local-calendar date and does not shift through UTC.
- [x] Blank attendance dates do not count as absences.
- [x] New attendance starts unmarked and cannot save until every student is marked.
- [x] Lists provide loading, error, empty, search/filter, and responsive overflow states.
- [x] Assessments, assignments, and class performance are available only in the
  teacher workspace and remain read-only for non-running semesters.

## Release verification

Run before deployment:

```bash
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py migrate --plan
venv/bin/python manage.py check
venv/bin/ruff check .
venv/bin/pytest -q

cd classmates-fe
npm run typecheck
npm run lint
npm run build
```

Apply shared and tenant migrations using the deployment process documented in
`docs/tenant-management.md`. Back up PostgreSQL before applying migrations in
production.
