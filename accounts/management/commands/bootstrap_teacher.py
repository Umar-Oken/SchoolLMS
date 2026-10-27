from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import CapabilityGrantAudit, User


CAPABILITIES = ("manage_accounts", "manage_academics")


class Command(BaseCommand):
    help = "Grant one capability to an existing teacher with an audit record."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True, help="Superuser operator username.")
        parser.add_argument("--target", required=True, help="Existing teacher username.")
        parser.add_argument(
            "--capability",
            required=True,
            choices=CAPABILITIES,
        )
        parser.add_argument("--reason", required=True)

    def handle(self, *args, **options):
        actor = self._get_actor(options["actor"])
        reason = options["reason"].strip()
        if not reason:
            raise CommandError("--reason bo‘sh bo‘lishi mumkin emas.")

        capability = options["capability"]
        permission = Permission.objects.get(
            content_type__app_label="accounts",
            codename=capability,
        )

        with transaction.atomic():
            target = self._get_target(options["target"])
            already_granted = target.user_permissions.filter(
                pk=permission.pk,
            ).exists()
            if not already_granted:
                target.user_permissions.add(permission)
                CapabilityGrantAudit.objects.create(
                    actor=actor,
                    target=target,
                    capability=capability,
                    reason=reason,
                )

        if already_granted:
            self.stdout.write(
                f"{target.username} allaqachon {capability} capability’siga ega."
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"{capability} {target.username} foydalanuvchisiga berildi."
            )
        )

    @staticmethod
    def _get_actor(username):
        try:
            actor = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError("Operator topilmadi.") from exc
        if not actor.is_active or not actor.is_superuser:
            raise CommandError("Actor faol Django superuser bo‘lishi kerak.")
        return actor

    @staticmethod
    def _get_target(username):
        try:
            target = User.objects.select_for_update().get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError("Maqsad foydalanuvchi topilmadi.") from exc
        if target.role != User.Role.TEACHER:
            raise CommandError("Capability faqat mavjud teacherga beriladi.")
        if not target.is_active:
            raise CommandError("Faol bo‘lmagan teacherga capability berilmaydi.")
        return target
