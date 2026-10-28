from django.contrib.auth import (
    authenticate,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils.crypto import get_random_string
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import GenericAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import AccountLifecycleAudit, User
from accounts.permissions import CanManageAccounts, MustChangePasswordPermission
from accounts.serializers import (
    AccountCreateSerializer,
    AccountCreatedResponseSerializer,
    AccountResetPasswordSerializer,
    ChangePasswordSerializer,
    CsrfTokenSerializer,
    CurrentUserSerializer,
    LoginErrorSerializer,
    LoginInputSerializer,
    LoginResponseSerializer,
)
from root.openapi import CSRF_HEADER, common_error_responses, error_response


def generate_temporary_password(user):
    for _ in range(10):
        password = get_random_string(40)
        try:
            validate_password(password, user=user)
        except DjangoValidationError:
            continue
        return password
    raise RuntimeError("Validated temporary password generation failed.")


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfCookieView(GenericAPIView):
    serializer_class = CsrfTokenSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(
        summary='CSRF token olish',
        description=(
            'Session orqali himoyalangan POST/PATCH so‘rovlaridan oldin '
            'cookie va unga mos CSRF tokenni beradi.'
        ),
        tags=['Auth'],
        request=None,
        responses={status.HTTP_200_OK: CsrfTokenSerializer},
        examples=[
            OpenApiExample(
                'CSRF token javobi',
                value={'csrfToken': 'csrf-token-namuna'},
                response_only=True,
            )
        ],
    )
    def get(self, request):
        return Response({"csrfToken": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(GenericAPIView):
    serializer_class = LoginInputSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(
        summary='Tizimga kirish',
        description=(
            'Username va parol bilan session yaratadi. Avval CSRF tokenni '
            'oling, so‘ng CSRF header bilan yuboring.'
        ),
        tags=['Auth'],
        parameters=[CSRF_HEADER],
        request=LoginInputSerializer,
        responses={
            status.HTTP_200_OK: LoginResponseSerializer,
            status.HTTP_400_BAD_REQUEST: LoginErrorSerializer,
            status.HTTP_403_FORBIDDEN: error_response(
                'CSRF cookie/header yo‘q yoki mos emas.',
                {'detail': 'CSRF token missing or incorrect.'},
            ),
        },
        examples=[
            OpenApiExample(
                'Login so‘rovi',
                value={'username': 'teacher01', 'password': 'namuna-parol'},
                request_only=True,
            ),
            OpenApiExample(
                'Login javobi',
                value={'detail': 'Muvaffaqiyatli tizimga kirildi.'},
                response_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        django_request = request._request
        user = authenticate(
            request=django_request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Login yoki parol noto‘g‘ri."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        login(django_request, user)
        return Response({"detail": "Muvaffaqiyatli tizimga kirildi."})


@extend_schema_view(
    get=extend_schema(
        summary='Joriy profilni ko‘rish',
        description='Session orqali kirgan foydalanuvchining profilini qaytaradi.',
        tags=['Profil'],
        responses={
            status.HTTP_200_OK: CurrentUserSerializer,
            **common_error_responses(),
        },
    ),
    patch=extend_schema(
        summary='Joriy profilni yangilash',
        description='Faqat ism, familiya va afzal tilni o‘zgartiradi.',
        tags=['Profil'],
        parameters=[CSRF_HEADER],
        request=CurrentUserSerializer,
        responses={
            status.HTTP_200_OK: CurrentUserSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Profilni yangilash',
                value={'first_name': 'Ali', 'preferred_language': 'uz'},
                request_only=True,
            )
        ],
    ),
)
class CurrentUserView(RetrieveUpdateAPIView):
    serializer_class = CurrentUserSerializer
    permission_classes = (IsAuthenticated,)
    authentication_classes = (SessionAuthentication,)
    http_method_names = ("get", "patch", "head", "options")

    def get_object(self):
        return self.request.user


class ChangePasswordView(GenericAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = (IsAuthenticated,)
    authentication_classes = (SessionAuthentication,)

    @extend_schema(
        summary='Parolni almashtirish',
        description=(
            'Joriy parolni tekshiradi, yangi parolni validatsiya qiladi va '
            'joriy sessionni saqlab qoladi.'
        ),
        tags=['Profil'],
        parameters=[CSRF_HEADER],
        request=ChangePasswordSerializer,
        responses={
            status.HTTP_200_OK: LoginResponseSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Parol almashtirish',
                value={
                    'current_password': 'eski-parol',
                    'new_password': 'Yangi-kuchli-parol-2026',
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=("password", "must_change_password"))
        update_session_auth_hash(request._request, user)
        return Response({"detail": "Parol muvaffaqiyatli o‘zgartirildi."})


class LogoutView(APIView):
    permission_classes = (IsAuthenticated,)
    authentication_classes = (SessionAuthentication,)

    @extend_schema(
        summary='Tizimdan chiqish',
        description='Joriy sessionni tugatadi.',
        tags=['Auth'],
        parameters=[CSRF_HEADER],
        request=None,
        responses={
            status.HTTP_200_OK: LoginResponseSerializer,
            **common_error_responses(),
        },
    )
    def post(self, request):
        logout(request._request)
        return Response({"detail": "Tizimdan chiqildi."})


class AccountCreateView(GenericAPIView):
    serializer_class = AccountCreateSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAccounts,
    )
    authentication_classes = (SessionAuthentication,)

    @extend_schema(
        summary='Account yaratish',
        description=(
            'manage_accounts huquqli operator pupil yoki teacher account '
            'yaratadi va bir martalik vaqtinchalik parol qaytaradi.'
        ),
        tags=['Accounts'],
        parameters=[CSRF_HEADER],
        request=AccountCreateSerializer,
        responses={
            status.HTTP_201_CREATED: AccountCreatedResponseSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Account yaratish',
                value={
                    'username': 'pupil07',
                    'first_name': 'Aziza',
                    'last_name': 'Karimova',
                    'role': 'pupil',
                    'preferred_language': 'uz',
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pending_user = User(
            username=serializer.validated_data["username"],
            role=serializer.validated_data["role"],
            first_name=serializer.validated_data.get("first_name", ""),
            last_name=serializer.validated_data.get("last_name", ""),
            preferred_language=serializer.validated_data.get(
                "preferred_language",
                "",
            ),
        )
        temporary_password = generate_temporary_password(pending_user)
        with transaction.atomic():
            user = serializer.save(password=temporary_password)
            AccountLifecycleAudit.objects.create(
                actor=request.user,
                target=user,
                action=AccountLifecycleAudit.Action.CREATED,
            )
        user.temporary_password = temporary_password
        response = AccountCreatedResponseSerializer(user).data
        return Response(
            response,
            status=status.HTTP_201_CREATED,
            headers={"Cache-Control": "no-store"},
        )

class AccountResetPasswordView(GenericAPIView):
    serializer_class = AccountResetPasswordSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAccounts,
    )
    authentication_classes = (SessionAuthentication,)

    @extend_schema(
        summary='Account parolini qayta o‘rnatish',
        description='manage_accounts huquqli operator vaqtinchalik parol yaratadi.',
        tags=['Accounts'],
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description='Paroli qayta o‘rnatiladigan account IDsi.',
            ),
            CSRF_HEADER,
        ],
        request=AccountResetPasswordSerializer,
        responses={
            status.HTTP_200_OK: AccountCreatedResponseSerializer,
            **common_error_responses(),
        },
    )
    def post(self, request, pk):
        with transaction.atomic():
            try:
                target_user = User.objects.select_for_update().get(pk=pk)
            except User.DoesNotExist:
                return Response(
                    {"detail": "Foydalanuvchi topilmadi."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if target_user.is_staff or target_user.is_superuser:
                return Response(
                    {
                        "detail": (
                            "Staff yoki superuser hisobini bu API orqali "
                            "qayta tiklab bo‘lmaydi."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            temporary_password = generate_temporary_password(
                target_user,
            )
            target_user.set_password(temporary_password)
            target_user.must_change_password = True
            target_user.save(update_fields=["password", "must_change_password"])
            AccountLifecycleAudit.objects.create(
                actor=request.user,
                target=target_user,
                action=AccountLifecycleAudit.Action.PASSWORD_RESET,
            )
            target_user.temporary_password = temporary_password
            response = AccountCreatedResponseSerializer(target_user).data
        return Response(
            response,
            status=status.HTTP_200_OK,
            headers={"Cache-Control": "no-store"},
        )
