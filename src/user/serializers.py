from django.contrib.auth.password_validation import validate_password
from django.db import connection, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

# Project Imports
from src.libs.get_context import get_user_by_context
from src.libs.permissions import get_permissions_for_user
from src.user.constants import SYSTEM_USER_ROLE
from src.user.models import Permission, PermissionCategory, User, UserRole


class UserRoleBriefSerializer(serializers.ModelSerializer):
    """Role as it appears inside a user payload."""

    class Meta:
        model = UserRole
        fields = ("id", "name", "codename")


def build_user_payload(user: User) -> dict:
    """
    The single shape the frontend reads a signed-in user from.

    Login and `account/me` both return this, so the client has one parser and
    one source of truth for what the user may do.
    """
    return {
        "id": user.id,
        "uuid": str(user.uuid),
        "username": user.username,
        "full_name": user.full_name or user.username,
        "first_name": user.first_name,
        "middle_name": user.middle_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone_no": user.phone_no,
        "alternate_phone_no": user.alternate_phone_no,
        "photo": user.photo.url if user.photo else None,
        "is_superuser": user.is_superuser,
        "must_change_password": user.must_change_password,
        "roles": UserRoleBriefSerializer(
            user.roles.filter(is_active=True, is_archived=False), many=True
        ).data,
        "permissions": get_permissions_for_user(user),
    }


# Authentication
# ------------------------------------------------------------------------------------


class UserLoginSerializer(serializers.Serializer):
    """Accepts a username or an email address in `persona`."""

    persona = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        persona = attrs.get("persona").strip()
        password = attrs.get("password")

        user = self.get_user(persona)

        if not user:
            # Match the password-hashing work of a failed existing-account login.
            User().set_password(password)
            raise serializers.ValidationError({"persona": "Invalid credentials."})

        # Serialize a first sign-in against account editing and deactivation.
        # This makes last_login a reliable lifecycle boundary rather than a
        # best-effort timestamp that can race an administrator's PATCH.
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user.pk)
            self.check_password(user, password)
            self.check_user_status(user)

            tokens = user.tokens
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])

            return {
                "message": "Logged in successfully.",
                "tokens": tokens,
                **build_user_payload(user),
            }

    def get_user(self, persona):
        return User.objects.find_by_persona(persona)

    def check_password(self, user, password):
        if not user.check_password(password):
            raise serializers.ValidationError({"persona": "Invalid credentials."})

    def check_user_status(self, user):
        if not user.is_active:
            raise serializers.ValidationError(
                {"persona": "This account is disabled. Contact your administrator."}
            )
        if user.roles.filter(codename="STUDENT").exists():
            from src.students.permissions import student_portal_access_error

            error = student_portal_access_error(user, allow_initial_password_change=True)
            if error:
                raise serializers.ValidationError({"persona": error})


class UserTokenRefreshSerializer(TokenRefreshSerializer):
    """Re-evaluate live student policy before extending a student session."""

    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])
        if refresh.get("tenant_schema") != connection.schema_name:
            raise AuthenticationFailed("This session belongs to a different college.")
        user = User.objects.filter(pk=refresh["user_id"], is_archived=False).first()
        if user is None or not user.is_active:
            raise AuthenticationFailed("This account is no longer active.")
        if user.roles.filter(codename="STUDENT").exists():
            from src.students.permissions import student_portal_access_error

            error = student_portal_access_error(user, allow_initial_password_change=True)
            if error:
                raise AuthenticationFailed(error)
        return super().validate(attrs)


