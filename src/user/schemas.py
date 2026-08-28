from rest_framework import serializers


class TokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()
    access = serializers.CharField()


class UserLoginResponseSerializer(serializers.Serializer):
    message = serializers.CharField(default="You have successfully logged in.")
    status = serializers.CharField(default="success")
    full_name = serializers.CharField()
    id = serializers.IntegerField(default=3)
    is_superuser = serializers.BooleanField(default=True)
    is_email_verified = serializers.BooleanField(default=False)
    is_phone_verified = serializers.BooleanField(default=False)
    phone_no = serializers.CharField(allow_blank=True, required=False, default="")
    alternate_phone_no = serializers.CharField(allow_blank=True, required=False, default="")
    photo = serializers.URLField(allow_null=True, required=False, default=None)
    email = serializers.EmailField(default="default@gmail.com")
    tokens = TokenSerializer()
    roles = serializers.ListField(child=serializers.CharField(), default=[])
    permissions = serializers.ListField(child=serializers.CharField(), default=[])
