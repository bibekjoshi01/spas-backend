# Production-readiness QA checklist

This checklist covers the currently supported product surface: accounts and roles,
departments, programs, batches and semesters, curriculum, class allocations,
students and enrollments, class rosters, attendance, assessments, assignments,
class-performance ratings, management attention queues, individual student
performance reports, and spreadsheet import of students and curriculum.
Batch/semester performance reports aggregate those parameters across the
student's active subject enrollments and normalize configured weights over only
the parameters that have recorded evidence. The attendance requirement that
decides eligibility is a per-tenant setting rather than a fixed 75%.

## Authorization and isolation

- [x] Tenant middleware selects one schema before application queries run.
- [x] Every API requires an explicit model permission or authenticated dashboard access.
- [x] The performance policy is readable by any signed-in staff account, because
  every eligibility badge measures against it, and writable only by a superuser.
- [x] Superusers receive tenant-wide administrative scope.
- [x] Department heads are restricted to their assigned department and its programs.
- [x] Program coordinators are restricted to their assigned programs.
- [x] Teachers can read and change only their own class allocations.
- [x] Student accounts are excluded from staff account and role-management APIs.
- [x] Detail, update, and archive requests use the same scoped queryset as lists.
- [x] Create and bulk-create serializers validate referenced department/program IDs.
- [x] Cross-scope staff assignment candidates and teacher IDs are rejected.
- [x] Unauthorized class detail lookups return 404 to avoid existence disclosure.
- [x] Management student reports return 404 for guessed students outside the
  caller's department/program authority; teachers cannot access the endpoint.
- [x] Batch/semester report selection is authority-scoped, and cross-program
  semester IDs return 404 without disclosing their existence.
- [x] Subject-allocation reports use the same hierarchy scope for roster and
  student-detail reads; teachers retain access only to their own allocations.
- [x] Management attendance reports enforce bounded, non-future date ranges
  and apply authority scope before program, batch, semester, or class filters.

## Spreadsheet import

- [x] Import accepts .csv and .xlsx only, caps file size and row count, and
  matches headings on letters and digits so spelling and case do not matter.
- [x] An upload validates and reports every row without writing; only an
  explicit commit writes, and it writes nothing unless every row is clean.
- [x] Rows run through the same create and patch serializers the API uses, so
  an import cannot enter what the API would reject, and audit fields, linked
  student identities, roles and history all follow.
- [x] A row matching an existing record updates it rather than duplicating it;
  blank cells are omitted, so a sparse sheet never erases recorded values.
- [x] Repeated identities inside one file are reported instead of silently
  letting the last row win.
- [x] Import requires both the add and edit permission for the resource.
- [x] The target batch or program is authority-checked, and one outside the
  caller's scope returns 404 without disclosing that it exists.

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
- [x] The attendance eligibility threshold is constrained to 0-100 at the database
  and mirrored in the serializer for a field-level 400.
- [x] Attention queues, dashboard at-risk lists, and batch reports all measure
  against the tenant's configured threshold, not a hardcoded figure.
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
- [x] Individual student reports retain semester and subject context, keep
  historical subjects readable, and provide a complete PDF export.
- [x] Batch reports default to running semesters, paginate large cohorts,
  prioritize attention cases, and export the complete filtered dataset.
- [x] Student and subject reports open from their existing CRUD rows instead
  of duplicating those resources in separate navigation modules.
- [x] Attendance reporting supports daily, weekly, monthly, and custom ranges,
  dependent management filters, pagination, and complete filtered PDF export.
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

cd spas-frontend
yarn verify
yarn build
```

Apply shared and tenant migrations using the deployment process documented in
`docs/tenant-management.md`. Back up PostgreSQL before applying migrations in
production.