class UserLogoutSerializer(serializers.Serializer):
    """Blacklists the refresh token so it cannot be exchanged again."""

    refresh = serializers.CharField(
        write_only=True,
        required=True,
        error_messages={"required": "Refresh token is required."},
    )

    def validate_refresh(self, value):
        try:
            token = RefreshToken(value)
        except Exception as err:
            raise serializers.ValidationError("Invalid refresh token.") from err
        if token.get("tenant_schema") != connection.schema_name or str(token.get("user_id")) != str(
            self.context["request"].user.pk
        ):
            raise serializers.ValidationError("Refresh token does not belong to this account.")
        return value

    def create(self, validated_data):
        RefreshToken(validated_data["refresh"]).blacklist()
        return validated_data


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True)

    def validate_current_password(self, value):
        user = get_user_by_context(self.context)
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        validate_password(value, user=get_user_by_context(self.context))
        return value

    def validate(self, attrs):
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": "The new password must differ from the current one."}
            )
        return attrs

    def create(self, validated_data):
        user = get_user_by_context(self.context)
        user.set_password(validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        return validated_data


class PasswordResetRequestSerializer(serializers.Serializer):
    persona = serializers.CharField(required=True, max_length=254)


class PasswordResetVerifySerializer(serializers.Serializer):
    persona = serializers.CharField(required=True, max_length=254)
    code = serializers.RegexField(
        regex=r"^\d{6}$",
        required=True,
        error_messages={"invalid": "Enter the six-digit verification code."},
    )


class PasswordResetConfirmSerializer(serializers.Serializer):
    reset_token = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)


# Current user
# ------------------------------------------------------------------------------------


class UserNameValidationMixin:
    def validate(self, attrs):
        candidate = User(
            **{
                name: attrs.get(name, getattr(self.instance, name, ""))
                for name in ("first_name", "middle_name", "last_name")
            }
        )
        candidate.compose_full_name()
        return super().validate(attrs)


class CurrentUserPatchSerializer(UserNameValidationMixin, serializers.ModelSerializer):
    """What a signed-in user may change about themselves — never their roles."""

    class Meta:
        model = User
        fields = (
            "first_name",
            "middle_name",
            "last_name",
            "phone_no",
            "alternate_phone_no",
            "photo",
        )

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)

        instance.full_name = instance.compose_full_name()
        instance.save()
        return instance

    def to_representation(self, instance):
        return {"message": "Profile updated successfully.", "id": instance.id}


# User management
# ------------------------------------------------------------------------------------


class UserListSerializer(serializers.ModelSerializer):
    roles = UserRoleBriefSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "uuid",
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "full_name",
            "email",
            "phone_no",
            "alternate_phone_no",
            "photo",
            "is_active",
            "is_superuser",
            "roles",
            "date_joined",
            "last_login",
        )


class UserRetrieveSerializer(serializers.ModelSerializer):
    roles = UserRoleBriefSerializer(many=True, read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "uuid",
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "full_name",
            "email",
            "phone_no",
            "alternate_phone_no",
            "photo",
            "is_active",
            "is_superuser",
            "roles",
            "permissions",
            "date_joined",
            "last_login",
        )

    def get_permissions(self, obj) -> list[str]:
        return get_permissions_for_user(obj)


class UserCreateSerializer(UserNameValidationMixin, serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    roles = serializers.PrimaryKeyRelatedField(
        queryset=UserRole.objects.filter(
            is_active=True,
            is_archived=False,
            is_system_managed=False,
        ),
        many=True,
        required=False,
    )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "password",
            "first_name",
            "middle_name",
            "last_name",
            "phone_no",
            "alternate_phone_no",
            "roles",
        )

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value, is_archived=False).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        roles = validated_data.pop("roles", [])
        password = validated_data.pop("password")

        user = User.objects.create_user(
            **validated_data,
            password=password,
            created_by=get_user_by_context(self.context),
        )
        user.full_name = user.compose_full_name()
        user.save(update_fields=["full_name"])

        # SYSTEM-USER marks an internal account. It is attached here rather
        # than chosen, so no caller can create a login without it.
        system_role = UserRole.objects.filter(codename=SYSTEM_USER_ROLE).first()
        user.roles.set([*roles, *([system_role] if system_role else [])])

        return user

    def to_representation(self, instance):
        return {"message": "User created successfully.", "id": instance.id}


