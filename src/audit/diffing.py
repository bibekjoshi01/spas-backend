"""
Turning two historical rows into a line a person can read.

`simple_history` stores a full snapshot per save, so "what changed" is a
comparison the reader should never have to do themselves. This module makes that
comparison, drops the fields that change on every save regardless, and renders
the remaining values as words rather than raw column data.
"""

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal

from django.db import models

#: Bookkeeping that changes on every single save and tells a reader nothing.
#: The actor and the timestamp are not dropped — they are reported as the
#: entry's own `actor` and `at`, which is where a reader looks for them.
NOISE_FIELDS = frozenset(
    {
        "id",
        "uuid",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
    }
)

ACTIONS = {"+": "CREATED", "~": "UPDATED", "-": "DELETED"}


def action_for(history_type: str) -> str:
    return ACTIONS.get(history_type, "UPDATED")


def tracked_fields(model) -> list:
    """The model's own fields, minus the history plumbing and the noise."""
    return [
        field
        for field in model._meta.fields
        if not field.name.startswith("history_") and field.name not in NOISE_FIELDS
    ]


def _raw(record, field):
    """The stored value, following FKs by id rather than fetching the object."""
    return getattr(record, field.attname, None)


def changed_fields(current, previous, fields) -> list:
    """
    Which of `fields` differ between two snapshots of the same row.

    A create has no previous snapshot, and listing every field of a new row as a
    "change" would bury the ones that matter under the defaults, so a create
    reports no field changes at all — the entry itself is the information.
    """
    if previous is None:
        return []
    return [field for field in fields if _raw(current, field) != _raw(previous, field)]


class ValueRenderer:
    """
    Renders stored values as words, resolving foreign keys in batches.

    A field-by-field lookup would put a query behind every changed row on the
    page. Collecting the referenced ids first means one query per related model
    for the whole page, however many entries mention it.
    """

    def __init__(self):
        self._labels: dict[tuple[type, object], str] = {}

    def collect(self, records, fields) -> None:
        """Note every foreign key id the page will need a name for."""
        wanted: dict[type, set] = defaultdict(set)
        for record in records:
            for field in fields:
                if not field.is_relation:
                    continue
                value = _raw(record, field)
                if value is not None:
                    wanted[field.related_model].add(value)

        for model, ids in wanted.items():
            for row in model._default_manager.filter(pk__in=ids):
                self._labels[(model, row.pk)] = str(row)

    def render(self, field, value):
        if value is None or value == "":
            return None

        if field.is_relation:
            # A row deleted since the change was made has no name left to show,
            # so the id stands in rather than the change disappearing.
            return self._labels.get((field.related_model, value), f"#{value}")

        if field.choices:
            return dict(field.choices).get(value, str(value))

        if isinstance(field, models.BooleanField):
            return "Yes" if value else "No"

        if isinstance(value, datetime):
            return value.isoformat(timespec="minutes")

        if isinstance(value, (date, time)):
            return value.isoformat()

        if isinstance(value, Decimal):
            return f"{value.normalize():f}"

        return str(value)

    def describe(self, field, current, previous) -> dict:
        return {
            "field": field.name,
            "label": str(field.verbose_name).capitalize(),
            "from": self.render(field, _raw(previous, field)),
            "to": self.render(field, _raw(current, field)),
        }
