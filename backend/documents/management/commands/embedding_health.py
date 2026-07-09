from django.core.management.base import BaseCommand

from documents.services import semantic_index


class Command(BaseCommand):
    help = "Zeigt den Status des semantischen Dokumentindex."

    def handle(self, *args, **options):
        health = semantic_index.embedding_health()
        self.stdout.write(f"Modell:             {health['model']}")
        self.stdout.write(f"Dimensionen:        {health['dimension']}")
        self.stdout.write(f"Dokumente:          {health['documents']}")
        self.stdout.write(f"Indexiert:          {health['indexed_documents']}")
        self.stdout.write(f"Fehlend:            {health['missing_documents']}")
        self.stdout.write(f"Chunks:             {health['chunks']}")