class UserPatchSerializer(UserNameValidationMixin, serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    roles = serializers.PrimaryKeyRelatedField(
        queryset=UserRole.objects.filter(
            is_active=True,
            is_archived=False,
            is_system_managed=False,
        ),
        many=True,
        required=False,
    )

    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "email",
            "phone_no",
            "alternate_phone_no",
            "photo",
            "password",
            "is_active",
            "roles",
        )

    def validate_password(self, value):
        validate_password(value, user=self.instance)
        return value

    def validate_email(self, value):
        duplicate = (
            User.objects.filter(email__iexact=value, is_archived=False)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if duplicate:
            raise serializers.ValidationError("A user with that email already exists.")
        return value

    def validate(self, attrs):
        self._validate_account_change(self.instance, attrs)
        return super().validate(attrs)

    def _validate_account_change(self, instance, attrs):
        if instance.is_superuser:
            if "roles" in attrs:
                raise serializers.ValidationError(
                    {"roles": "A superuser's roles cannot be changed."}
                )
            if "is_active" in attrs and attrs["is_active"] != instance.is_active:
                raise serializers.ValidationError(
                    {"is_active": "A superuser's status cannot be changed here."}
                )

        request = self.context.get("request")
        if (
            request
            and request.user.pk == instance.pk
            and "is_active" in attrs
            and attrs["is_active"] != instance.is_active
        ):
            raise serializers.ValidationError(
                {"is_active": "You cannot change your own account status."}
            )

        # Roles describe current responsibility and remain administratively
        # editable throughout the account lifecycle. Identity and credential
        # fields become immutable after the first successful sign-in.
        locked_fields = set(attrs) - {"is_active", "roles"}
        if instance.last_login is not None and locked_fields:
            message = "This field cannot be changed after the account's first sign-in."
            raise serializers.ValidationError(dict.fromkeys(locked_fields, message))

    @transaction.atomic
    def update(self, instance, validated_data):
        # Lock and re-check so a simultaneous first login cannot slip between
        # validation and persistence.
        instance = User.objects.select_for_update().get(pk=instance.pk)
        self._validate_account_change(instance, validated_data)

        roles = validated_data.pop("roles", None)
        password = validated_data.pop("password", None)

        for field, value in validated_data.items():
            setattr(instance, field, value)

        instance.full_name = instance.compose_full_name()
        if password is not None:
            instance.set_password(password)
        instance.save()

        if roles is not None:
            # Internal roles are not on offer in the picker, so a save that
            # omits them must not remove them.
            internal = instance.roles.filter(is_system_managed=True)
            authority_codenames = []
            if instance.headed_departments.filter(is_archived=False).exists():
                authority_codenames.append("DEPARTMENT-HEAD")
            if instance.coordinated_programs.filter(is_archived=False).exists():
                authority_codenames.append("PROGRAM-COORDINATOR")
            authority_roles = UserRole.objects.filter(codename__in=authority_codenames)
            instance.roles.set([*roles, *internal, *authority_roles])

        return instance

    def to_representation(self, instance):
        return {"message": "User updated successfully.", "id": instance.id}


# Roles and permissions
# ------------------------------------------------------------------------------------


class PermissionBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ("id", "name", "codename")


class UserRoleListSerializer(serializers.ModelSerializer):
    permissions = PermissionBriefSerializer(many=True, read_only=True)

    class Meta:
        model = UserRole
        fields = ("id", "name", "codename", "is_system_managed", "is_active", "permissions")


class PermissionCategorySerializer(serializers.ModelSerializer):
    """Permissions grouped the way a role-editing screen renders them."""

    permissions = serializers.SerializerMethodField()

    class Meta:
        model = PermissionCategory
        fields = ("id", "name", "codename", "permissions")

    def get_permissions(self, obj) -> list[dict]:
        active = [item for item in obj.permissions.all() if item.is_active]
        return list(PermissionBriefSerializer(active, many=True).data)
