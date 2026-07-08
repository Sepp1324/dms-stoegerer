from django.core.management.base import BaseCommand

from documents.models import Document
from documents.services import case_matching


class Command(BaseCommand):
    help = "Erzeugt Akten-Autopilot-Vorschläge für bestehende Dokumente."

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
            Document.objects.select_related(
                "current_version",
                "correspondent",
                "document_type",
                "case_file",
            )
            .prefetch_related(
                "tags",
                "custom_field_values__field",
                "current_version__page_texts",
                "extraction_candidates",
                "case_file_candidates",
            )
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
            created_total += case_matching.generate_candidates(document)

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {created_total} Aktenvorschläge für {len(documents)} Dokumente erzeugt."
            )
        )
