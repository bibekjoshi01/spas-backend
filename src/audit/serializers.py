"""
The shapes an audit trail is read in.

These describe rather than validate: the entries are assembled from historical
snapshots rather than written, so the serializers exist to pin the response
contract and to document it in the schema.
"""

from rest_framework import serializers


class AuditResourceSerializer(serializers.Serializer):
    """One openable trail, for the picker."""

    slug = serializers.CharField()
    label = serializers.CharField()


class AuditChangeSerializer(serializers.Serializer):
    """One field that moved, already rendered as words."""

    field = serializers.CharField()
    label = serializers.CharField()
    # Null where the field was empty on that side of the change, which is a
    # different statement from an empty string and is drawn differently.
    to = serializers.CharField(allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # "from" is a Python keyword, so it cannot be declared as an attribute.
        self.fields["from"] = serializers.CharField(allow_null=True)


class AuditActorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()


class AuditEntrySerializer(serializers.Serializer):
    """One recorded change to one record."""

    id = serializers.CharField()
    resource = serializers.CharField()
    resource_label = serializers.CharField()
    object_id = serializers.IntegerField()
    object_label = serializers.CharField()
    action = serializers.ChoiceField(choices=("CREATED", "UPDATED", "DELETED"))
    at = serializers.DateTimeField()
    #: Null for a change made outside a request — a data migration or a shell.
    actor = AuditActorSerializer(allow_null=True)
    changes = AuditChangeSerializer(many=True)
