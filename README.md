# Operon — SPAS

Multi-tenant academic administration and student-performance backend. Each
college is one PostgreSQL schema, resolved from the request hostname.

- [Architecture and domain model](docs/architecture.md)
- [Development and verification](docs/development.md)
- [Tenant and account operations](docs/tenant-management.md)
- [API design](docs/api-design.md)
- [Naming conventions](docs/naming-conventions.md)
- [Production readiness checklist](docs/qa-production-readiness.md)

## Requirements

Python 3.12, PostgreSQL 14+, Node.js 20.19+, and Yarn 1.x.

## Setup

### 1. Backend

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements/dev.txt
pre-commit install

cp .env.example .env
createdb operon_db
python manage.py migrate_schemas --shared
```

Adjust `.env` if your PostgreSQL connection differs from the defaults.

### 2. Public control plane

`migrate_schemas --shared` builds the public schema, but nothing yet maps a
hostname to it. Without this step `localhost:8000` has no tenant to resolve and
every request 404s.

```bash
python manage.py bootstrap_platform
python manage.py create_platform_user platform-admin 'admin@123' --is_platform_admin
```

`bootstrap_platform` defaults to host `localhost`; use `--domain` for another.
Both commands are safe to re-run.

### 3. First college

Creates the schema and domain, migrates it, seeds roles and permissions, and
creates the college's first superuser. Safe to re-run.

```bash
python manage.py register_college sunrise "Sunrise College" \
  --admin-username principal \
  --admin-email principal@sunrise.edu \
  --admin-password 'admin@123'
```

The domain defaults to `sunrise.localhost`; use `--domain` to override.

Create further staff through **Administration → Accounts & Roles** in the app,
not the command line, so role assignment stays audited.

### 4. Frontend

The frontend is a separate repository, [spas-frontend](https://github.com/bibekjoshi01/spas-frontend).

```bash
cd spas-frontend
yarn install
cp .env.example .env
yarn dev
```

### 5. Run

```bash
python manage.py runserver 0.0.0.0:8000
```

| Surface | URL |
|---|---|
| Platform dashboard | <http://localhost:8000/dashboard> |
| College app | `http://sunrise.localhost:3000` |
| College API | `http://sunrise.localhost:8000/api/v1/internal` |
| College Django admin | `http://sunrise.localhost:8000/cms/` |

Browse a college on its subdomain, never bare `localhost:3000` — the hostname
is what selects the schema. `*.localhost` resolves without an `/etc/hosts`
entry in modern browsers.

Optional development data:

```bash
python manage.py seed_demo_data sunrise --students 24 --weeks 6
```

## Verification

```bash
make ci
```

Runs Ruff check and format, mypy, and pytest. See
[docs/development.md](docs/development.md).

## Applying migrations

```bash
python manage.py migrate_schemas --shared          # public schema
python manage.py migrate_schemas                   # every college
python manage.py migrate_schemas --schema=sunrise  # one college
```

Apply both shared and tenant migrations after deploying schema changes. Back up
PostgreSQL first in production.
