import logging

from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.views import TokenRefreshView

# Project Imports
from src.base.schemas import MessageResponseSerializer
from src.libs.get_context import get_user_by_request
from src.user.models import PermissionCategory, User, UserRole
from src.user.password_reset import (
    GENERIC_REQUEST_MESSAGE,
    INVALID_CODE_MESSAGE,
    create_password_reset_request,
    find_recoverable_user,
    reset_password_with_token,
    send_password_reset_code,
    verify_password_reset_code,
)
from src.user.permissions import UserPermission, UserRolePermission

from .serializers import (
    ChangePasswordSerializer,
    CurrentUserPatchSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifySerializer,
    PermissionCategorySerializer,
    UserCreateSerializer,
    UserListSerializer,
    UserLoginSerializer,
    UserLogoutSerializer,
    UserPatchSerializer,
    UserRetrieveSerializer,
    UserRoleListSerializer,
    build_user_payload,
)
from .throttling import ForgetPasswordThrottle, LoginThrottle, PasswordResetAttemptThrottle

logger = logging.getLogger(__name__)

# Authentication
# ------------------------------------------------------------------------------------


class UserTokenRefreshView(TokenRefreshView):
    authentication_classes: tuple[type, ...] = (JWTAuthentication,)  # type: ignore[assignment]
    permission_classes: tuple[type, ...] = (AllowAny,)  # type: ignore[assignment]


class UserLoginView(APIView):
    """Exchange credentials for tokens, the user profile, and their permissions."""

    permission_classes: tuple[type, ...] = (AllowAny,)
    serializer_class = UserLoginSerializer
    throttle_classes: tuple[type, ...] = (LoginThrottle,)

    @extend_schema(request=UserLoginSerializer, responses=UserLoginSerializer)
    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class UserLogoutView(APIView):
    """Blacklist the supplied refresh token."""

    permission_classes: tuple[type, ...] = (IsAuthenticated,)
    serializer_class = UserLogoutSerializer

    @extend_schema(request=UserLogoutSerializer, responses=MessageResponseSerializer)
    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Logged out successfully."}, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    permission_classes: tuple[type, ...] = (IsAuthenticated,)
    serializer_class = ChangePasswordSerializer

    @extend_schema(request=ChangePasswordSerializer, responses=MessageResponseSerializer)
    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Password changed successfully."})


class PasswordResetRequestView(APIView):
    """Send a recovery OTP without revealing whether the account exists."""

    permission_classes: tuple[type, ...] = (AllowAny,)
    serializer_class = PasswordResetRequestSerializer
    throttle_classes: tuple[type, ...] = (ForgetPasswordThrottle,)

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = find_recoverable_user(serializer.validated_data["persona"])

        if user is not None:
            reset_request, code = create_password_reset_request(
                user,
                request.META.get("REMOTE_ADDR"),
            )
            try:
                send_password_reset_code(user, code)
            except Exception:
                reset_request.is_archived = True
                reset_request.save(update_fields=("is_archived",))
                logger.exception("Unable to send password reset OTP", extra={"user_id": user.pk})

        return Response({"message": GENERIC_REQUEST_MESSAGE})


class PasswordResetVerifyView(APIView):
    """Verify the OTP and exchange it for a short-lived signed reset token."""

    permission_classes: tuple[type, ...] = (AllowAny,)
    serializer_class = PasswordResetVerifySerializer
    throttle_classes: tuple[type, ...] = (PasswordResetAttemptThrottle,)

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        reset_token = verify_password_reset_code(**serializer.validated_data)
        if reset_token is None:
            return Response(
                {"code": INVALID_CODE_MESSAGE, "success": False},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"message": "Code verified.", "reset_token": reset_token})


class PasswordResetConfirmView(APIView):
    """Set a new password using a verified, single-use reset session."""

    permission_classes: tuple[type, ...] = (AllowAny,)
    serializer_class = PasswordResetConfirmSerializer
    throttle_classes: tuple[type, ...] = (PasswordResetAttemptThrottle,)

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        reset_password_with_token(**serializer.validated_data)
        return Response({"message": "Password reset successfully. You can now sign in."})


# Current user
# ------------------------------------------------------------------------------------


class CurrentUserView(generics.GenericAPIView):
    """
    The signed-in user, their roles and every permission codename they hold.

    The frontend calls this once after login (and on reload) to decide which
    menus and actions to render.
    """

    permission_classes: tuple[type, ...] = (IsAuthenticated,)
    serializer_class = CurrentUserPatchSerializer
    http_method_names = ("get", "patch", "options")

    def get_object(self):
        return get_user_by_request(self.request)

    @extend_schema(responses=UserRetrieveSerializer)
    def get(self, request):
        return Response(build_user_payload(self.get_object()))

    @transaction.atomic
    def patch(self, request):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# User management
# ------------------------------------------------------------------------------------


class UserViewSet(ModelViewSet):
    """CRUD for the people who can sign in to this college's workspace."""

    permission_classes = (UserPermission,)
    queryset = (
        User.objects.filter(is_archived=False)
        .exclude(roles__codename="STUDENT")
        .prefetch_related("roles")
        .distinct()
    )
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("is_active", "roles")
    search_fields = ("username", "email", "full_name", "phone_no")
    ordering = ("-id",)
    ordering_fields = ("id", "username", "date_joined")
    http_method_names = ("get", "head", "post", "patch", "options", "delete")

    def get_queryset(self):
        queryset = super().get_queryset()
        role = self.request.query_params.get("role")
        if role:
            queryset = queryset.filter(roles__codename=role.upper())
        return queryset.distinct()

    def get_serializer_class(self):
        if self.request.method == "POST":
            return UserCreateSerializer
        if self.request.method == "PATCH":
            return UserPatchSerializer
        if self.action == "list":
            return UserListSerializer
        return UserRetrieveSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(request=None, responses=MessageResponseSerializer)
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        user = self.get_object()

        if user == request.user:
            return Response(
                {"detail": "You cannot archive your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.is_archived = True
        user.is_active = False
        user.archived_at = timezone.now()
        user.save(update_fields=["is_archived", "is_active", "archived_at", "updated_at"])

        return Response({"message": "User archived successfully."})


class UserRoleViewSet(ReadOnlyModelViewSet):
    """
    Roles and the permissions attached to them.

    Read-only: the set of roles is fixed and seeded from fixtures. Pass
    ?assignable=true to exclude the internal roles (SYSTEM-USER, STUDENT) that
    the system attaches rather than a person choosing.
    """

    permission_classes = (UserRolePermission,)
    queryset = (
        UserRole.objects.filter(is_archived=False).prefetch_related("permissions").order_by("name")
    )
    serializer_class = UserRoleListSerializer
    filter_backends = (SearchFilter, OrderingFilter)
    search_fields = ("name", "codename")
    ordering = ("name",)
    ordering_fields = ("id", "name")
    http_method_names = ("get", "head", "options")

    def get_queryset(self):
        queryset = super().get_queryset()

        # ?assignable=true is what a role picker asks for: the roles a human
        # chooses, without the internal ones the system attaches itself.
        if self.request.query_params.get("assignable", "").lower() == "true":
            queryset = queryset.filter(is_system_managed=False)

        return queryset


class PermissionListView(generics.ListAPIView):
    """Every permission, grouped by category, for building a role screen."""

    permission_classes = (UserRolePermission,)
    serializer_class = PermissionCategorySerializer
    pagination_class = None

    def get_queryset(self):
        return (
            PermissionCategory.objects.filter(is_active=True)
            .prefetch_related("permissions")
            .select_related("main_module")
            .order_by("main_module__name", "name")
        )
