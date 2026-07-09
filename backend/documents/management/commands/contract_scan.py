"""Backfill für das Contract Center.

Beispiele:

    python manage.py contract_scan
    python manage.py contract_scan --all
    python manage.py contract_scan --dry-run --limit 20
"""
from django.core.management.base import BaseCommand

from documents.models import Document, DocumentVersion
from documents.services import contracts


class Command(BaseCommand):
    help = "Scannt Dokumente nach Vertragsdaten und legt ContractRecord-Einträge an."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Auch Dokumente mit bestehendem ContractRecord erneut scannen.",
        )
        parser.add_argument(
            "--include-unready",
            action="store_true",
            help="Auch noch nicht READY verarbeitete Dokumente berücksichtigen.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximal so viele Dokumente scannen (0 = kein Limit).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur erkennen und ausgeben, keine ContractRecords speichern.",
        )

    def handle(self, *args, **options):
        qs = Document.objects.select_related(
            "current_version",
            "correspondent",
            "document_type",
            "case_file",
        ).exclude(current_version__isnull=True)
        if not options["include_unready"]:
            qs = qs.filter(
                current_version__processing_state=DocumentVersion.ProcessingState.READY
            )
        if not options["all"]:
            qs = qs.filter(contract_record__isnull=True)
        qs = qs.order_by("id")
        limit = max(0, int(options["limit"] or 0))
        if limit:
            qs = qs[:limit]

        dry_run = options["dry_run"]
        total = qs.count()
        counters = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "no_contract": 0,
            "failed": 0,
            "would_create": 0,
        }
        self.stdout.write(f"Scanne {total} Dokumente …")

        for document in qs.iterator():
            try:
                if dry_run:
                    extraction = contracts.extract_contract_data(document)
                    if extraction is None:
                        counters["no_contract"] += 1
                        continue
                    counters["would_create"] += 1
                    self.stdout.write(
                        f"  Doc {document.id}: Vertrag erkannt "
                        f"({extraction.confidence}%, {extraction.signals})"
                    )
                    continue

                result = contracts.sync_contract_record(document)
            except Exception as exc:  # pragma: no cover - best-effort Backfill
                counters["failed"] += 1
                self.stderr.write(f"  Doc {document.id} FEHLER: {exc}")
                continue

            status = result.get("status", "failed")
            counters[status] = counters.get(status, 0) + 1
            if status != "no_contract":
                self.stdout.write(
                    f"  Doc {document.id}: {status} "
                    f"({result.get('confidence', 0)}%, Review={result.get('needs_review')})"
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Fertig: "
                + ", ".join(f"{name}={value}" for name, value in counters.items())
                + (" (dry-run)" if dry_run else "")
            )
        )
