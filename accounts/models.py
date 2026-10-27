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
        permissions = [
            ('manage_accounts', 'Can manage accounts'),
            ('manage_academics', 'Can manage academics'),
        ]


class CapabilityGrantAudit(models.Model):
    actor = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='capability_grants_made',
    )
    target = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='capability_grant_audits',
    )
    capability = models.CharField(max_length=32)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')


class AccountLifecycleAudit(models.Model):
    class Action(models.TextChoices):
        CREATED = 'created', 'Created'
        PASSWORD_RESET = 'password_reset', 'Password reset'

    actor = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='account_lifecycle_actions',
    )
    target = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='account_lifecycle_audits',
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')
