"""Legt für Altbestand einen synthetischen „Upload"-Audit-Eintrag an.

Dokumente, die vor Einführung der Audit-Trail-Ansicht angelegt wurden, haben
u. U. keinen Verlauf. Dieser Befehl ergänzt genau einen Basis-Eintrag pro
Dokument, das noch gar keinen Audit-Eintrag besitzt – idempotent, d. h. ein
erneuter Lauf ändert nichts.

    python manage.py backfill_audit

Der Zeitstempel wird auf ``added_at`` des Dokuments gesetzt (chronologisch
korrekt), da ``timestamp`` normalerweise via ``auto_now_add`` gesetzt wird.
Bereits protokollierte Ereignisse (Upload/OCR/Klassifizierung neuerer Dokumente)
werden nicht angetastet.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from documents.models import AuditLogEntry, Document


class Command(BaseCommand):
    help = "Ergänzt fehlende Basis-Audit-Einträge für Altbestand (idempotent)."

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for document in Document.objects.select_related("owner").iterator():
            version_ids = [str(v.id) for v in document.versions.all()]
            has_audit = AuditLogEntry.objects.filter(
                Q(object_type="Document", object_id=str(document.id))
                | Q(object_type="DocumentVersion", object_id__in=version_ids)
            ).exists()
            if has_audit:
                skipped += 1
                continue

            entry = AuditLogEntry.objects.create(
                actor=document.owner,
                action="upload",
                object_type="Document",
                object_id=str(document.id),
                detail={"title": document.title, "backfilled": True},
            )
            # timestamp ist auto_now_add → beim Anlegen ignoriert; hier nachziehen.
            AuditLogEntry.objects.filter(pk=entry.pk).update(timestamp=document.added_at)
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {created} Basis-Einträge ergänzt, {skipped} übersprungen."
            )
        )
