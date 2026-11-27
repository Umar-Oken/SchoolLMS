import json
from pathlib import Path

from django.core.management.base import BaseCommand

from root.recovery import database_inventory


class Command(BaseCommand):
    help = 'Restore comparison uchun faqat aggregate DB inventory yozadi.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        output = Path(options['output'])
        output.write_text(
            json.dumps(database_inventory(), sort_keys=True) + '\n',
            encoding='utf-8',
        )
        output.chmod(0o600)
