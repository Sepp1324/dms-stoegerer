from django.core.management.base import BaseCommand

from documents.models import Document, DocumentChunk
from documents.services import semantic_index


class Command(BaseCommand):
    help = "Erzeugt den semantischen Index fuer Dokumente neu oder fehlende nach."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Alle Dokumente neu indexieren.")
        parser.add_argument("--document-id", type=int, help="Nur ein bestimmtes Dokument indexieren.")
        parser.add_argument("--limit", type=int, default=0, help="Maximale Anzahl Dokumente.")
        parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, was passieren wuerde.")

    def handle(self, *args, **options):
        qs = (
            Document.objects.select_related(
                "current_version",
                "correspondent",
                "document_type",
                "folder",
                "case_file",
            )
            .prefetch_related("tags", "current_version__page_texts")
            .exclude(current_version__isnull=True)
            .order_by("id")
        )
        if options["document_id"]:
            qs = qs.filter(id=options["document_id"])

        docs = list(qs)
        if not options["all"]:
            indexed_ids = set(
                DocumentChunk.objects.filter(
                    document__in=docs,
                    version_id__in=[doc.current_version_id for doc in docs],
                ).values_list("document_id", flat=True)
            )
            docs = [doc for doc in docs if doc.id not in indexed_ids]

        if options["limit"] and options["limit"] > 0:
            docs = docs[: options["limit"]]

        self.stdout.write(
            f"Semantischer Index: {len(docs)} Dokumente "
            f"({'neu' if options['all'] else 'fehlend'})"
        )
        if options["dry_run"]:
            for doc in docs[:20]:
                self.stdout.write(f"- {doc.id}: {doc.title}")
            return

        created = 0
        empty = 0
        failed = 0
        for doc in docs:
            try:
                result = semantic_index.sync_document_embeddings(doc)
            except Exception as exc:  # noqa: BLE001 - Batch soll weiterlaufen
                failed += 1
                self.stderr.write(f"FEHLER {doc.id}: {exc}")
                continue
            created += int(result.get("created", 0))
            if result.get("status") == "empty":
                empty += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {created} Chunks erstellt, {empty} leer, {failed} Fehler."
            )
        )
