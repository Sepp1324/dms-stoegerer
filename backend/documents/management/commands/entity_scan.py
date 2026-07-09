"""Backfill für das private DMS-Gedächtnis.

Beispiele:

    python manage.py entity_scan
    python manage.py entity_scan --all
    python manage.py entity_scan --dry-run --limit 20
"""
from django.core.management.base import BaseCommand

from documents.models import Document, DocumentVersion
from documents.services import entity_graph


class Command(BaseCommand):
    help = "Scannt Dokumente nach Entitäten/Identifiern und baut den Beziehungsgraphen."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Auch Dokumente mit bestehenden Entitätslinks erneut scannen.",
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
            help="Nur erkannte Treffer ausgeben, keine Datenbankänderung.",
        )

    def handle(self, *args, **options):
        qs = Document.objects.select_related(
            "current_version",
            "correspondent",
            "owner",
        ).exclude(current_version__isnull=True)
        if not options["include_unready"]:
            qs = qs.filter(
                current_version__processing_state=DocumentVersion.ProcessingState.READY
            )
        if not options["all"]:
            qs = qs.filter(entity_links__isnull=True)
        qs = qs.order_by("id").distinct()
        limit = max(0, int(options["limit"] or 0))
        if limit:
            qs = qs[:limit]

        dry_run = options["dry_run"]
        scanned = entities = links = relations = failed = 0
        self.stdout.write(f"Scanne {qs.count()} Dokumente …")

        for document in qs.iterator():
            scanned += 1
            try:
                if dry_run:
                    hits = entity_graph.extract_entity_hits(document)
                    entities += len(
                        {
                            (
                                hit.kind,
                                entity_graph.canonicalize(
                                    hit.kind, hit.identifier_value or hit.name
                                ),
                            )
                            for hit in hits
                        }
                    )
                    links += len(hits)
                    if hits:
                        self.stdout.write(f"  Doc {document.id}: {len(hits)} Treffer")
                    continue

                result = entity_graph.sync_document_entities(document)
            except Exception as exc:  # pragma: no cover - abhängig vom Dokumenttext
                failed += 1
                self.stderr.write(f"  Doc {document.id} FEHLER: {exc}")
                continue

            entities += int(result.get("entities", 0))
            links += int(result.get("links", 0))
            relations += int(result.get("relations", 0))
            if result.get("links", 0):
                self.stdout.write(
                    f"  Doc {document.id}: entities={result.get('entities', 0)}, "
                    f"links={result.get('links', 0)}, relations={result.get('relations', 0)}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: scanned={scanned}, entities={entities}, links={links}, "
                f"relations={relations}, failed={failed}"
                + (" (dry-run)" if dry_run else "")
            )
        )
