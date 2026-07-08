from django.core.management.base import BaseCommand

from documents.models import Document
from documents.services import extraction


class Command(BaseCommand):
    help = "Erzeugt Smart-Inbox-Extraktionsvorschläge für bestehende Dokumente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Alle Dokumente prüfen, nicht nur offene Review-Dokumente.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximale Anzahl Dokumente (0 = kein Limit).",
        )

    def handle(self, *args, **options):
        qs = (
            Document.objects.select_related("current_version")
            .prefetch_related("current_version__page_texts", "extraction_candidates")
            .exclude(current_version__isnull=True)
            .order_by("-added_at")
        )
        if not options["all"]:
            qs = qs.filter(review_status=Document.ReviewStatus.NEEDS_REVIEW)
        if options["limit"] > 0:
            qs = qs[: options["limit"]]

        documents = list(qs)
        created_total = 0
        for document in documents:
            created_total += extraction.generate_candidates(document)

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {created_total} Vorschläge für {len(documents)} Dokumente erzeugt."
            )
        )
