# Development and verification

## Checks

```bash
make ci
```

Runs Ruff check, Ruff format check, mypy, and pytest — the same gate CI uses.
Individual targets:

```bash
make lint   # ruff check + ruff format
make type   # mypy
make test   # pytest
```

Run the narrow relevant tests while developing, then `make ci` before handing
work over.

Release checks that `make ci` does not cover:

```bash
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py check
```

## Frontend checks

The frontend is a separate repository. From `spas-frontend/`:

```bash
yarn verify   # typecheck + lint + format check
yarn build
```

## Local hosts

Backend on port 8000, frontend on 3000. For a college with schema `sunrise`:

| Surface | URL |
|---|---|
| Platform dashboard | `http://localhost:8000/dashboard` |
| College app | `http://sunrise.localhost:3000` |
| College API | `http://sunrise.localhost:8000/api/v1/internal` |
| College Django admin | `http://sunrise.localhost:8000/cms/` |

The hostname selects the schema, so always browse the subdomain. Set
`VITE_API_URL` in the frontend to target one college's API directly when
hostname derivation is unsuitable.

In DEBUG, authenticated users can read `/api/schema/` and `/api/docs/` on a
college host.

## Fixtures and demo data

Load a fixture into every college, only when it is designed for tenant schemas:

```bash
python manage.py all_tenants_command loaddata <fixture>
```

`seed_demo_data` is for development and demonstration environments only.
