"""Embedding-Dimension 1024 -> 384 (Umstieg e5-large -> MiniLM-L12-v2).

e5-large (1024-dim) lud auf dem Cluster selbst mit 8Gi nicht (onnxruntime-
Graph-Optimierungs-Spike, OOMKill). Umstieg auf das kleine mehrsprachige
paraphrase-multilingual-MiniLM-L12-v2 (384-dim).

Bestehende 1024-dim-Vektoren lassen sich nicht auf 384 casten und stammen ohnehin
vom alten Modell -> vor dem Typwechsel alle Chunks löschen (der Index ist aktuell
leer; danach wird per ``reindex_embeddings --all`` mit dem neuen Modell neu
eingebettet). Kein Vektor-Index auf der Spalte -> nichts nachzuziehen.
"""
import pgvector.django
from django.db import migrations


def _clear_chunks(apps, schema_editor):
    apps.get_model("documents", "DocumentChunk").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0046_backfill_search_vector"),
    ]

    operations = [
        migrations.RunPython(_clear_chunks, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="documentchunk",
            name="embedding",
            field=pgvector.django.VectorField(
                blank=True, dimensions=384, null=True
            ),
        ),
    ]
