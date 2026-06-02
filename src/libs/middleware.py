import logging
from contextlib import suppress
from time import perf_counter
from uuid import uuid4

from django.conf import settings
from django.http import JsonResponse

from .request_context import (
    reset_request_context,
    set_request_context,
    set_response_context,
)

access_logger = logging.getLogger("request_access")


class RequestContextMiddleware:
    """
    Adds request context + ensures consistent logging metadata.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _request_id(request):
        incoming = request.headers.get("X-Request-ID", "").strip()

        if incoming and len(incoming) <= 64:
            return incoming

        return uuid4().hex

    def __call__(self, request):
        schema_name = getattr(getattr(request, "tenant", None), "schema_name", "-")

        request_id = self._request_id(request)

        # expose request_id on the request object for downstream code/tests
        with suppress(Exception):
            request.request_id = request_id

        user = getattr(request, "user", None)
        user_id = user.id if user and user.is_authenticated else "-"

        ip_address = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0] or request.META.get(
            "REMOTE_ADDR", "-"
        )

        tokens = set_request_context(
            request_id=request_id,
            schema_name=schema_name,
            method=request.method,
            path=request.path,
            user_id=user_id,
            ip_address=ip_address,
        )

        start = perf_counter()

        status_code = "-"
        response = None

        try:
            response = self.get_response(request)
            status_code = response.status_code

            return response

        except Exception:
            status_code = 500
            raise

        finally:
            duration_ms = round((perf_counter() - start) * 1000, 2)
            set_response_context(status_code=status_code, duration_ms=duration_ms)

            if response is not None:
                response["X-Request-ID"] = request_id
                access_logger.info("request completed")
            else:
                access_logger.error("request failed")

            reset_request_context(tokens)


class NoIndexMiddleware:
    """
    Attach crawl-prevention headers to production responses.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not settings.DEBUG:
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive"

        return response


class TenantStatusMiddleware:
    """
    Blocks suspended tenants globally.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = getattr(request, "tenant", None)

        if tenant and hasattr(tenant, "is_active") and not tenant.is_active:
            return JsonResponse(
                {"error": "Your account has been suspended. Please contact software vendor."},
                status=403,
            )

        return self.get_response(request)


class BlockPostmanMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if the 'postman-token' header is present in the request
        if "postman-token" in request.headers:
            return JsonResponse(
                {"error": "Requests from Postman are not allowed"},
                status=403,
            )

        # Continue processing the request if not from Postman
        return self.get_response(request)
