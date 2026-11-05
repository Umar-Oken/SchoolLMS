from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils.crypto import get_random_string
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.generics import GenericAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

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
    LogoutInputSerializer,
    TokenPairResponseSerializer,
    UsernameCaseInsensitiveTokenObtainPairSerializer,
)
from root.openapi import common_error_responses, error_response


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
            'Legacy CSRF token yordamchisi. JWT API endpointlarida token olish '
            'yoki yuborish talab qilinmaydi.'
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


class LoginView(GenericAPIView):
    serializer_class = LoginInputSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get_authenticate_header(self, request):
        return JWTAuthentication().authenticate_header(request)

    @extend_schema(
        summary='Tizimga kirish',
        description=(
            'Username va parol bilan JWT access va refresh token beradi.'
        ),
        tags=['Auth'],
        request=LoginInputSerializer,
        responses={
            status.HTTP_200_OK: TokenPairResponseSerializer,
            status.HTTP_400_BAD_REQUEST: LoginErrorSerializer,
            status.HTTP_401_UNAUTHORIZED: error_response(
                'Username yoki parol noto‘g‘ri.',
                {'detail': 'No active account found with the given credentials'},
            ),
        },
        examples=[
            OpenApiExample(
                'Login so‘rovi',
                value={'username': 'teacher01', 'password': 'namuna-parol'},
                request_only=True,
            ),
            OpenApiExample(
                'JWT login javobi',
                value={'access': 'access-token', 'refresh': 'refresh-token'},
                response_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = UsernameCaseInsensitiveTokenObtainPairSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


@extend_schema_view(
    get=extend_schema(
        summary='Joriy profilni ko‘rish',
        description=        'Bearer JWT orqali kirgan foydalanuvchining profilini qaytaradi.',
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
    authentication_classes = (JWTAuthentication,)
    http_method_names = ("get", "patch", "head", "options")

    def get_object(self):
        return self.request.user


class ChangePasswordView(GenericAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = (IsAuthenticated,)
    authentication_classes = (JWTAuthentication,)

    @extend_schema(
        summary='Parolni almashtirish',
        description=(
            'Joriy parolni tekshiradi, yangi parolni validatsiya qiladi va '
            'JWT-authenticated joriy foydalanuvchi parolini yangilaydi.'
        ),
        tags=['Profil'],
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
        return Response({"detail": "Parol muvaffaqiyatli o‘zgartirildi."})


class LogoutView(GenericAPIView):
    serializer_class = LogoutInputSerializer
    permission_classes = (IsAuthenticated,)
    authentication_classes = (JWTAuthentication,)

    @extend_schema(
        summary='Tizimdan chiqish',
        description=(
            'Autentifikatsiyadan o‘tgan foydalanuvchining refresh tokenini '
            'blacklist qilib, keyingi refresh so‘rovlarini bekor qiladi.'
        ),
        tags=['Auth'],
        request=LogoutInputSerializer,
        responses={
            status.HTTP_204_NO_CONTENT: None,
            status.HTTP_400_BAD_REQUEST: error_response(
                'Refresh token yuborilmagan yoki body noto‘g‘ri.',
                {'refresh': ['This field is required.']},
            ),
            status.HTTP_401_UNAUTHORIZED: error_response(
                'JWT access yoki refresh token noto‘g‘ri.',
                {'detail': 'Token is invalid or expired'},
            ),
            status.HTTP_403_FORBIDDEN: error_response(
                'Refresh token joriy foydalanuvchiga tegishli emas.',
                {'detail': 'Refresh token does not belong to this user.'},
            ),
        },
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            refresh = RefreshToken(serializer.validated_data["refresh"])
        except TokenError as exc:
            raise AuthenticationFailed("Refresh token yaroqsiz.") from exc

        if str(refresh.get("user_id")) != str(request.user.pk):
            raise PermissionDenied(
                "Refresh token joriy foydalanuvchiga tegishli emas."
            )

        refresh.blacklist()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AccountCreateView(GenericAPIView):
    serializer_class = AccountCreateSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(
        summary='Account yaratish',
        description=(
            'Login talab qilmaydi. Faqat pupil yoki teacher roli bilan account '
            'yaratadi. Parol Django password hasher orqali saqlanadi; '
            'autentifikatsiya va CSRF token kerak emas.'
        ),
        tags=['Accounts'],
        request=AccountCreateSerializer,
        responses={
            status.HTTP_201_CREATED: CurrentUserSerializer,
            status.HTTP_400_BAD_REQUEST: error_response(
                'Username takrorlangan yoki request maydonlari noto‘g‘ri.',
                {'username': ['A user with that username already exists.']},
            ),
        },
        examples=[
            OpenApiExample(
                'Account yaratish',
                value={
                    'username': 'pupil07',
                    'first_name': 'Aziza',
                    'last_name': 'Karimova',
                    'role': 'pupil',
                    'password': 'Kuchli-parol-2026!',
                    'preferred_language': 'uz',
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            user = serializer.save()
            AccountLifecycleAudit.objects.create(
                actor=request.user if request.user.is_authenticated else None,
                target=user,
                action=AccountLifecycleAudit.Action.CREATED,
            )
        response = CurrentUserSerializer(user).data
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
    authentication_classes = (JWTAuthentication,)

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
