"""Backfill: erkennt ASNs aus Barcodes/QR in bereits abgelegten Dokumenten.

Für Dokumente, die vor der Barcode-Erkennung (STOAA-516) aufgenommen wurden.
Scannt die Archiv-PDFs (bzw. Original-Bilder) der bestehenden Versionen nach
Code128-/QR-ASN-Etiketten und leitet einen Treffer durch den bestehenden
ASN-Service (``match_and_reconcile``) – **ohne Re-OCR**. Damit werden erneut
eingescannte Papierdokumente nachträglich ihrem ursprünglichen Dokument
zugeordnet (statt als Duplikat liegenzubleiben).

    python manage.py asn_backfill              # alle current-Versionen scannen
    python manage.py asn_backfill --dry-run    # nur berichten, nichts ändern
    python manage.py asn_backfill --all        # ALLE Versionen, nicht nur current

Idempotent: eine bereits per Barcode/QR protokollierte Version (``ASNScan`` mit
demselben ``matched_by``) wird übersprungen, sodass wiederholte Läufe keine
Duplikat-Scans oder erneuten Umzüge auslösen. Am Ende steht eine Zusammenfassung
(erkannt / zugeordnet / übersprungen).
"""
from django.core.management.base import BaseCommand
from django.db.models import F

from documents.models import ASNScan, DocumentVersion
from documents.services import asn as asn_service
from documents.services import asn_barcode


class Command(BaseCommand):
    help = "Erkennt ASNs aus Barcodes/QR in bestehenden Dokumenten (ohne Re-OCR)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur berichten, welche ASNs erkannt würden – nichts ändern.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Alle Versionen scannen, nicht nur die jeweils aktuelle.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        qs = DocumentVersion.objects.select_related("document").order_by("pk")
        if not options["all"]:
            # Nur die jeweils aktuelle Version eines Dokuments (Regelfall).
            qs = qs.filter(document__current_version=F("pk"))

        detected = 0
        assigned = 0
        skipped = 0

        for version in qs.iterator():
            # Idempotenz: bereits per Barcode/QR gescannte Version überspringen.
            if ASNScan.objects.filter(
                version=version, matched_by__in=("Barcode", "QR")
            ).exists():
                skipped += 1
                continue

            found = asn_barcode.scan_asn(version)
            if found is None:
                skipped += 1
                continue

            asn, matched_by = found
            detected += 1
            label = asn_service.format_asn(asn)

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] Version #{version.id} (Dok #{version.document_id}) "
                    f"→ {label} via {matched_by}"
                )
                continue

            result = asn_service.match_and_reconcile(
                version,
                actor=version.created_by,
                asn=asn,
                matched_by=matched_by,
            )
            if result.get("matched"):
                assigned += 1
                moved = " (Version umgehängt)" if result.get("moved") else ""
                self.stdout.write(
                    f"  Version #{version.id} → {label} via {matched_by}"
                    f" – Dok #{result['document_id']}{moved}"
                )
            else:
                self.stdout.write(
                    f"  Version #{version.id} → {label} via {matched_by}"
                    " – kein bestehendes Dokument, unverändert"
                )

        summary = (
            f"Fertig: {detected} erkannt, {assigned} zugeordnet, "
            f"{skipped} übersprungen."
        )
        if dry_run:
            summary += " (dry-run – nichts geändert)"
        self.stdout.write(self.style.SUCCESS(summary))
