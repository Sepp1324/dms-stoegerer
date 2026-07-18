import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations


class Migration(migrations.Migration):
    """Materialisierter Volltext-Suchvektor + GIN-Index (Perf, additiv/PVC-sicher).

    Das Feld ist zunächst NULL; ``manage.py backfill_search_vectors`` füllt den
    Bestand, Signale + Pipeline-Hook pflegen ihn danach automatisch.
    """

    dependencies = [
        ("documents", "0044_document_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(
                editable=False, null=True
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="documents_search_vector_gin"
            ),
        ),
    ]
