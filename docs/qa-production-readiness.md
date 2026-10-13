# Production-readiness QA checklist

Latest backend audit: [Backend QA — 6 September 2026](qa-backend-2026-09-06.md).
Previous website audit: [Website QA — 5 September 2026](qa-2026-09-05.md).

This checklist covers the currently supported product surface: accounts and roles,
departments, programs, batches and semesters, curriculum, class allocations,
students and enrollments, class rosters, attendance, assessments, assignments,
class-performance ratings, management attention queues, individual student
performance reports, spreadsheet import of students and curriculum, and the
read-only student portal with academic progression, subject marks, assignment
deadlines/feedback, timetable, and paginated personal attendance history. The supported surface also includes
admin-only institution details, subject internal full/pass marks, and blank or
calculated internal evaluation PDF registers.
Batch/semester performance reports aggregate those parameters across the
student's active subject enrollments and normalize configured weights over only
the parameters that have recorded evidence. The attendance requirement that
decides eligibility is a per-tenant setting rather than a fixed 75%.

## Authorization and isolation

- [x] Tenant middleware selects one schema before application queries run.
- [x] Staff dashboards and attention queues require live attendance permission;
  the existing row-level authority checks still apply.
- [x] The performance policy is readable by any signed-in staff account, because
  every eligibility badge measures against it, and writable only by a superuser.
- [x] Superusers receive tenant-wide administrative scope.
- [x] Department heads are restricted to their assigned department and its programs.
- [x] Program coordinators are restricted to their assigned programs.
- [x] Teachers can read and change only their own class allocations.
- [x] Student accounts are excluded from staff APIs even if staff roles or a
  superuser flag were accidentally attached. Portal access remains separate.
- [x] Student portal reads derive the student exclusively from the authenticated
  account relationship and accept no client-supplied student identifier.
- [x] Student subject detail/history return 404 for guessed peer enrollment IDs,
  including peers in the same class; shared class metadata excludes staff IDs,
  usernames, roster counts, and classmates' attendance aggregates.
- [x] Linked student accounts remain excluded from staff APIs after role removal;
  inactive/archived student roles grant no portal access. Session profiles hide
  accidental staff roles/superuser flags and recheck live student access policy.
- [x] Personal portal responses prohibit browser/proxy caching with no-store.
- [x] Access and refresh tokens are bound to the issuing tenant schema and are
  rejected when replayed against another college hostname.
- [x] Password-reset tokens are tenant-bound; logout checks both the tenant and
  refresh-token owner. Failed login responses do not distinguish missing accounts.
- [x] The tenant student-login switch is writable only by a superuser and is
  enforced again on every portal request, including sessions issued earlier.
- [x] Student status, account activity, and tenant policy are rechecked during
  refresh; non-studying students cannot extend an old session.
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
- [x] Students have one linked account, remain absent from staff account APIs,
  and receive a deterministic `{firstName}-{rollNumber}` username (with a suffix only
  when two students would otherwise receive the same username).
- [x] Initial roll-number passwords are hashed, require replacement before any
  portal data is readable, and password changes rotate access and refresh tokens.
- [x] Staff accounts created with temporary passwords require replacement at
  first sign-in. Login/profile flags drive the shared initial-password screen;
  pending accounts cannot access staff features, even with a superuser flag.
  Rejected changes retain the requirement; valid changes restore role permissions.
- [x] Teacher assignments support basic formatted task descriptions and up to five
  private resources (10 MiB each), with audited create/edit/soft removal and atomic
  validation. Teacher downloads remain allocation-owned; student downloads require
  the caller's own enrollment and the live student login policy.
- [x] Students have an ongoing/all assignments section and task details with safe
  formatting, resources and only their manual evaluation/feedback. Future-dated,
  inactive and archived tasks stay unpublished. Student submission remains out of
  scope; teachers retain the existing manual evaluation workflow.
- [x] A batch has at most one running semester.
- [x] Duplicate batch entry years within a program return a field-level `year`
  error on create and edit, including database conflicts after validation.
  Archived years may be reused; inactive, unarchived batches still reserve theirs.
- [x] Semester end date cannot precede start date; both dates remain optional.
- [x] Attendance cannot be recorded in the future or outside configured semester dates.
- [x] Upcoming and completed semesters are read-only for performance records.
- [x] Completed classes remain visible to their scoped owner as historical records.
- [x] Archives are soft deletes and preserve related historical data.
- [x] Class identity cannot change after roster or performance records exist,
  including archived records. Program/subject edits preserve existing semesters.
- [x] Assessment scale changes cannot invalidate recorded marks; grade writes
  recheck the latest scale under the same row lock.
- [x] Current and historical account full-name columns fit all three name fields.
- [x] Duplicate bulk enrollments are idempotently skipped, including repeated IDs
  within one request. Enrollment foreign keys are scoped before validation.
- [x] Disabled and archived roles do not grant permissions.
- [x] Cohort graduation writes student history and records which students it
  changed; undo preserves individual graduates. Legacy graduates require review.
- [x] The attendance eligibility threshold is constrained to 0-100 at the database
  and mirrored in the serializer for a field-level 400.
- [x] Attention queues, dashboard at-risk lists, and batch reports all measure
  against the tenant's configured threshold, not a hardcoded figure.
- [x] Class-performance ratings are nullable, constrained to 1-10, unique per active
  subject enrollment, and immutable outside a running semester.
- [x] Assessment marks and assignment submissions belong to students on the same class roster.
- [x] Calendar entries store the Gregorian date and derive Bikram Sambat from a
  single server-side table, so a date cannot be saved against one day and drawn
  on another.
- [x] A date may carry several calendar entries, but not two with the same title.
- [x] At least one weekday must remain a teaching day.

