import json

from django.core.management.base import BaseCommand

from root.monitoring import background_status


class Command(BaseCommand):
    help = 'Fon ishlarining xavfsiz, agregat operational holatini JSON chiqaradi.'

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(background_status(), sort_keys=True))
