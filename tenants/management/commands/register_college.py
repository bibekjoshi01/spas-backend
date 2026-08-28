"""Register a college: schema, domain, permission catalogue and first admin."""

import json
from pathlib import Path

from django.conf import settings
from django.core import serializers
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from tenants.models import Domain, Tenant

FIXTURES = ("main_module.json", "permission_category.json", "permissions.json")
ROLE_FIXTURE = Path(settings.BASE_DIR) / "src" / "user" / "fixtures" / "user_role.json"


class Command(BaseCommand):
    help = (
        "Create a tenant with its schema, subdomain, seeded permissions and an "
        "admin account. Safe to re-run: existing pieces are left alone."
    )

    def add_arguments(self, parser):
        parser.add_argument("schema_name", help="Postgres schema, e.g. sunrise")
        parser.add_argument("name", help='Display name, e.g. "Sunrise College"')
        parser.add_argument(
            "--domain",
            help="Host that serves this college. Defaults to <schema>.localhost",
        )
        parser.add_argument("--admin-username", default="admin")
        parser.add_argument("--admin-email", default=None)
        parser.add_argument("--admin-password", default="Admin!2345")

    def handle(self, *args, **options):
        schema_name = options["schema_name"].strip().lower()
        name = options["name"].strip()
        domain = (options["domain"] or f"{schema_name}.localhost").strip().lower()

        if schema_name == "public":
            raise CommandError("The public schema is the platform, not a college.")

        tenant, created = Tenant.objects.get_or_create(
            schema_name=schema_name,
            defaults={"name": name, "subdomain": schema_name, "is_active": True},
        )
        self._report("schema", schema_name, created)

        if not created and not tenant.is_active:
            tenant.activate()
            self.stdout.write("  reactivated a suspended tenant")

        existing_domain = Domain.objects.filter(domain=domain).first()
        if existing_domain and existing_domain.tenant_id != tenant.id:
            raise CommandError(
                f"{domain} already points at schema "
                f"'{existing_domain.tenant.schema_name}'. Pick another host or "
                f"move that domain first."
            )

        _, domain_created = Domain.objects.get_or_create(
            domain=domain, defaults={"tenant": tenant, "is_primary": True}
        )
        self._report("domain", domain, domain_created)

        # A tenant created by hand in an earlier release may predate the SPAS
        # apps, so bring the schema up to date either way.
        call_command("migrate_schemas", schema_name=schema_name, verbosity=0)
        self.stdout.write("  migrations applied")

        with schema_context(schema_name):
            self._seed(options)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{name} is ready."))
        self.stdout.write(f"  app  http://{domain}:3000")
        self.stdout.write(f"  api  http://{domain}:8000/api/v1/internal")
        self.stdout.write(f"  cms  http://{domain}:8000/{settings.ADMIN_URL}")
        self.stdout.write(
            f"  sign in as '{options['admin_username']}' / '{options['admin_password']}'"
        )

    def _seed(self, options):
        from src.user.models import User, UserRole

        username = options["admin_username"]
        email = options["admin_email"] or f"{username}@{options['schema_name']}.edu"

        admin = User.objects.filter(username=username).first()
        if admin is None:
            admin = User.objects.create_superuser(
                username=username, email=email, password=options["admin_password"]
            )
            self._report("admin", username, True)
        else:
            self._report("admin", username, False)

        if not UserRole.objects.exists():
            call_command("loaddata", *FIXTURES, verbosity=0)

            # UserRole.created_by is required, and the fixture assumes the first
            # user is pk 1. Bind it to whoever actually administers this college.
            roles = json.loads(ROLE_FIXTURE.read_text())
            for row in roles:
                row["fields"]["created_by"] = admin.pk
            for obj in serializers.deserialize("json", json.dumps(roles)):
                obj.save()

            self._report("roles", f"{UserRole.objects.count()} seeded", True)
        else:
            self._report("roles", f"{UserRole.objects.count()} present", False)

    def _report(self, label: str, value: str, created: bool):
        mark = self.style.SUCCESS("created") if created else "exists "
        self.stdout.write(f"  {mark}  {label:<7} {value}")
