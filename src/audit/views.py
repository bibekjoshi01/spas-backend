"""
Read-only audit trails over the history every domain model already writes.

There is no write surface here by design: an audit trail a user can edit answers
nothing during an audit. The views are read-only all the way down, and the only
question they answer is "who changed this, when, and from what to what".
"""

from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from src.libs.permissions import get_permissions_for_user

from .diffing import ValueRenderer, action_for, changed_fields, tracked_fields
from .registry import BY_SLUG, AuditResource, readable_resources
from .serializers import AuditEntrySerializer, AuditResourceSerializer

#: A page of an audit trail. Small on purpose: each entry carries its own field
#: changes, so a large page is a lot of rows for a screen nobody skims.
MAX_LIMIT = 100


def _resource_for(request, slug: str) -> AuditResource:
    resource = BY_SLUG.get(slug)
    if resource is None:
        raise NotFound("No such audit trail.")
    if resource.permission not in set(get_permissions_for_user(request.user)):
        # The permission that admits someone to the records admits them to the
        # history of those records, and nothing more.
        raise PermissionDenied("You cannot read this audit trail.")
    return resource


def _visible_object_ids(resource: AuditResource, user) -> list[int]:
    """
    The rows of this resource the caller may see, by primary key.

    History is filtered by this rather than scoped again over the historical
    table, so the trail can never widen past the listing it belongs to.
    """
    queryset = resource.model.objects.all()
    return list(resource.scope(queryset, user).values_list("pk", flat=True))


class AuditResourceListView(generics.GenericAPIView):
    """The trails this caller may open, for the picker on the audit screen."""

    permission_classes = (IsAuthenticated,)
    serializer_class = AuditResourceSerializer
    pagination_class = None

    @extend_schema(responses=AuditResourceSerializer(many=True))
    def get(self, request):
        permissions = set(get_permissions_for_user(request.user))
        resources = readable_resources(permissions)
        return Response(AuditResourceSerializer(resources, many=True).data)


