from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, ValidationError

from assignments.services import change_assignment_deadlines


class Command(BaseCommand):
    help = 'Vazifaning deadline qiymatlarini nazoratli ravishda almashtiradi.'

    def add_arguments(self, parser):
        parser.add_argument('--assignment-id', type=int, required=True)
        parser.add_argument('--available-at', required=True)
        parser.add_argument('--due-at', required=True)
        parser.add_argument('--closes-at', required=True)

    def handle(self, *args, **options):
        deadlines = {}
        for field in ('available_at', 'due_at', 'closes_at'):
            value = parse_datetime(options[field])
            if value is None or not timezone.is_aware(value):
                raise CommandError(
                    f'--{field.replace("_", "-")} ISO 8601 timezone-aware qiymat bo‘lishi kerak.'
                )
            deadlines[field] = value
        try:
            assignment = change_assignment_deadlines(
                options['assignment_id'],
                **deadlines,
            )
        except NotFound as exc:
            raise CommandError('Assignment topilmadi.') from exc
        except ValidationError as exc:
            raise CommandError(str(exc.detail)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f'Assignment {assignment.pk} deadline_version={assignment.deadline_version}'
            )
        )
