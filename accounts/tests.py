import logging
from io import StringIO

from django.contrib.auth.models import Permission
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import (
    AccountLifecycleAudit,
    CapabilityGrantAudit,
    User,
)
from accounts.permissions import CanManageAccounts, CanManageAcademics


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

    def test_only_manage_accounts_operator_can_access_account_endpoints(self):
        for user in (self.teacher, self.pupil):
            client = APIClient()
            client.force_authenticate(user=user)

            create_response = client.post(
                "/api/v1/accounts/",
                {"username": "denied-account", "role": User.Role.PUPIL},
                format="json",
            )
            reset_response = client.post(
                f"/api/v1/accounts/{self.pupil.pk}/reset-password/",
                {},
                format="json",
            )

            self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(reset_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(username="denied-account").exists())

    def test_create_returns_one_time_password_and_creates_limited_account(self):
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
                        "role": role,
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertIn("temporary_password", response.data)
                self.assertNotIn("password", response.data)
                user = User.objects.get(username=username)
                temporary_password = response.data["temporary_password"]
                self.assertTrue(user.check_password(temporary_password))
                self.assertNotEqual(user.password, temporary_password)
                self.assertTrue(user.must_change_password)
                self.assertEqual(user.role, role)
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)
                self.assertFalse(user.user_permissions.exists())
                self.assertFalse(user.course_enrollments.exists())
                audit = AccountLifecycleAudit.objects.get(target=user)
                self.assertEqual(audit.actor, self.operator)
                self.assertEqual(audit.action, AccountLifecycleAudit.Action.CREATED)

    def test_create_rejects_privilege_password_and_enrollment_fields(self):
        forbidden_fields = (
            {"is_staff": True},
            {"is_superuser": True},
            {"user_permissions": []},
            {"groups": []},
            {"manage_accounts": True},
            {"enrollment": 1},
            {"password": "caller-supplied-password"},
        )
        for extra in forbidden_fields:
            with self.subTest(extra=extra):
                response = self.operator_client.post(
                    "/api/v1/accounts/",
                    {
                        "username": f"rejected-{len(extra)}-{list(extra)[0]}",
                        "role": User.Role.TEACHER,
                        **extra,
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertNotIn("caller-supplied-password", response.content.decode())

    def test_reset_changes_password_and_invalidates_previous_sessions(self):
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

    def test_account_mutations_require_csrf_and_do_not_log_request_secrets(
        self,
    ):
        csrf_client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(
            csrf_client.login(
                username=self.operator.username,
                password="safe-operator-password",
            )
        )
        csrf_token = csrf_client.get("/api/v1/auth/csrf/").data["csrfToken"]

        rejected = csrf_client.post(
            "/api/v1/accounts/",
            {"username": "csrf-denied", "role": User.Role.PUPIL},
            format="json",
        )
        self.assertEqual(rejected.status_code, status.HTTP_403_FORBIDDEN)

        accepted = csrf_client.post(
            "/api/v1/accounts/",
            {"username": "csrf-accepted", "role": User.Role.PUPIL},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(accepted.status_code, status.HTTP_201_CREATED)

        request_secret = "never-log-this-password"
        log_output = StringIO()
        handler = logging.StreamHandler(log_output)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            rejected = csrf_client.post(
                "/api/v1/accounts/",
                {
                    "username": "secret-body-rejected",
                    "role": User.Role.PUPIL,
                    "password": request_secret,
                },
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
        finally:
            root_logger.removeHandler(handler)
            handler.close()
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn(request_secret, log_output.getvalue())

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
