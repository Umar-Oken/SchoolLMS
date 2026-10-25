from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User


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
