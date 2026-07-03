"""Erzeugt die Miniaturbilder bestehender Dokumente neu.

Nützlich nach einer Auflösungs-/Format-Änderung der Thumbnails.

    python manage.py regen_thumbnails
"""
from django.core.management.base import BaseCommand

from documents import pipeline
from documents.models import DocumentVersion


class Command(BaseCommand):
    help = "Erzeugt die Miniaturbilder bestehender Dokumente neu."

    def handle(self, *args, **options):
        created = 0
        total = 0
        for version in DocumentVersion.objects.all().iterator():
            total += 1
            if pipeline.generate_thumbnail(version):
                created += 1

        self.stdout.write(
            self.style.SUCCESS(f"Fertig: {created} von {total} Miniaturbildern erzeugt.")
        )
