# Operon — SPAS

Multi-tenant student performance and academic administration system. The
Django backend is in this repository; the React frontend is in
[`classmates-fe/`](classmates-fe/).

## First-time setup

Prerequisites: Python, PostgreSQL, Node.js, and Yarn 1.x.

### 1. Configure the backend

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements/dev.txt
pre-commit install

cp .env.example .env
createdb operon_db
python manage.py migrate_schemas --shared
```

Update `.env` if your PostgreSQL connection differs from the provided
development defaults.

### 2. Create the platform administrator

The platform administrator manages colleges from the public control plane.

```bash
python manage.py create_platform_user platform-admin 'admin@123' \
  --is_platform_admin
```

Run the backend and open <http://localhost:8000/dashboard>.

```bash
python manage.py runserver 0.0.0.0:8000
```

### 3. Register the first college and its administrator

This creates the tenant schema and domain, applies its migrations, seeds roles
and permissions, and creates the college's first superuser. It is safe to run
again if setup was interrupted.

```bash
python manage.py register_college sunrise "Sunrise College" \
  --admin-username principal \
  --admin-email principal@sunrise.edu \
  --admin-password 'admin@123'
```

The default domain is `sunrise.localhost`; use `--domain` for another host.

Do not create every staff member from the command line. Sign in as the college
administrator and use **Administration → Accounts & Roles** to register
department heads, coordinators, teachers, and other users with the appropriate
roles.

### 4. Configure the frontend

```bash
cd classmates-fe
yarn install
cp .env.example .env
yarn dev
```

Open <http://sunrise.localhost:3000>, not bare `localhost:3000`, and sign in
with the college administrator created above.

Optional development data:

```bash
python manage.py seed_demo_data sunrise --students 24 --weeks 6
```

## Applying migrations

After pulling backend model or migration changes:

```bash
source venv/bin/activate
python manage.py migrate_schemas --shared  # public/shared schema
python manage.py migrate_schemas           # all college schemas
```

To migrate only one college:

```bash
python manage.py migrate_schemas --schema=sunrise
```

## Documentation

- [Architecture and domain model](docs/architecture.md)
- [Development and verification](docs/development.md)
- [Tenant and account operations](docs/tenant-management.md)
- [API design](docs/api-design.md)
- [Naming conventions](docs/naming-conventions.md)
