# Backend QA — 6 September 2026

## Scope

Reviewed tenant schema selection and authentication, account/role APIs, academic
structure and lifecycle validation, enrollment and assessment writes, dashboard
and report scopes, password recovery, generated API documentation, and CI.
Changes improve existing behavior; no new product modules were introduced.

## Corrections

| Area | Problem | Result |
| --- | --- | --- |
| CI | Calendar helpers returned untyped third-party values from functions declared to return integers. | Explicit integer boundaries pass mypy, including a run without its incremental cache. |
| CI visibility | All checks appeared as one failing pipeline step. | Lint/format, typing, Django/migration drift and tests now have separate workflow steps. `make ci` runs the equivalent local checks. |
| Tenant isolation | Password-reset signatures included only a reset-row ID, which can overlap across college schemas. | Reset sessions include the issuing schema and are rejected in another schema before querying reset records. |
| Logout | A signed-in caller could submit a refresh token belonging to another account or college. | Both account ownership and tenant schema must match before blacklisting. |
| Sign-in | Lowercasing usernames could select a different legacy case-distinct account; mixed-case stored names/emails could fail login. | Exact usernames take precedence; case-insensitive fallback succeeds only for a unique identity. Recovery uses the same lookup. |
| Account enumeration | Missing accounts and incorrect passwords returned different error fields. | Both return the same credential error; missing accounts also perform password-hashing work. |
| Role separation | A student with mistakenly attached staff privileges could enter management APIs. | Student identity overrides staff grants, including a mistaken superuser flag, on the protected staff surfaces. Dedicated portal access retains its own checks. |
| Revocation | Teaching dashboards and management attention could remain visible after role permissions were removed. | Reads require a live attendance permission as well as the existing allocation or management scope. |
| Account lifecycle | The archive endpoint bypassed the existing restriction on changing superuser status. | Superusers cannot be archived through normal staff-account management. |
| Query validation | Hand-written ID filters could crash or silently ignore malformed values. | Roster, performance, report and audit ID parameters return field errors; opaque out-of-scope object lookups remain 404. |
| Academic consistency | Program length and subject semester edits could contradict existing classes. | A program cannot shrink below existing curriculum/cohort semesters; allocated subjects must continue matching their classes. |
| History | Class identity edits ignored archived roster/performance records. | Archived records also prevent changing the class's subject or semester identity. |
| Assessment scores | Reducing full marks could leave recorded scores above the new maximum. | Invalid scale changes are rejected; scale edits and grade writes share an exam-row lock and grade writes recheck the latest scale. |
| Account schema | Three allowed 100-character name components did not fit the 100-character full-name column. | Current and historical account full names hold 302 characters, preserving student/account consistency. |
| OpenAPI | The custom tenant authenticator was absent from generated security requirements. | Protected tenant operations explicitly declare the JWT bearer scheme; login remains anonymously accessible. |

## Regression coverage and checks

`tests/test_backend_qa.py` adds 19 regression tests covering the corrected flows,
including successful operations, rejection without partial writes, audit
attribution, stale assessment forms, role revocation, and schema-bound recovery.
`tests/test_openapi_schema.py` checks the generated authentication contract.

The full pipeline passed with 318 tests; the additional OpenAPI regression test
also passed. A final disabled-account identity regression passed separately
(320 distinct tests in total). It used the repository's pinned Ruff 0.15.5 and mypy 2.1.0,
Django 5.2, and PostgreSQL. Additional checks include a clean mypy run, the
read-only migration plan, and OpenAPI generation with `--validate --fail-on-warn`
(zero schema warnings or errors).

The GitHub run inspected was
[CI run 34037445646](https://github.com/bibekjoshi01/spas-backend/actions/runs/34037445646)
for `5ac80f7`. Its pipeline step failed; the two calendar typing errors were
reproduced locally. GitHub must run the changed code after it is pushed; a local
pass does not change the status of that earlier run.

## Deployment notes

New migration: `user.0013_widen_full_name`. It widens `User.full_name` and
`HistoricalUser.full_name` from 100 to 302 characters without deleting rows.
Apply it through the normal backup/migration deployment process before accepting
long names. No application/production migrations were applied during this QA.
Test schema migrations were applied only by the test suite.

The local migration plan also lists the earlier pending
`performance.0009_attendancesession_makeup_reason_and_more` and
`students.0009_track_batch_graduation`; those are not new migrations from this QA.

Already-issued password-reset sessions lacking a tenant claim are intentionally
rejected. A user in that short recovery flow should verify/request a fresh code.
Ordinary access and refresh token formats are unchanged.

OpenAPI weekday names and unused generic audit filters were also corrected,
leaving schema generation warning-free. The inspected GitHub run still warns
about the existing checkout/setup-python actions' Node runtime; this was not the
reproduced typing failure. This review is not a guarantee against every possible
production race or deployment misconfiguration.
