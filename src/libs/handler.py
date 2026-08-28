import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler

from .request_context import get_request_context

exception_logger = logging.getLogger("exception_error")
validation_logger = logging.getLogger("validation_error")


def _as_drf_validation_error(exc: DjangoValidationError) -> ValidationError:
    """
    Translate a model-layer ValidationError into the DRF one.

    Models call full_clean() in save(), so clean() and constraint violations
    surface as django.core ValidationError. DRF does not know that class and
    would return 500 for what is a client error.
    """
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)

    return ValidationError({"error": list(exc.messages)})


def custom_exception_handler(exc, context):
    ctx = get_request_context() or {}

    if isinstance(exc, DjangoValidationError):
        exc = _as_drf_validation_error(exc)

    # Validation errors (expected)
    # -------------------------
    if isinstance(exc, ValidationError):
        validation_logger.warning(
            "validation error",
            extra={
                "request_id": ctx.get("request_id", "-"),
                "schema_name": ctx.get("schema_name", "-"),
                "method": ctx.get("method", "-"),
                "path": ctx.get("path", "-"),
                "status_code": 400,
                "duration_ms": ctx.get("duration_ms", "-"),
                "detail": getattr(exc, "detail", str(exc)),
            },
        )

    # System errors (unexpected)
    # -------------------------
    else:
        exception_logger.exception(
            "api exception",
            extra={
                "request_id": ctx.get("request_id", "-"),
                "schema_name": ctx.get("schema_name", "-"),
                "method": ctx.get("method", "-"),
                "path": ctx.get("path", "-"),
            },
        )

    response = exception_handler(exc, context)

    if response is not None and isinstance(response.data, dict):
        response.data.setdefault("success", False)

    return response