## Academic calendar

Teaching-day integration is documented in [calendar-integration.md](calendar-integration.md).
Assessment and assignment dates remain independent of the academic calendar.
Attendance rejects configured weekends and active holidays, without timetable or
makeup overrides. Its BS/AD picker shares Academics' conversion and closure data.

- [x] The year grid is built server-side in the requested system: Bikram Sambat
  renders real BS months (29-32 days, correct starting weekday), Gregorian
  renders January to December.
- [x] Conversion covers BS 1975-2100; a date outside it is a field-level 400
  rather than a 500.
- [x] Staff read the calendar with `view_academic_calendar`; only a superuser may
  set the weekend or mark a date.
- [x] Students read the same year through the student portal, which applies the
  portal's own conditions (login enabled, still studying, temporary password
  replaced) before returning anything, and are refused the staff endpoints.
- [x] Removing a calendar entry archives it and stamps the acting user.
- [x] The calendar palette is a tenant setting a superuser alone may change;
  colours are validated as six-digit hex and stored in one casing.
- [x] The palette travels on the year payload, so a student sees the college's
  colours without reaching the staff settings endpoint.
- [x] Saving the weekend and saving the theme write to one row without
  resetting each other.
- [x] Inactive entries are excluded from staff and student year grids; the
  management list retains them for correction and reactivation.
- [x] Invalid or reversed entry date filters return field-level 400 responses.
- [x] Default years use Django's local date; model writes also reject dates
  outside the conversion table. Corrections preserve the creator and audit actor.
- [x] Calendar weekday headings follow the saved weekend policy. Settings load
  failures offer retry, and weekend validation errors remain visible in the form.
- [x] Staff and students can download selected calendar months across BS years
  as an A4 PDF without letterhead. Exports use authorized calendar responses,
  preserve Nepali text, exclude inactive entries, and paginate long event notes.

## Internal evaluation sheets

- [x] College identity has tenant-local admin CRUD, soft archive, singleton
  constraints and attributed history. No college identity ID is accepted by exports.
- [x] Internal full marks default to 40, pass marks to 16; model, serializer and
  database enforce valid ranges and pass marks not exceeding full marks.
- [x] Teacher workspaces and management allocation reports offer blank and
  calculated sheets. Server downloads always use the complete authorized roster.
- [x] Exact weighted scaling rounds up; missing enabled evidence blocks calculated
  downloads and is listed by student, while blank sheets remain available.
- [x] Guessed peer/department/program allocation IDs, revoked authority/permissions,
  student identities with staff flags, and unauthenticated requests are rejected.
- [x] Administrators can preview and download another teacher's class in both
  blank and calculated modes; archived allocation IDs still return 404.
- [x] Archived evidence/roster rows are excluded; completed classes remain readable.
  Downloading never mutates grades, weights or correction history.
- [x] A4 layout follows the supplied register: headings, year/part, figures/words,
  status rows, red circles for failures/absence, pagination and signatures.
  An embedded, shaped Nepali font preserves Devanagari college/student names.
- [x] Synthetic browser checks cover downloads, server refusals after preview,
  institution create/edit with no archive action, setup errors, permission gating
  and mobile sizing.

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
- [x] Protected screens wait for session validation. Expired access cookies can
  recover through refresh; temporary network failures provide retry.
- [x] Late requests and refreshes cannot cross an account switch. Account or
  authority changes and logout clear cached protected records.
- [x] Returning from the CSV file picker preserves the student import dialog
  and pending preview during session checks. Unchanged authority refreshes data;
  account or authority changes still clear protected caches. Preview errors stay
  visible and only an explicit import writes rows.
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
- [x] Blank attendance dates, weekends and holidays do not count as absences.
- [x] Today’s expected/pending class counts exclude college closures; extra classes
  do not clear another class’s pending reminder. Archived sessions are excluded
  consistently from class, student and dashboard attendance percentages.
- [x] New attendance starts unmarked and cannot save until every student is marked.
- [x] Lists provide loading, error, empty, search/filter, and responsive overflow states.
- [x] Assessments, assignments, and class performance are available only in the
  teacher workspace and remain read-only for non-running semesters.
- [x] Student navigation exposes only the student's dashboard, profile and account
  actions; staff dashboard and management routes explicitly reject the Student role.
- [x] Student portal separates current/upcoming/completed subjects, retains empty
  profiles and progression, and supports subject search and semester filtering.
- [x] Subject records show dated marks, pass lines, assignment deadlines/feedback,
  latest rating and timetable, plus paginated own attendance with unmarked states.
- [x] Attendance eligibility is per subject, and dashboard progress averages only
  current active subjects with evidence. Missing scores remain distinct from zero.
- [x] Student identity is read-only in the portal so account-profile edits cannot
  diverge from the authoritative student record.
- [x] Missing marks and ratings remain "not recorded" and are not presented or
  calculated as zero evidence in the student dashboard; the same applies when no
  attendance sessions have been held.

## Release verification

Run before deployment:

```bash
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py migrate --plan
venv/bin/python manage.py check
venv/bin/ruff check .
venv/bin/ruff format --check .
venv/bin/mypy .
venv/bin/pytest -q

cd spas-frontend
yarn verify
yarn build
yarn test:internal-evaluation  # synthetic sheet downloads and institution CRUD
yarn test:student-portal  # synthetic student screens and isolation regression
yarn test:session  # Node 22+ and Chrome; synthetic browser regression scenarios
yarn test:assignments  # teacher editor/resources, manual evaluation, student visibility
yarn test:responsive  # teacher/student layouts and calendar controls
```

Apply shared and tenant migrations using the deployment process documented in
`docs/tenant-management.md`. Back up PostgreSQL before applying migrations in
production.
