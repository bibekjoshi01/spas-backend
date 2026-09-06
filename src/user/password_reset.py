import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import connection, transaction
from django.utils import timezone
from rest_framework import serializers

from src.user.models import User, UserForgetPasswordRequest

logger = logging.getLogger(__name__)

OTP_TTL = timedelta(minutes=10)
RESET_TOKEN_MAX_AGE_SECONDS = 10 * 60
MAX_FAILED_ATTEMPTS = 5
RESET_TOKEN_SALT = "spas.password-reset.v1"
GENERIC_REQUEST_MESSAGE = (
    "If an active account matches those details, a verification code has been sent."
)
INVALID_CODE_MESSAGE = "The verification code is invalid or has expired. Request a new code."


def find_recoverable_user(persona: str) -> User | None:
    return User.objects.find_by_persona(persona, active_only=True)


@transaction.atomic
def create_password_reset_request(
    user: User, requested_ip: str | None
) -> tuple[UserForgetPasswordRequest, str]:
    UserForgetPasswordRequest.objects.filter(
        user=user,
        is_archived=False,
        consumed_at__isnull=True,
    ).update(is_archived=True)

    code = f"{secrets.randbelow(1_000_000):06d}"
    request = UserForgetPasswordRequest.objects.create(
        user=user,
        code_hash=make_password(code),
        expires_at=timezone.now() + OTP_TTL,
        requested_ip=requested_ip,
    )
    return request, code


def send_password_reset_code(user: User, code: str) -> None:
    send_mail(
        subject="Your SPAS password reset code",
        message=(
            f"Hello {user.full_name or user.username},\n\n"
            f"Your SPAS password reset code is: {code}\n\n"
            "This code expires in 10 minutes. If you did not request a password reset, "
            "you can ignore this email. Never share this code with anyone."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def verify_password_reset_code(persona: str, code: str) -> str | None:
    user = find_recoverable_user(persona)
    if user is None:
        return None

    invalid_code = False
    with transaction.atomic():
        reset_request = (
            UserForgetPasswordRequest.objects.select_for_update()
            .filter(user=user, is_archived=False, consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        now = timezone.now()
        if (
            reset_request is None
            or reset_request.expires_at <= now
            or reset_request.failed_attempts >= MAX_FAILED_ATTEMPTS
        ):
            invalid_code = True
        elif not check_password(code, reset_request.code_hash):
            reset_request.failed_attempts += 1
            if reset_request.failed_attempts >= MAX_FAILED_ATTEMPTS:
                reset_request.is_archived = True
            reset_request.save(update_fields=("failed_attempts", "is_archived"))
            invalid_code = True
        else:
            reset_request.verified_at = now
            reset_request.save(update_fields=("verified_at",))
            reset_token = signing.dumps(
                {
                    "request_id": reset_request.pk,
                    "tenant_schema": getattr(connection, "schema_name", "public"),
                },
                salt=RESET_TOKEN_SALT,
            )

    if invalid_code:
        return None
    return reset_token


@transaction.atomic
def reset_password_with_token(reset_token: str, new_password: str) -> None:
    try:
        payload = signing.loads(
            reset_token,
            salt=RESET_TOKEN_SALT,
            max_age=RESET_TOKEN_MAX_AGE_SECONDS,
        )
        if payload.get("tenant_schema") != getattr(connection, "schema_name", "public"):
            raise signing.BadSignature("Reset session belongs to another college.")
        request_id = payload["request_id"]
    except (signing.BadSignature, signing.SignatureExpired, KeyError, TypeError) as error:
        raise serializers.ValidationError(
            {"reset_token": "This reset session is invalid or expired."}
        ) from error

    reset_request = (
        UserForgetPasswordRequest.objects.select_for_update()
        .select_related("user")
        .filter(
            pk=request_id, is_archived=False, consumed_at__isnull=True, verified_at__isnull=False
        )
        .first()
    )
    if reset_request is None or reset_request.expires_at <= timezone.now():
        raise serializers.ValidationError(
            {"reset_token": "This reset session is invalid or expired."}
        )

    from django.contrib.auth.password_validation import validate_password

    try:
        validate_password(new_password, user=reset_request.user)
    except DjangoValidationError as error:
        raise serializers.ValidationError({"new_password": list(error.messages)}) from error

    reset_request.user.set_password(new_password)
    reset_request.user.must_change_password = False
    reset_request.user.save(update_fields=("password", "must_change_password"))
    reset_request.consumed_at = timezone.now()
    reset_request.is_archived = True
    reset_request.save(update_fields=("consumed_at", "is_archived"))

    UserForgetPasswordRequest.objects.filter(
        user=reset_request.user,
        consumed_at__isnull=True,
    ).exclude(pk=reset_request.pk).update(is_archived=True)
