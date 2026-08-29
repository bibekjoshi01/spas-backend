"""Bootstrap the public control plane: its tenant row and the host that serves it."""

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name

from tenants.models import Domain, Tenant


class Command(BaseCommand):
    help = (
        "Create the public tenant and the host that serves the platform control "
        "plane. Run once after 'migrate_schemas --shared'. Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            default="localhost",
            help="Host that serves the control plane. Defaults to localhost",
        )
        parser.add_argument(
            "--name",
            default="Platform",
            help='Display name for the public tenant. Defaults to "Platform"',
        )

    def handle(self, *args, **options):
        schema_name = get_public_schema_name()
        domain = options["domain"].strip().lower()
        name = options["name"].strip()

        if not domain:
            raise CommandError("--domain cannot be empty.")

        # django-tenants skips schema creation for the public schema, which
        # migrate_schemas --shared has already built.
        tenant, created = Tenant.objects.get_or_create(
            schema_name=schema_name,
            defaults={"name": name, "subdomain": schema_name, "is_active": True},
        )
        self._report("schema", schema_name, created)

        if not created and not tenant.is_active:
            tenant.activate()
            self.stdout.write("  reactivated a suspended platform tenant")

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

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Platform control plane is ready."))
        self.stdout.write(f"  dashboard  http://{domain}:8000/dashboard")

    def _report(self, label: str, value: str, created: bool):
        mark = self.style.SUCCESS("created") if created else "exists "
        self.stdout.write(f"  {mark}  {label:<7} {value}")
