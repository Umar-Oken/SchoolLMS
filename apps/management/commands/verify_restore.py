import json
from pathlib import Path

from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from files.models import Attachment
from root.recovery import database_inventory


class Command(BaseCommand):
    help = 'Alohida restore targetidagi aggregate data va private bytesni tekshiradi.'

    def add_arguments(self, parser):
        parser.add_argument('--inventory', required=True)
        parser.add_argument('--require-records', action='store_true')

    def handle(self, *args, **options):
        expected = json.loads(
            Path(options['inventory']).read_text(encoding='utf-8'),
        )
        actual = database_inventory()
        fields = (
            'course_offerings',
            'submissions',
            'grades',
            'attempts',
            'attachments',
            'attachment_key_digest',
        )
        mismatch = [field for field in fields if actual.get(field) != expected.get(field)]
        if mismatch:
            raise CommandError('Restore inventory mismatch.')
        if options['require_records'] and any(
            actual[field] == 0
            for field in ('course_offerings', 'submissions', 'grades', 'attempts')
        ):
            raise CommandError('Restore drill requires synthetic business records.')

        storage = storages['private']
        missing = [
            str(key)
            for key in Attachment.objects.values_list('key', flat=True)
            if not storage.exists(str(key))
        ]
        if missing:
            raise CommandError('Restore private object reference mismatch.')
        self.stdout.write('restore verification passed')
