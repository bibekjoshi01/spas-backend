from django.urls import include, path
from rest_framework.routers import DefaultRouter

from src.user.views import (
    ChangePasswordView,
    CurrentUserView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    PasswordResetVerifyView,
    PermissionListView,
    UserLoginView,
    UserLogoutView,
    UserRoleViewSet,
    UserTokenRefreshView,
    UserViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register("users", UserViewSet, basename="user")
router.register("roles", UserRoleViewSet, basename="user-role")

urlpatterns = [
    # Authentication
    path("account/login", UserLoginView.as_view(), name="user-login"),
    path("account/logout", UserLogoutView.as_view(), name="user-logout"),
    path("account/token/refresh", UserTokenRefreshView.as_view(), name="user-token-refresh"),
    path("account/change-password", ChangePasswordView.as_view(), name="user-change-password"),
    path(
        "account/password-reset/request",
        PasswordResetRequestView.as_view(),
        name="user-password-reset-request",
    ),
    path(
        "account/password-reset/verify",
        PasswordResetVerifyView.as_view(),
        name="user-password-reset-verify",
    ),
    path(
        "account/password-reset/confirm",
        PasswordResetConfirmView.as_view(),
        name="user-password-reset-confirm",
    ),
    # Current user
    path("account/me", CurrentUserView.as_view(), name="user-me"),
    # Permission catalogue
    path("permissions", PermissionListView.as_view(), name="permission-list"),
    path("", include(router.urls)),
]
