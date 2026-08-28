# API design

These are the conventions this codebase actually implements. Follow them rather
than inventing per-endpoint shapes.

## Layout

| Path | Schema | Auth |
|---|---|---|
| `/api/v1/internal/<module>-mod/<resource>/` | college | required |
| `/api/v1/external/<resource>` | public | anonymous, throttled |
| `/dashboard` | public | platform administrator |
| `/cms/` | college | Django admin |

Modules are `user-mod`, `academics-mod`, `students-mod`, and `performance-mod`.
The request hostname selects the schema, so never accept a tenant ID in a
payload.

## Format

JSON is **camelCase at the HTTP boundary and snake_case in Python** —
`djangorestframework_camel_case` translates both directions, so serializers stay
snake_case. Numbers keep their own segment (`address1`, not `address_1`).

## Authentication and authorization

JWT bearer tokens (`rest_framework_simplejwt`) with session auth as a fallback.
`IsAuthenticated` is the default; each resource then declares a
`ModelPermission` subclass mapping HTTP method to a permission codename, with
`SAFE_METHODS` covering every read.

Permissions decide *what*; queryset scoping decides *which rows*. Both are
mandatory — see `AuthorityScopedMixin`, `scope_by_authority`, and
`scope_to_allocation_owner`. Apply the same authority check to POST and
bulk-write foreign keys; a scoped list queryset alone is not enough.

## Lists

`CustomLimitOffsetPagination`, page size 10:

```
GET /api/v1/internal/students-mod/students/?limit=20&offset=40
```

```json
{ "count": 128, "next": "...", "previous": "...", "results": [] }
```

`limit=0` returns every row the caller is authorized to see — selector dropdowns
and exports depend on this. Plain `LimitOffsetPagination` would silently
truncate to the page size instead.

Use the DRF backends rather than ad-hoc query parsing: `filterset_fields` for
`?field=value`, `search_fields` for `?search=`, and `ordering_fields` for
`?ordering=-created_at`.

Keep list serializers compact and retrieve serializers detailed.

## Writes

Create and update answer with a message and the row id; delete and archive
answer with a message only.

```json
{ "message": "Student created successfully.", "id": 42 }
{ "message": "Student archived successfully." }
```

Deletes are soft archives. Academic and attendance history is never physically
removed through the API.

## Errors

Field-keyed, so a form can attach each message to its input. The exception
handler adds `success: false` and translates model-layer `ValidationError` —
raised by `full_clean()` in `save()` — into a 400 instead of a 500.

```json
{ "date": ["A class cannot be recorded for a future date."], "success": false }
```

| Status | Meaning |
|---|---|
| 400 | Validation failure, with actionable field errors |
| 401 | Missing or expired credentials |
| 403 | Authenticated but lacks the permission |
| 404 | No such row, **or** one outside the caller's authority |

Return 404 rather than 403 for out-of-scope lookups: a 403 confirms the row
exists. Never disclose whether another scope's record exists.

## Changing the surface

Do not add fields silently. Update the serializer, the OpenAPI annotations, the
frontend types, and the tests together. Authorization changes need positive and
negative tests, including a guessed cross-scope ID.
