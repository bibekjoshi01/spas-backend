from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
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
        "photo": user.photo.url if user.photo else None,
        "is_superuser": user.is_superuser,
        "roles": UserRoleBriefSerializer(user.roles.all(), many=True).data,
        "permissions": get_permissions_for_user(user),
    }


# Authentication
# ------------------------------------------------------------------------------------


class UserLoginSerializer(serializers.Serializer):
    """Accepts a username or an email address in `persona`."""

    persona = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        persona = attrs.get("persona").strip().lower()
        password = attrs.get("password")

        user = self.get_user(persona)

        if not user:
            raise serializers.ValidationError({"persona": "Invalid credentials."})

        self.check_password(user, password)
        self.check_user_status(user)

        return {
            "message": "Logged in successfully.",
            "tokens": user.tokens,
            **build_user_payload(user),
        }

    def get_user(self, persona):
        lookup = {"email": persona} if "@" in persona else {"username": persona}
        return User.objects.filter(is_archived=False, **lookup).first()

    def check_password(self, user, password):
        if not user.check_password(password):
            raise serializers.ValidationError({"password": "Invalid credentials."})

    def check_user_status(self, user):
        if not user.is_active:
            raise serializers.ValidationError(
                {"persona": "This account is disabled. Contact your administrator."}
            )


class UserLogoutSerializer(serializers.Serializer):
    """Blacklists the refresh token so it cannot be exchanged again."""

    refresh = serializers.CharField(
        write_only=True,
        required=True,
        error_messages={"required": "Refresh token is required."},
    )

    def validate_refresh(self, value):
        try:
            RefreshToken(value)
        except Exception as err:
            raise serializers.ValidationError("Invalid refresh token.") from err
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
        user.save(update_fields=["password"])
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


class CurrentUserPatchSerializer(serializers.ModelSerializer):
    """What a signed-in user may change about themselves — never their roles."""

    class Meta:
        model = User
        fields = ("first_name", "middle_name", "last_name", "phone_no", "photo")

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)

        instance.full_name = " ".join(
            part for part in (instance.first_name, instance.middle_name, instance.last_name) if part
        )
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
            "full_name",
            "email",
            "phone_no",
            "photo",
            "is_active",
            "is_superuser",
            "roles",
            "date_joined",
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
            "photo",
            "is_active",
            "is_superuser",
            "roles",
            "permissions",
            "date_joined",
        )

    def get_permissions(self, obj) -> list[str]:
        return get_permissions_for_user(obj)


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    roles = serializers.PrimaryKeyRelatedField(
        queryset=UserRole.objects.filter(is_active=True, is_archived=False),
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
        user.full_name = " ".join(
            part for part in (user.first_name, user.middle_name, user.last_name) if part
        )
        user.save(update_fields=["full_name"])

        # SYSTEM-USER marks an internal account. It is attached here rather
        # than chosen, so no caller can create a login without it.
        system_role = UserRole.objects.filter(codename=SYSTEM_USER_ROLE).first()
        user.roles.set([*roles, *([system_role] if system_role else [])])

        return user

    def to_representation(self, instance):
        return {"message": "User created successfully.", "id": instance.id}


class UserPatchSerializer(serializers.ModelSerializer):
    roles = serializers.PrimaryKeyRelatedField(
        queryset=UserRole.objects.filter(is_active=True, is_archived=False),
        many=True,
        required=False,
    )

    class Meta:
        model = User
        fields = (
            "first_name",
            "middle_name",
            "last_name",
            "email",
            "phone_no",
            "photo",
            "is_active",
            "roles",
        )

    def validate_email(self, value):
        duplicate = (
            User.objects.filter(email__iexact=value, is_archived=False)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if duplicate:
            raise serializers.ValidationError("A user with that email already exists.")
        return value

    @transaction.atomic
    def update(self, instance, validated_data):
        roles = validated_data.pop("roles", None)

        if instance.is_superuser and roles is not None:
            raise serializers.ValidationError({"roles": "A superuser's roles cannot be changed."})

        for field, value in validated_data.items():
            setattr(instance, field, value)

        instance.full_name = " ".join(
            part for part in (instance.first_name, instance.middle_name, instance.last_name) if part
        )
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
