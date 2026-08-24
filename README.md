# Operon — SPAS

Student Performance Analysis System for colleges, delivered as multi-tenant
SaaS. Each college gets its own Postgres schema and its own subdomain; the host
a request arrives on is what selects the college, so nothing carries a tenant id
in a payload.

The repo holds the Django backend. The React frontend lives in
[`classmates-fe/`](classmates-fe) as its own git repository.

---

## Running it

You need two servers and a Postgres database. Every college is a subdomain of
`localhost` in development — `*.localhost` resolves to 127.0.0.1 on macOS and in
modern browsers, so there is nothing to add to `/etc/hosts`.

### 1. Backend

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements/dev.txt
pre-commit install

cp .env.example .env          # then fill in DB_NAME, DB_USER, DB_PASSWORD
createdb operon_db

python manage.py migrate_schemas --shared
python manage.py runserver 0.0.0.0:8000
```

### 2. Register a college

One command creates the schema, maps the subdomain, runs the migrations, seeds
the permission catalogue and the five roles, and creates the first admin. It is
safe to re-run.

```bash
python manage.py register_college sunrise "Sunrise College" \
  --admin-username principal --admin-password 'Principal@123'
```

```
  created  schema  sunrise
  created  domain  sunrise.localhost
  migrations applied
  created  admin   principal
  created  roles   5 seeded

Sunrise College is ready.
  app  http://sunrise.localhost:3000
  api  http://sunrise.localhost:8000/api/v1/internal
  cms  http://sunrise.localhost:8000/cms/
```

Pass `--domain` to serve a college from something other than
`<schema>.localhost`.

### 3. Give it something to look at

```bash
python manage.py seed_demo_data sunrise --students 24 --weeks 6
```

Seeds one department, one program, a 2079 batch through three semesters, four
classes, 24 students and six weeks of attendance, marks and assignments.
Teachers `rshrestha` and `sthapa` are created with password `Teacher!2345`.

### 4. Frontend

```bash
cd classmates-fe
yarn install
cp .env.example .env
yarn dev
```

Then open **http://sunrise.localhost:3000** — not `localhost:3000`. The app
reads the college from its own hostname and calls
`sunrise.localhost:8000` for data. Opening a bare `localhost:3000` tells you so
rather than failing every request.

Sign in as `principal` / `Principal@123` to see everything, or as `rshrestha` /
`Teacher!2345` to see a teacher's view — the same screens scoped to their own
two classes.

---

## How multi-tenancy works

| | |
|---|---|
| Host → schema | `django_tenants` resolves the `Host` header against `tenants.Domain` before URL resolution |
| Public schema | `config.platform_urls` — the control plane, `/healthz`, the tenant dashboard |
| Tenant schema | `config.tenant_urls` — `/api/v1/internal`, the Django admin at `/cms/` |
| Frontend | reads the slug from `window.location.hostname`, derives the API host from it |
| CORS | one regex covers every subdomain of the app domain, so a new college needs no config change |

`VITE_API_URL` in the frontend `.env` short-circuits the derivation, which is
how you point a local frontend at one specific college.

---

## The data model

Sixteen tables across three modules, with a one-way dependency:
`performance → students → academics`.

Two rows carry the design. **SubjectAllocation** is a class — one subject taught
to one batch in one semester by one teacher. **SubjectEnrollment** is one
student on that class's roster. Attendance, exam marks and assignment statuses
all point at both, so a mark cannot be recorded for a student who is not taking
the subject.

Promotion never rewrites a student. A `Student` row is the admission record;
where they study each semester is a `SemesterEnrollment`, so moving a batch
forward is an insert. The same mechanism covers repeats and back papers.

---

## The API

41 endpoints under `/api/v1/internal`, plus three aggregate reads.

| Module | Path | What it holds |
|---|---|---|
| `user-mod` | `/account/*`, `/users`, `/roles`, `/permissions` | auth, staff accounts, the permission catalogue |
| `academics-mod` | `/departments` … `/allocations` | the academic structure |
| `students-mod` | `/students`, `/*-enrollments`, `/*/bulk` | admission, promotion, rosters |
| `performance-mod` | `/attendance-sessions`, `/internal-exams`, `/assignments`, `/roster` | what teachers record |
| `performance-mod` | `/analytics/*` | attendance %, mark totals, dashboard |

Conventions: no trailing slashes, camelCase on the wire, PATCH never PUT, writes
answer `{message, id}`, delete archives rather than destroys, lists are
limit/offset paginated.

Browsable schema at `/api/schema/` and Swagger at `/api/docs/` — both DEBUG
only, and both require a login.

### Permissions

Authorization runs on codenames, not Django's permission table. Every API
operation has one, named `<verb>_<resource>` with verbs `view`, `add`, `edit`,
`delete`. A login response carries every codename the user holds, which is what
the frontend gates its menus on.

Five roles ship seeded: `SYSTEM-USER`, `PUBLIC-USER`, `DEPARTMENT-HEAD`,
`PROGRAM-COORDINATOR`, `TEACHER`.

Teacher visibility has one rule: **whoever may allocate classes sees everything
recorded against them; everyone else sees what is allocated to them.** Lists
arrive already scoped — the client never sends a teacher filter.

---

## Development

```bash
make ci        # ruff check · ruff format --check · mypy · pytest
make lint
make test
```

`make ci` is the contract — if it passes locally it is safe to push. It runs on
every push and PR against a real Postgres 16.

### Per-tenant operations

```bash
python manage.py migrate_schemas --shared              # shared apps
python manage.py migrate_schemas --schema=sunrise      # one college
python manage.py migrate_schemas                       # every college
python manage.py all_tenants_command loaddata <fixture>
python manage.py create_tenant_user sunrise <username> <password> <email>
```

### Layout

```
config/          settings, both URL confs, celery, logging
tenants/         Tenant and Domain, the register_college command
control_plane/   the platform-side dashboard (public schema)
src/academics/   Department, Program, Batch, BatchSemester, Subject, Teacher, Allocation
src/students/    Student, SemesterEnrollment, SubjectEnrollment
src/performance/ attendance, internal exams, assignments, analytics
src/user/        auth, roles, the permission catalogue
src/libs/        middleware, permissions, exception handler, tenant-aware Celery
reference/       read-only example module from another project — not installed
```
