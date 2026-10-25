from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models import Q


class LMSUserManager(UserManager):
    @staticmethod
    def _validate_role(extra_fields):
        role = extra_fields.get('role')
        if role not in User.Role.values:
            raise ValueError("Users must have the 'pupil' or 'teacher' role.")

    def create_user(self, username, email=None, password=None, **extra_fields):
        self._validate_role(extra_fields)
        return super().create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        self._validate_role(extra_fields)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        PUPIL = 'pupil', 'Pupil'
        TEACHER = 'teacher', 'Teacher'

    role = models.CharField(max_length=7, choices=Role.choices)
    must_change_password = models.BooleanField(default=False)
    preferred_language = models.CharField(max_length=10, blank=True, default='')

    objects = LMSUserManager()

    REQUIRED_FIELDS = ['role']

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(role__in=['pupil', 'teacher']),
                name='accounts_user_role_valid',
            ),
        ]
