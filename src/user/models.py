from typing import ClassVar, cast
from uuid import uuid4

# Django Imports
from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Rest Framework
from rest_framework_simplejwt.tokens import RefreshToken
from simple_history.models import HistoricalRecords

# Project Imports
from src.base.models import AuditInfoModel
from src.user.exceptions import (
    EmailNotSetError,
    IsStaffError,
    IsSuperuserError,
    RoleNotFound,
)

from .constants import SYSTEM_USER_ROLE
from .validators import CustomUsernameValidator, validate_user_image


class MainModule(models.Model):
    """Main Module to group permission categories"""

    name = models.CharField(_("name"), max_length=50)
    codename = models.CharField(_("codename"), max_length=50, unique=True)
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    def __str__(self):
        return f"id - {self.id} : {self.name}"


class PermissionCategory(models.Model):
    """Permission Category to group permissions"""

    name = models.CharField(max_length=50)
    codename = models.CharField(_("codename"), unique=True, max_length=100)

    main_module = models.ForeignKey(
        MainModule,
        on_delete=models.PROTECT,
        related_name="permission_categories",
    )
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    def __str__(self):
        return f"id - {self.id} : {self.name} : {self.main_module.name}"


class Permission(models.Model):
    """
    The permissions system provides a way to assign permissions to specific
    users and groups of users.
    """

    name = models.CharField(_("name"), max_length=100)
    codename = models.CharField(_("codename"), unique=True, max_length=100)
    permission_category = models.ForeignKey(
        PermissionCategory, on_delete=models.PROTECT, related_name="permissions"
    )
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("permission")
        verbose_name_plural = _("permissions")
        ordering = (
            "permission_category__main_module",
            "permission_category",
            "id",
        )

    def __str__(self):
        return f"{self.permission_category} : {self.name}"


class UserRole(AuditInfoModel):
    """
    User Roles are a generic way of categorizing users to apply permissions, or
    some other label, to those users. A user can belong to any number of
    groups.
    """

    name = models.CharField(_("name"), max_length=50, unique=True)
    codename = models.CharField(_("codename"), max_length=50, unique=True)
    is_system_managed = models.BooleanField(
        _("System Managed"),
        default=False,
        help_text=_("Managed by system"),
    )
    permissions = models.ManyToManyField(Permission, verbose_name=_("permissions"), blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("role")
        verbose_name_plural = _("roles")
        ordering = ("name",)

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    use_in_migrations = False

    def _create_user(
        self, username: str, email: str | None, password: str | None, **extra_fields
    ) -> "User":
        """
        Create and save a user with the given username, email, and password.
        """
        if not username:
            error_message = _("The given username must be set")
            raise ValueError(error_message)

        if not email:
            raise EmailNotSetError

        email = self.normalize_email(email)

        user = cast("User", self.model(username=username, email=email, **extra_fields))
        user.password = make_password(password)
        user.save(using=self._db)

        return user

    def create_user(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields,
    ) -> "User":
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra_fields)

    def create_system_user(
        self, username: str, email: str, password: str, **extra_fields
    ) -> "User":
        user = self.create_user(username, email, password, **extra_fields)
        try:
            role = UserRole.objects.get(codename=SYSTEM_USER_ROLE)
            user.roles.add(role)
        except UserRole.DoesNotExist:
            raise RoleNotFound("System User")
        return user

    def create_superuser(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields,
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise IsStaffError
        if extra_fields.get("is_superuser") is not True:
            raise IsSuperuserError

        user = self._create_user(username, email, password, **extra_fields)

        # Assign system roles automatically (if exist)
        roles = UserRole.objects.filter(codename=SYSTEM_USER_ROLE)
        for role in roles:
            user.roles.add(role)

        return user


class User(AbstractBaseUser, PermissionsMixin):
    """
    User Model

    This model represents a user in the system,
    extending the base user functionality provided by Django's AbstractBaseUser.
    """

    uuid = models.UUIDField(default=uuid4, unique=True, editable=False)

    username_validator = CustomUsernameValidator()

    username = models.CharField(
        _("username"),
        max_length=30,
        unique=True,
        help_text=_(
            "Required. 30 characters or fewer. Letters, digits and @/./+/-/_ only.",
        ),
        validators=[username_validator],
        error_messages={
            "unique": _("A user with that username already exists."),
        },
    )
    first_name = models.CharField(_("first name"), max_length=100, blank=True)
    middle_name = models.CharField(_("middle Name"), max_length=100, blank=True)
    last_name = models.CharField(_("last name"), max_length=100, blank=True)
    full_name = models.CharField(_("full name"), max_length=100, blank=True)
    email = models.EmailField(_("email address"), blank=True)
    phone_no = models.CharField(_("phone number"), max_length=15, blank=True)
    photo = models.ImageField(
        validators=[validate_user_image],
        blank=True,
        null=True,
        default="",
    )

    # System Signature Fields
    is_superuser = models.BooleanField(
        _("superuser status"),
        default=False,
        help_text=_(
            "Designates that this user has all permissions without explicitly assigning them.",
        ),
    )
    is_staff = models.BooleanField(
        _("staff status"),
        default=False,
        help_text=_("Designates whether the user can log into this admin site."),
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_(
            "Designates whether this user should be treated as active. "
            "Unselect this instead of deleting accounts.",
        ),
    )
    is_archived = models.BooleanField(
        _("archived"),
        default=False,
        help_text=_(
            "Designates whether this user should be treated as delected. "
            "Unselect this instead of deleting users.",
        ),
    )
    roles = models.ManyToManyField(
        UserRole,
        related_name="users",
        blank=True,
    )
    date_joined = models.DateTimeField(
        _("date joined"),
        default=timezone.now,
        editable=False,
    )
    updated_at = models.DateTimeField(_("date updated"), auto_now=True)
    archived_at = models.DateTimeField(_("date archived"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
    )

    objects = UserManager()

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["email"]

    history = HistoricalRecords()

    class Meta:
        ordering = ("-id",)
        verbose_name = _("user")
        verbose_name_plural = _("users")
        constraints = (
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(is_archived=False),
                name="unique_email_active_user",
            ),
        )

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)

    def __str__(self):
        return str(self.email)

    def get_upload_path(self, filename: str) -> str:
        return f"{self.id}/{filename}"

    def is_system_user(self) -> bool:
        return self.roles.filter(codename=SYSTEM_USER_ROLE).exists()

    @property
    def tokens(self) -> dict[str, str]:
        refresh = RefreshToken.for_user(self)
        return {"refresh": str(refresh), "access": str(refresh.access_token)}


class UserForgetPasswordRequest(models.Model):
    """A short-lived, single-use password recovery challenge."""

    uuid = models.UUIDField(default=uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_requests")
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ("-id",)
        indexes = (
            models.Index(fields=("user", "created_at"), name="user_pwdreset_user_created_idx"),
            models.Index(fields=("expires_at",), name="user_pwdreset_expires_idx"),
        )

    def __str__(self) -> str:
        return f"Password reset request for user {self.user_id}"


class UserAccountVerification(models.Model):
    """User Account Verification"""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    otp = models.CharField(max_length=6, blank=True)
    token = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField()
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ("-id",)

    def __str__(self) -> str:
        return f"User Id: {self.user.id!s} + '-' + {self.otp}"
