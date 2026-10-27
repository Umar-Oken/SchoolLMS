from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from accounts.models import User


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    new_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Joriy parol noto‘g‘ri.")
        return value

    def validate_new_password(self, value):
        user = self.context["request"].user
        if user.check_password(value):
            raise serializers.ValidationError(
                "Yangi parol joriy paroldan farq qilishi kerak."
            )
        try:
            validate_password(value, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value


class LoginInputSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=False)
    password = serializers.CharField(
        max_length=128,
        trim_whitespace=False,
        write_only=True,
        style={"input_type": "password"},
    )


class CsrfTokenSerializer(serializers.Serializer):
    csrfToken = serializers.CharField(read_only=True)


class LoginResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


class LoginErrorSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


class CurrentUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "role",
            "preferred_language",
        )
        read_only_fields = ("id", "username", "role")

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            writable_fields = {
                name
                for name, field in self.fields.items()
                if not field.read_only
            }
            forbidden_fields = set(data.keys()) - writable_fields
            if forbidden_fields:
                raise serializers.ValidationError({
                    name: "Bu maydonni o‘zgartirib bo‘lmaydi."
                    for name in forbidden_fields
                })
        return super().to_internal_value(data)


class AccountCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "role",
            "preferred_language",
        )
        extra_kwargs = {
            "username": {"required": True},
            "role": {"required": True},
            "first_name": {"required": False, "allow_blank": True},
            "last_name": {"required": False, "allow_blank": True},
            "preferred_language": {"required": False, "allow_blank": True},
        }

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            extra_fields = set(data.keys()) - set(self.fields)
            if extra_fields:
                raise serializers.ValidationError({
                    name: "Bu maydon qabul qilinmaydi."
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate_role(self, value):
        if value not in User.Role.values:
            raise serializers.ValidationError("Role faqat 'pupil' yoki 'teacher' bo‘lishi kerak.")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(
            password=password,
            must_change_password=True,
            **validated_data,
        )


class AccountResetPasswordSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            extra_fields = set(data.keys())
            if extra_fields:
                raise serializers.ValidationError({
                    name: "Reset so‘rovi bu maydonni qabul qilmaydi."
                    for name in extra_fields
                })
        elif data:
            raise serializers.ValidationError(
                {"detail": "Reset so‘rovi bo‘sh bo‘lishi kerak."}
            )
        return super().to_internal_value(data)


class AccountCreatedResponseSerializer(serializers.ModelSerializer):
    temporary_password = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "role",
            "preferred_language",
            "temporary_password",
        )
