"""Wendet die Klassifizierungsregeln auf bestehende Dokumente an.

Nötig, um Altbestand nachträglich einzuordnen, nachdem Regeln angelegt wurden
(neue Uploads werden automatisch klassifiziert).

    python manage.py reclassify

Regeln überschreiben bereits gesetzte Einzelwerte (Typ/Korrespondent/Ablagepfad)
NICHT – nur leere Felder werden gefüllt, Tags werden ergänzt. Für inhaltsbasierte
Regeln sollte vorher ``reindex_text`` gelaufen sein (sonst fehlt der OCR-Text).
"""
from django.core.management.base import BaseCommand

from documents import classification
from documents.models import Document


class Command(BaseCommand):
    help = "Wendet Klassifizierungsregeln auf bestehende Dokumente an."

    def handle(self, *args, **options):
        classified = 0
        total = 0
        for document in Document.objects.select_related("current_version").iterator():
            total += 1
            result = classification.apply_rules(document)
            if result["rules"]:
                classified += 1
                self.stdout.write(
                    f"  #{document.id} „{document.title}“ → {', '.join(result['rules'])}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {classified} von {total} Dokumenten klassifiziert."
            )
        )
