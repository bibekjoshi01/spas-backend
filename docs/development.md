# Development and verification

## Backend checks

```bash
make ci
```

This runs Ruff checks, formatting verification, mypy, and pytest. Individual
commands are also available:

```bash
make lint
make type
make test
```

Run narrow relevant tests first while developing, followed by the complete
suite when practical.

## Frontend checks

Run from `classmates-fe/`:

```bash
yarn typecheck
yarn lint
yarn build
```

## Local hosts

Run the backend on port 8000 and frontend on port 3000. For a tenant named
`sunrise`:

- Frontend: `http://sunrise.localhost:3000`
- API: `http://sunrise.localhost:8000/api/v1/internal`
- Tenant Django admin: `http://sunrise.localhost:8000/cms/`

`VITE_API_URL` can point the frontend at a specific tenant API when hostname
derivation is unsuitable.

## Fixtures

Load a tenant fixture into every tenant only when it is designed for tenant
schemas:

```bash
python manage.py all_tenants_command loaddata <fixture>
```

Use `seed_demo_data` only in development or demonstration environments.
