"""Small boundary validators shared by hand-written query endpoints."""

from rest_framework import serializers


def positive_query_id(value, field: str) -> int:
    """Validate before ORM lookup so malformed ids produce a field-level 400."""
    try:
        return int(serializers.IntegerField(min_value=1, max_value=2**63 - 1).run_validation(value))
    except serializers.ValidationError as error:
        raise serializers.ValidationError({field: error.detail}) from error
