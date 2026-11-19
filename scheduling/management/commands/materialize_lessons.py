from datetime import date

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from scheduling.services import materialize_lessons


class Command(BaseCommand):
    help = 'WeeklySlot qoidalaridan cheklangan sana oynasi uchun darslarni yaratadi.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from',
            dest='from_date',
            type=date.fromisoformat,
            required=True,
        )
        parser.add_argument(
            '--to',
            dest='to_date',
            type=date.fromisoformat,
            required=True,
        )

    def handle(self, *args, **options):
        try:
            sessions = materialize_lessons(
                from_date=options['from_date'],
                to_date=options['to_date'],
            )
        except ValidationError as exc:
            raise CommandError(exc.messages) from exc
        self.stdout.write(f'{len(sessions)} ta yangi dars yaratildi.')