class AuditTrailView(generics.GenericAPIView):
    """
    One resource's history, newest first.

    A resource is always required. Merging every model's history into a single
    feed would mean querying and sorting sixteen tables to render twenty rows,
    and the question an audit actually asks is about one kind of record at a
    time.
    """

    permission_classes = (IsAuthenticated,)
    serializer_class = AuditEntrySerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("resource", str, required=True, description="Registry slug."),
            OpenApiParameter("object", int, description="Limit to one record's trail."),
            OpenApiParameter("actor", int, description="Limit to one user's changes."),
            OpenApiParameter("action", str, description="CREATED, UPDATED or DELETED."),
            OpenApiParameter("from", str, description="ISO date, inclusive."),
            OpenApiParameter("to", str, description="ISO date, inclusive."),
        ],
        responses=AuditEntrySerializer(many=True),
    )
    def get(self, request):
        resource = _resource_for(request, request.query_params.get("resource", ""))
        visible_ids = _visible_object_ids(resource, request.user)
        if not visible_ids:
            return self.get_paginated_response([])

        history = resource.model.history.model.objects.filter(id__in=visible_ids)
        history = self._apply_filters(history, request, visible_ids)
        history = history.select_related("history_user").order_by("-history_date", "-history_id")

        page = self.paginate_queryset(history) or []
        return self.get_paginated_response(self._entries(resource, page))

    # Filtering
    # --------------------------------------------------------------------------------

    def _apply_filters(self, history, request, visible_ids):
        params = request.query_params

        object_id = params.get("object")
        if object_id:
            if not object_id.isdigit() or int(object_id) not in set(visible_ids):
                # Out of scope reads as absent, so a trail cannot be used to
                # confirm that a record exists elsewhere in the college.
                raise NotFound("No such record.")
            history = history.filter(id=int(object_id))

        actor = params.get("actor")
        if actor:
            if not actor.isdigit():
                raise ValidationError({"actor": "Expected a user id."})
            history = history.filter(history_user_id=int(actor))

        action = params.get("action")
        if action:
            symbols = {"CREATED": "+", "UPDATED": "~", "DELETED": "-"}
            if action not in symbols:
                raise ValidationError({"action": "Expected CREATED, UPDATED or DELETED."})
            history = history.filter(history_type=symbols[action])

        for name, lookup in (
            ("from", "history_date__date__gte"),
            ("to", "history_date__date__lte"),
        ):
            raw = params.get(name)
            if raw:
                parsed = parse_date(raw)
                if parsed is None:
                    raise ValidationError({name: "Expected a date as YYYY-MM-DD."})
                history = history.filter(**{lookup: parsed})

        return history

    # Rendering
    # --------------------------------------------------------------------------------

    def _entries(self, resource: AuditResource, page) -> list[dict]:
        if not page:
            return []

        fields = tracked_fields(resource.model)
        previous_by_history_id = self._previous_records(resource, page)

        renderer = ValueRenderer()
        renderer.collect(
            [*page, *(row for row in previous_by_history_id.values() if row is not None)], fields
        )

        labels = self._object_labels(resource, {row.id for row in page})

        entries = []
        for record in page:
            previous = previous_by_history_id.get(record.history_id)
            changes = changed_fields(record, previous, fields)
            actor = record.history_user
            entries.append(
                {
                    "id": f"{resource.slug}:{record.history_id}",
                    "resource": resource.slug,
                    "resource_label": resource.label,
                    "object_id": record.id,
                    "object_label": labels.get(record.id, f"#{record.id}"),
                    "action": action_for(record.history_type),
                    "at": record.history_date,
                    "actor": (
                        {
                            "id": actor.id,
                            "full_name": actor.full_name or actor.username,
                        }
                        if actor
                        else None
                    ),
                    "changes": [renderer.describe(field, record, previous) for field in changes],
                }
            )
        return entries

    @staticmethod
    def _previous_records(resource: AuditResource, page) -> dict:
        """
        The snapshot immediately before each row on this page.

        Every row on a page belongs to one of a handful of records, so the prior
        snapshots are fetched per record in one query each rather than one query
        per row. A page showing twenty edits to the same mark costs one query,
        not twenty.
        """
        model = resource.model.history.model
        oldest_by_object: dict[int, object] = {}
        for record in page:
            current = oldest_by_object.get(record.id)
            if current is None or record.history_date < current.history_date:
                oldest_by_object[record.id] = record

        candidates: dict[int, list] = {}
        for object_id, oldest in oldest_by_object.items():
            candidates[object_id] = list(
                model.objects.filter(id=object_id, history_date__lte=oldest.history_date)
                .exclude(history_id=oldest.history_id)
                .order_by("-history_date", "-history_id")[:1]
            )

        # Rows on the page are contiguous per record, so within the page each
        # row's predecessor is the next-older row already in hand; only the
        # oldest of each record needs the extra lookup above.
        by_object: dict[int, list] = {}
        for record in page:
            by_object.setdefault(record.id, []).append(record)

        previous_by_history_id: dict[int, object] = {}
        for object_id, rows in by_object.items():
            ordered = sorted(rows, key=lambda row: (row.history_date, row.history_id))
            tail = candidates.get(object_id) or [None]
            chain = [tail[0], *ordered]
            for index, row in enumerate(ordered):
                previous_by_history_id[row.history_id] = chain[index]
        return previous_by_history_id

    @staticmethod
    def _object_labels(resource: AuditResource, object_ids: set[int]) -> dict[int, str]:
        """
        The line a reader identifies each record by, in one query.

        A record archived since the change was made is still described, because
        the audit question is most often asked about exactly those.
        """
        queryset = resource.model.objects.filter(pk__in=object_ids)
        if resource.related:
            queryset = queryset.select_related(*resource.related)
        return {row.pk: resource.describe(row) for row in queryset}

    def paginate_queryset(self, queryset):
        paginator = self.paginator
        if paginator is not None:
            paginator.max_limit = MAX_LIMIT
        return super().paginate_queryset(queryset)
