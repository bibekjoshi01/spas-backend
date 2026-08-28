# Tenant and account operations

## Account types

SPAS separates public platform accounts from accounts inside a college.

| Account | Scope | Creation path |
|---|---|---|
| Platform administrator | Public control plane and college management | `create_platform_user` |
| College administrator | One tenant with full college access | `register_college` |
| College staff user | One tenant with assigned roles and permissions | College admin in **Administration → Accounts & Roles** |

## Register a college

Use the idempotent registration command instead of manually creating tenant
and domain rows:

```bash
python manage.py register_college <schema> "<college name>" \
  --admin-username <username> \
  --admin-email <email> \
  --admin-password '<password>'
```

Optional `--domain` overrides `<schema>.localhost`. The command creates or
reuses the schema and domain, migrates the tenant, seeds roles and permissions,
and creates the first college administrator.

## Register other college users

After setup, the college administrator signs into the frontend and opens
**Administration → Accounts & Roles**. The administrator creates each login
and assigns the appropriate department-head, coordinator, teacher, or other
available role.

This is the normal registration flow. It keeps role assignment visible and
auditable and avoids giving deployment-shell access to college staff.

For recovery or automation only, an operator can create an account directly:

```bash
python manage.py create_tenant_user <schema> <username> <password> <email>
```

Add `--is_staff` for Django admin access or `--is_superuser` for unrestricted
tenant access. A directly created regular user does not automatically receive a
business role; assign roles through Accounts & Roles before handover.

## Migrations

```bash
python manage.py migrate_schemas --shared          # public schema
python manage.py migrate_schemas                   # every tenant
python manage.py migrate_schemas --schema=sunrise  # one tenant
```

Apply both shared and tenant migrations after deploying schema changes.

## Access and lifecycle

- Platform dashboard: `http://localhost:8000/dashboard`
- College frontend: `http://<schema>.localhost:3000`
- College Django admin: `http://<schema>.localhost:8000/cms/`

Use the platform dashboard to activate or suspend colleges. Archive or
deactivate college users through the tenant administration interface so their
existing academic history is preserved.
