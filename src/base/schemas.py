"""Response serializers that exist only to document non-serializer payloads."""

from rest_framework import serializers


class MessageResponseSerializer(serializers.Serializer):
    """The shape every write and delete endpoint answers with."""

    message = serializers.CharField()


class MessageWithIdResponseSerializer(serializers.Serializer):
    """The shape create and update endpoints answer with."""

    message = serializers.CharField()
    id = serializers.IntegerField()
