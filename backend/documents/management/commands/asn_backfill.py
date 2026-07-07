"""Scannt bestehende Archiv-PDFs nach ASN-Barcodes/QR-Codes und reconciled.

Nutzt nur Barcode-Erkennung (pyzbar) – kein Re-OCR. Idempotent.

    python manage.py asn_backfill           # tatsächlich reconcilen
    python manage.py asn_backfill --dry-run # nur ausgeben, was erkannt würde
"""
from django.core.management.base import BaseCommand

from documents.models import DocumentVersion
from documents.services import asn as asn_service
from documents.services.asn_barcode import scan_pdf_for_asn


class Command(BaseCommand):
    help = "ASN-Backfill: Barcode/QR-Erkennung auf bestehenden Archiv-PDFs (kein Re-OCR)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur anzeigen, was erkannt würde – keine Datenbankänderung.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        erkannt = zugeordnet = uebersprungen = 0

        qs = DocumentVersion.objects.select_related("document").order_by("id")
        total = qs.count()
        self.stdout.write(f"Scanne {total} Versionen …")

        for version in qs.iterator():
            pdf_path = asn_service.version_pdf_path(version)
            if not pdf_path:
                uebersprungen += 1
                continue

            try:
                asn = scan_pdf_for_asn(pdf_path)
            except Exception as exc:
                self.stderr.write(f"  WARN Version {version.pk}: {exc}")
                uebersprungen += 1
                continue

            if asn is None:
                uebersprungen += 1
                continue

            erkannt += 1
            self.stdout.write(
                f"  Version {version.pk} (Doc {version.document_id}): ASN {asn} per Barcode"
            )

            if dry_run:
                continue

            try:
                result = asn_service.match_and_reconcile(version)
                if result.get("matched"):
                    zugeordnet += 1
            except Exception as exc:
                self.stderr.write(f"  FEHLER Version {version.pk}: {exc}")
                uebersprungen += 1

        self.stdout.write(
            f"\nFertig: {erkannt} erkannt, {zugeordnet} zugeordnet, "
            f"{uebersprungen} übersprungen"
            + (" (dry-run, keine Änderungen)" if dry_run else "")
        )
