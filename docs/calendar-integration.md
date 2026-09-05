# Academic calendar and attendance

Attendance uses the college's weekend settings and active holidays from Academics.
Events, class timetables, and legacy makeup/cancellation records do not block or
permit attendance. Exams and assignments remain independent of the calendar.

## Behavior

- Configured weekend days and active, unarchived holidays reject attendance writes
  with a field-level `date` error. A makeup reason cannot override a closure.
- Every other day permits attendance subject to the existing owner permissions,
  running-semester lifecycle, optional semester bounds, and no-future-date rules.
- Later calendar changes never delete recorded attendance or alter percentages.
  If a recorded day becomes closed, it remains readable, but cannot be edited
  while closed. Corrections retain creator and history attribution.
- The attendance picker displays BS and AD dates together using the same server
  conversion table as Academics. Red denotes closures; a green dot denotes held
  attendance. A blank date never counts as an absence.
- Dashboard reminders still follow the timetable and are suppressed on closures.
  Timetable slots do not restrict attendance recording on other open days.

## API and scope

`GET /api/v1/internal/performance-mod/calendar/class` requires `view_attendance`
and an `allocation` owned by the caller, matching attendance's existing scope.
Guessed allocations outside the caller's scope return 404.

| Query | Response |
|---|---|
| `date` | `date`, `label`, `isWeekend`, `holidayTitles`, `isExpected` (open day) |
| `system=BS`, optional `year` or Gregorian `anchor` | Same year/month/day format as the academic calendar, with saved events and holidays |
| `date_from`, `date_to` | Ordered `days`, maximum 367 inclusive dates |

The year defaults to the anchor's year or today's year. Unsupported years/dates
return 400. Dates remain Gregorian at the API boundary. Staff and student academic
calendars exclude assessments, assignments, and legacy class-schedule changes.

The class-schedule API and UI have been retired. Existing schedule and reason
history is preserved; it has no effect on attendance eligibility. Migration
`performance.0009_attendancesession_makeup_reason_and_more` remains in the released
graph for upgrade compatibility. This simplification adds no migration and does
not apply migrations to any college database.
