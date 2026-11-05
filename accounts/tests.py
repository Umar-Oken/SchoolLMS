from io import StringIO

from django.contrib import admin as django_admin
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import (
    AccountLifecycleAudit,
    CapabilityGrantAudit,
    User,
)
from accounts.permissions import (
    CanManageAccounts,
    CanManageAcademics,
    MustChangePasswordPermission,
)


class UserModelTests(TestCase):
    def test_create_user_hashes_password_and_requires_business_role(self):
        user = User.objects.create_user(
            username='pupil-one',
            password='safe-test-password',
            role=User.Role.PUPIL,
        )

        self.assertEqual(user.role, User.Role.PUPIL)
        self.assertTrue(user.check_password('safe-test-password'))
        self.assertNotEqual(user.password, 'safe-test-password')
        self.assertFalse(user.must_change_password)
        self.assertEqual(user.preferred_language, '')

    def test_manager_rejects_role_outside_the_two_business_roles(self):
        with self.assertRaisesMessage(ValueError, "pupil' or 'teacher"):
            User.objects.create_user(
                username='invalid-role',
                password='safe-test-password',
                role='admin',
            )

    def test_database_check_constraint_rejects_unknown_role(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create(username='invalid-db-role', role='admin')

    def test_superuser_remains_a_teacher_business_role(self):
        user = User.objects.create_superuser(
            username='technical-superuser',
            password='safe-test-password',
            role=User.Role.TEACHER,
        )

        self.assertEqual(user.role, User.Role.TEACHER)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_superuser_bypasses_capability_and_password_change_permissions(self):
        user = User.objects.create_superuser(
            username="permission-superuser",
            password="safe-test-password",
            role=User.Role.TEACHER,
            must_change_password=True,
        )
        request = type("Request", (), {"user": user})()

        self.assertTrue(CanManageAccounts().has_permission(request, None))
        self.assertTrue(CanManageAcademics().has_permission(request, None))
        self.assertTrue(MustChangePasswordPermission().has_permission(request, None))


class TeacherCapabilityTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(
            username='technical-operator',
            password='safe-test-password',
            role=User.Role.TEACHER,
        )
        self.teacher = User.objects.create_user(
            username='ordinary-teacher',
            password='safe-test-password',
            role=User.Role.TEACHER,
        )
        self.pupil = User.objects.create_user(
            username='capability-pupil',
            password='safe-test-password',
            role=User.Role.PUPIL,
        )

    def run_bootstrap(self, *, target, capability='manage_accounts', reason='Approved'):
        call_command(
            'bootstrap_teacher',
            actor=self.actor.username,
            target=target.username,
            capability=capability,
            reason=reason,
            stdout=StringIO(),
        )

    def test_ordinary_teacher_has_no_management_capability(self):
        self.assertFalse(self.teacher.has_perm('accounts.manage_accounts'))
        self.assertFalse(self.teacher.has_perm('accounts.manage_academics'))
        self.assertFalse(
            CanManageAccounts().has_permission(
                type('Request', (), {'user': self.teacher})(),
                None,
            )
        )
        self.assertFalse(
            CanManageAcademics().has_permission(
                type('Request', (), {'user': self.teacher})(),
                None,
            )
        )

    def test_command_grants_only_requested_capability_and_audits_actor_target_reason(self):
        self.run_bootstrap(target=self.teacher, capability='manage_accounts')

        self.assertTrue(self.teacher.has_perm('accounts.manage_accounts'))
        self.assertFalse(self.teacher.has_perm('accounts.manage_academics'))
        audit = CapabilityGrantAudit.objects.get()
        self.assertEqual(audit.actor, self.actor)
        self.assertEqual(audit.target, self.teacher)
        self.assertEqual(audit.capability, 'manage_accounts')
        self.assertEqual(audit.reason, 'Approved')

    def test_command_grants_academics_capability_without_accounts_capability(self):
        self.run_bootstrap(target=self.teacher, capability='manage_academics')

        self.assertTrue(self.teacher.has_perm('accounts.manage_academics'))
        self.assertFalse(self.teacher.has_perm('accounts.manage_accounts'))
        self.assertEqual(
            CapabilityGrantAudit.objects.get().capability,
            'manage_academics',
        )

    def test_repeated_grant_does_not_duplicate_permission_or_audit(self):
        self.run_bootstrap(target=self.teacher)
        self.run_bootstrap(target=self.teacher, reason='Repeated request')

        self.assertEqual(
            self.teacher.user_permissions.filter(
                codename='manage_accounts',
                content_type__app_label='accounts',
            ).count(),
            1,
        )
        self.assertEqual(CapabilityGrantAudit.objects.count(), 1)

    def test_command_rejects_pupil_target(self):
        with self.assertRaisesMessage(CommandError, 'faqat mavjud teacherga'):
            self.run_bootstrap(target=self.pupil)

        self.assertFalse(self.pupil.has_perm('accounts.manage_accounts'))
        self.assertFalse(CapabilityGrantAudit.objects.exists())


class AccountLifecycleAPITests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="account-operator",
            password="safe-operator-password",
            role=User.Role.TEACHER,
        )
        permission = Permission.objects.get(
            content_type__app_label="accounts",
            codename="manage_accounts",
        )
        self.operator.user_permissions.add(permission)
        self.pupil = User.objects.create_user(
            username="existing-pupil",
            password="old-pupil-password",
            role=User.Role.PUPIL,
        )
        self.teacher = User.objects.create_user(
            username="existing-teacher",
            password="old-teacher-password",
            role=User.Role.TEACHER,
        )
        self.operator_client = APIClient()
        self.operator_client.force_authenticate(user=self.operator)

    def test_only_manage_accounts_operator_can_reset_accounts(self):
        for user in (self.teacher, self.pupil):
            client = APIClient()
            client.force_authenticate(user=user)

            reset_response = client.post(
                f"/api/v1/accounts/{self.pupil.pk}/reset-password/",
                {},
                format="json",
            )

            self.assertEqual(reset_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_uses_supplied_password_and_creates_limited_account(self):
        for username, role in (
                ("new-pupil", User.Role.PUPIL),
                ("new-teacher", User.Role.TEACHER),
        ):
            with self.subTest(role=role):
                response = self.operator_client.post(
                    "/api/v1/accounts/",
                    {
                        "username": username,
                        "first_name": "New",
                        "last_name": "Learner",
                        "role": role,
                        "password": "Valid-Account-Password-2026!",
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertNotIn("password", response.data)
                self.assertNotIn("temporary_password", response.data)
                user = User.objects.get(username=username)
                self.assertTrue(user.check_password("Valid-Account-Password-2026!"))
                self.assertNotEqual(user.password, "Valid-Account-Password-2026!")
                self.assertFalse(user.must_change_password)
                self.assertEqual(user.role, role)
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)
                self.assertFalse(user.user_permissions.exists())
                self.assertFalse(user.course_enrollments.exists())
                audit = AccountLifecycleAudit.objects.get(target=user)
                self.assertEqual(audit.actor, self.operator)
                self.assertEqual(audit.action, AccountLifecycleAudit.Action.CREATED)

    def test_public_registration_creates_both_roles_without_auth_or_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        password = "Valid-Account-Password-2026!"

        for role in (User.Role.PUPIL, User.Role.TEACHER):
            with self.subTest(role=role):
                username = f"public-{role}"
                response = client.post(
                    "/api/v1/accounts/",
                    {
                        "username": username,
                        "first_name": "Public",
                        "last_name": "User",
                        "role": role,
                        "password": password,
                        "preferred_language": "uz",
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertNotIn("password", response.data)
                self.assertEqual(response.data["role"], role)
                user = User.objects.get(username=username)
                self.assertTrue(user.check_password(password))
                self.assertTrue(user.is_active)
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)
                self.assertIsNone(
                    AccountLifecycleAudit.objects.get(target=user).actor
                )

                login = client.post(
                    "/api/v1/auth/login/",
                    {"username": username.upper(), "password": password},
                    format="json",
                )
                self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_public_registration_rejects_privilege_fields_and_admin_roles(self):
        client = APIClient(enforce_csrf_checks=True)
        invalid_fields = (
            {"role": "admin"},
            {"role": "superuser"},
            {"is_superuser": True},
            {"is_staff": True},
        )

        for index, extra in enumerate(invalid_fields):
            with self.subTest(extra=extra):
                username = f"rejected-public-{index}"
                response = client.post(
                    "/api/v1/accounts/",
                    {
                        "username": username,
                        "first_name": "Rejected",
                        "last_name": "User",
                        "role": User.Role.PUPIL,
                        "password": "Valid-Account-Password-2026!",
                        **extra,
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertFalse(User.objects.filter(username=username).exists())

    def test_public_registration_rejects_weak_password_and_duplicate_username(self):
        client = APIClient(enforce_csrf_checks=True)
        data = {
            "username": "duplicate-public-user",
            "first_name": "Public",
            "last_name": "User",
            "role": User.Role.PUPIL,
            "password": "Valid-Account-Password-2026!",
        }
        created = client.post("/api/v1/accounts/", data, format="json")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        weak_password = client.post(
            "/api/v1/accounts/",
            {**data, "username": "weak-password-user", "password": "123"},
            format="json",
        )
        duplicate = client.post("/api/v1/accounts/", data, format="json")

        self.assertEqual(weak_password.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)

    def test_jwt_login_rejects_inactive_and_missing_users(self):
        inactive = User.objects.create_user(
            username="inactive-login-user",
            password="Valid-Account-Password-2026!",
            role=User.Role.PUPIL,
            is_active=False,
        )
        client = APIClient()

        inactive_login = client.post(
            "/api/v1/auth/login/",
            {
                "username": inactive.username,
                "password": "Valid-Account-Password-2026!",
            },
            format="json",
        )
        missing_login = client.post(
            "/api/v1/auth/login/",
            {
                "username": "not-a-real-user",
                "password": "Valid-Account-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(inactive_login.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(missing_login.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_jwt_login_rejects_case_insensitive_username_collisions(self):
        User.objects.create_user(
            username="CaseCollision",
            password="Valid-Account-Password-2026!",
            role=User.Role.TEACHER,
        )
        User.objects.create_user(
            username="casecollision",
            password="Valid-Account-Password-2026!",
            role=User.Role.PUPIL,
        )
        client = APIClient()

        for username in ("CaseCollision", "CASECOLLISION"):
            with self.subTest(username=username):
                response = client.post(
                    "/api/v1/auth/login/",
                    {
                        "username": username,
                        "password": "Valid-Account-Password-2026!",
                    },
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                )

    def test_create_rejects_privilege_password_and_enrollment_fields(self):
        forbidden_fields = (
            {"is_staff": True},
            {"is_superuser": True},
            {"user_permissions": []},
            {"groups": []},
            {"manage_accounts": True},
            {"enrollment": 1},
        )
        for extra in forbidden_fields:
            with self.subTest(extra=extra):
                response = self.operator_client.post(
                    "/api/v1/accounts/",
                    {
                        "username": f"rejected-{len(extra)}-{list(extra)[0]}",
                        "first_name": "Rejected",
                        "last_name": "Teacher",
                        "role": User.Role.TEACHER,
                        "password": "Valid-Account-Password-2026!",
                        **extra,
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def legacy_session_reset_flow(self):
        first_session = APIClient()
        second_session = APIClient()
        self.assertTrue(
            first_session.login(
                username=self.pupil.username,
                password="old-pupil-password",
            )
        )
        self.assertTrue(
            second_session.login(
                username=self.pupil.username,
                password="old-pupil-password",
            )
        )

        response = self.operator_client.post(
            f"/api/v1/accounts/{self.pupil.pk}/reset-password/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Cache-Control"], "no-store")
        temporary_password = response.data["temporary_password"]
        self.pupil.refresh_from_db()
        self.assertTrue(self.pupil.check_password(temporary_password))
        self.assertFalse(self.pupil.check_password("old-pupil-password"))
        self.assertTrue(self.pupil.must_change_password)
        self.assertEqual(
            first_session.get("/api/v1/me/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            second_session.get("/api/v1/me/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertFalse(
            first_session.login(
                username=self.pupil.username,
                password="old-pupil-password",
            )
        )
        self.assertTrue(
            first_session.login(
                username=self.pupil.username,
                password=temporary_password,
            )
        )
        self.assertEqual(
            first_session.get("/api/v1/courses/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        audit = AccountLifecycleAudit.objects.get(
            target=self.pupil,
            action=AccountLifecycleAudit.Action.PASSWORD_RESET,
        )
        self.assertEqual(audit.actor, self.operator)

    def test_reset_revokes_existing_jwts(self):
        sessions = []
        for _ in range(2):
            client = APIClient()
            login = client.post(
                "/api/v1/auth/login/",
                {
                    "username": self.pupil.username,
                    "password": "old-pupil-password",
                },
                format="json",
            )
            self.assertEqual(login.status_code, status.HTTP_200_OK)
            client.credentials(
                HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
            )
            sessions.append(client)

        response = self.operator_client.post(
            f"/api/v1/accounts/{self.pupil.pk}/reset-password/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        temporary_password = response.data["temporary_password"]
        self.pupil.refresh_from_db()
        self.assertTrue(self.pupil.check_password(temporary_password))
        self.assertTrue(self.pupil.must_change_password)
        for client in sessions:
            self.assertEqual(
                client.get("/api/v1/me/").status_code,
                status.HTTP_401_UNAUTHORIZED,
            )
        login = APIClient().post(
            "/api/v1/auth/login/",
            {
                "username": self.pupil.username,
                "password": temporary_password,
            },
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertIn("refresh", login.data)

    def test_reset_rejects_input_fields_and_does_not_change_password(self):
        response = self.operator_client.post(
            f"/api/v1/accounts/{self.pupil.pk}/reset-password/",
            {
                "password": "caller-supplied-password",
                "is_staff": True,
                "manage_accounts": True,
                "enrollment": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("caller-supplied-password", response.content.decode())
        self.pupil.refresh_from_db()
        self.assertTrue(self.pupil.check_password("old-pupil-password"))
        self.assertFalse(self.pupil.must_change_password)

    def test_account_create_is_csrf_free_for_jwt_and_session_clients(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_authenticate(user=self.operator)
        response = client.post(
            "/api/v1/accounts/",
            {
                "username": "jwt-created-account",
                "first_name": "New",
                "last_name": "Account",
                "role": User.Role.PUPIL,
                "password": "Valid-Account-Password-2026!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("password", response.data)

        session_client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(
            session_client.login(
                username=self.operator.username,
                password="safe-operator-password",
            )
        )
        session_response = session_client.post(
            "/api/v1/accounts/",
            {
                "username": "session-created-account",
                "first_name": "Session",
                "last_name": "Account",
                "role": User.Role.PUPIL,
                "password": "Valid-Account-Password-2026!",
            },
            format="json",
        )
        self.assertEqual(session_response.status_code, status.HTTP_201_CREATED)

    def test_command_requires_active_superuser_actor(self):
        with self.assertRaisesMessage(CommandError, 'superuser'):
            call_command(
                'bootstrap_teacher',
                actor=self.teacher.username,
                target=self.teacher.username,
                capability='manage_accounts',
                reason='Approved',
                stdout=StringIO(),
            )

    def test_profile_patch_cannot_grant_capability(self):
        client = APIClient()
        client.force_authenticate(user=self.teacher)

        response = client.patch(
            '/api/v1/me/',
            {'manage_accounts': True},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self.teacher.has_perm('accounts.manage_accounts'))
        self.assertFalse(CapabilityGrantAudit.objects.exists())


class AuthenticationFlowAPITests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="session-operator",
            password="safe-test-password",
            role=User.Role.TEACHER,
        )
        permission = Permission.objects.get(
            content_type__app_label="accounts",
            codename="manage_accounts",
        )
        self.operator.user_permissions.add(permission)

    def test_jwt_account_create_and_logout_flow_is_csrf_free(self):
        anonymous_client = APIClient(enforce_csrf_checks=True)
        anonymous_create = anonymous_client.post(
            "/api/v1/accounts/",
            {
                "username": "anonymous-account",
                "first_name": "Anonymous",
                "last_name": "Account",
                "role": User.Role.PUPIL,
                "password": "Valid-Account-Password-2026!",
            },
            format="json",
        )
        self.assertEqual(
            anonymous_create.status_code,
            status.HTTP_201_CREATED,
        )

        invalid_login = anonymous_client.post(
            "/api/v1/auth/login/",
            {
                "username": self.operator.username,
                "password": "incorrect-password",
            },
            format="json",
        )
        self.assertEqual(invalid_login.status_code, status.HTTP_401_UNAUTHORIZED)

        login = anonymous_client.post(
            "/api/v1/auth/login/",
            {
                "username": self.operator.username,
                "password": "safe-test-password",
            },
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertIn("access", login.data)
        self.assertIn("refresh", login.data)

        client = APIClient(enforce_csrf_checks=True)
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
        )
        created = client.post(
            "/api/v1/accounts/",
            {
                "username": "fresh-jwt-account",
                "first_name": "Fresh",
                "last_name": "Account",
                "role": User.Role.PUPIL,
                "password": "Valid-Account-Password-2026!",
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        new_user = User.objects.get(username="fresh-jwt-account")
        self.assertTrue(new_user.check_password("Valid-Account-Password-2026!"))
        self.assertNotEqual(new_user.password, "Valid-Account-Password-2026!")
        self.assertNotIn("password", created.data)

        new_login = anonymous_client.post(
            "/api/v1/auth/login/",
            {
                "username": new_user.username,
                "password": "Valid-Account-Password-2026!",
            },
            format="json",
        )
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)
        protected_client = APIClient(enforce_csrf_checks=True)
        protected_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {new_login.data['access']}"
        )
        self.assertEqual(
            protected_client.get("/api/v1/me/").status_code,
            status.HTTP_200_OK,
        )

        logout = client.post(
            "/api/v1/auth/logout/",
            {"refresh": login.data["refresh"]},
            format="json",
        )
        self.assertEqual(logout.status_code, status.HTTP_204_NO_CONTENT)
        refresh = anonymous_client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": login.data["refresh"]},
            format="json",
        )
        self.assertEqual(refresh.status_code, status.HTTP_401_UNAUTHORIZED)

        relogin = anonymous_client.post(
            "/api/v1/auth/login/",
            {
                "username": self.operator.username,
                "password": "safe-test-password",
            },
            format="json",
        )
        self.assertEqual(relogin.status_code, status.HTTP_200_OK)

    def test_superuser_teacher_can_create_account_with_jwt_without_capability(self):
        superuser = User.objects.create_superuser(
            username="umar-superuser",
            password="safe-test-password",
            role=User.Role.TEACHER,
            must_change_password=True,
        )
        client = APIClient(enforce_csrf_checks=True)
        login = client.post(
            "/api/v1/auth/login/",
            {
                "username": superuser.username,
                "password": "safe-test-password",
            },
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
        )
        response = client.post(
            "/api/v1/accounts/",
            {
                "username": "swagger-created-pupil",
                "first_name": "New",
                "last_name": "Pupil",
                "role": User.Role.PUPIL,
                "password": "Valid-Account-Password-2026!",
                "preferred_language": "uz",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["username"], "swagger-created-pupil")
        self.assertEqual(response.data["preferred_language"], "uz")
        self.assertTrue(superuser.is_superuser)
        self.assertEqual(superuser.role, User.Role.TEACHER)
        self.assertTrue(
            User.objects.get(username="swagger-created-pupil").check_password(
                "Valid-Account-Password-2026!"
            )
        )

    def test_public_account_create_ignores_invalid_jwt(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

        response = client.post(
            "/api/v1/accounts/",
            {
                "username": "invalid-token-is-ignored",
                "first_name": "Public",
                "last_name": "User",
                "role": User.Role.PUPIL,
                "password": "Valid-Account-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class AuthenticationOpenAPITests(TestCase):
    def test_jwt_security_and_password_schemas(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        security_schemes = schema["components"]["securitySchemes"]
        self.assertEqual(
            security_schemes["jwtAuth"],
            {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            },
        )
        self.assertNotIn("basicAuth", security_schemes)

        account_operation = schema["paths"]["/api/v1/accounts/"]["post"]
        self.assertEqual(account_operation["security"], [{}])
        self.assertNotIn(
            "X-CSRFToken",
            {parameter["name"] for parameter in account_operation.get("parameters", [])},
        )
        account_schema = schema["components"]["schemas"]["AccountCreate"]
        self.assertTrue(
            {
                "username",
                "first_name",
                "last_name",
                "role",
                "password",
            }.issubset(account_schema["required"])
        )
        self.assertEqual(
            account_schema["properties"]["password"]["format"],
            "password",
        )
        self.assertTrue(
            account_schema["properties"]["password"]["writeOnly"]
        )
        role_schema_name = account_schema["properties"]["role"]["$ref"].rsplit(
            "/",
            1,
        )[-1]
        self.assertEqual(
            schema["components"]["schemas"][role_schema_name]["enum"],
            [User.Role.PUPIL, User.Role.TEACHER],
        )

        login_operation = schema["paths"]["/api/v1/auth/login/"]["post"]
        self.assertEqual(login_operation["security"], [{}])
        logout_operation = schema["paths"]["/api/v1/auth/logout/"]["post"]
        self.assertEqual(logout_operation["security"], [{"jwtAuth": []}])
        logout_request = schema["components"]["schemas"]["LogoutInput"]
        self.assertEqual(logout_request["required"], ["refresh"])
        self.assertNotIn("password", logout_request["properties"])


class DjangoAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin-operator",
            password="safe-test-password",
            role=User.Role.TEACHER,
        )
        self.admin_user.is_active = True
        self.admin_user.save(update_fields=("is_active",))
        self.staff_user = User.objects.create_user(
            username="limited-admin",
            password="safe-test-password",
            role=User.Role.TEACHER,
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username="listed-account",
            password="safe-test-password",
            role=User.Role.PUPIL,
        )
        self.client.force_login(self.admin_user)

    def test_custom_user_and_group_are_registered_in_admin(self):
        user_admin = django_admin.site._registry[User]
        group_admin = django_admin.site._registry[Group]
        request = RequestFactory().get("/admin/accounts/user/")
        request.user = self.admin_user
        self.assertIn("role", user_admin.get_list_display(request))
        self.assertIn("is_superuser", user_admin.get_list_display(request))
        self.assertIsNotNone(group_admin)

        user_list = self.client.get("/admin/accounts/user/")
        self.assertEqual(user_list.status_code, 200)
        self.assertContains(user_list, self.user.username)
        self.assertContains(user_list, "Users")
        self.assertEqual(self.client.get("/admin/auth/group/").status_code, 200)

    def test_superuser_can_create_account_from_admin(self):
        response = self.client.post(
            "/admin/accounts/user/add/",
            {
                "username": "admin-created-account",
                "password1": "Valid-Account-Password-2026!",
                "password2": "Valid-Account-Password-2026!",
                "role": User.Role.TEACHER,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="admin-created-account")
        self.assertEqual(user.role, User.Role.TEACHER)
        self.assertTrue(user.check_password("Valid-Account-Password-2026!"))

    def test_superuser_can_manage_users_groups_and_role_permissions(self):
        user_admin = django_admin.site._registry[User]
        request = RequestFactory().get("/admin/accounts/user/")
        request.user = self.admin_user
        fields = {
            field
            for _, options in user_admin.get_fieldsets(request, self.user)
            for field in options.get("fields", ())
        }
        self.assertTrue(
            {"groups", "user_permissions", "is_staff", "is_superuser"}.issubset(
                fields
            )
        )

        account_permission = Permission.objects.get(
            content_type__app_label="accounts",
            codename="manage_accounts",
        )
        self.assertTrue(self.admin_user.has_perm("accounts.manage_accounts"))
        self.user.user_permissions.add(account_permission)
        self.assertTrue(self.user.has_perm("accounts.manage_accounts"))

    def test_non_superuser_cannot_edit_user_or_group_permissions(self):
        permissions = Permission.objects.filter(
            content_type__app_label="auth",
            codename__in=("view_user", "change_user", "view_group", "change_group"),
        )
        self.staff_user.user_permissions.add(*permissions)
        user_admin = django_admin.site._registry[User]
        request = RequestFactory().get("/admin/accounts/user/")
        request.user = self.staff_user

        fields = {
            field
            for _, options in user_admin.get_fieldsets(request, self.user)
            for field in options.get("fields", ())
        }
        self.assertNotIn("groups", fields)
        self.assertNotIn("user_permissions", fields)
        self.assertNotIn("is_superuser", fields)
        self.assertIn("is_active", user_admin.get_readonly_fields(request, self.user))
        self.assertIn("is_staff", user_admin.get_readonly_fields(request, self.user))

        group_admin = django_admin.site._registry[Group]
        self.assertFalse(group_admin.has_module_permission(request))
        self.client.force_login(self.staff_user)
        self.assertEqual(self.client.get("/admin/auth/group/").status_code, 403)
