"""STOAA-513: help_text von ``DocumentVersion.ingest_source`` um den neuen Wert
``mobile`` (Mobile-Erfassung) ergänzen.

Rein kosmetisch – ``help_text`` erzeugt auf Postgres keine DDL (kein
Schema-/Spalten-Change). Django erkennt die ``help_text``-Änderung dennoch im
Autodetector, daher ist dieses ``AlterField`` nötig, damit
``makemigrations --check`` grün bleibt.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0020_documentreminder"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documentversion",
            name="ingest_source",
            field=models.CharField(
                blank=True,
                default="upload",
                help_text="upload | consume | mail | api | paperless_import | mobile",
                max_length=16,
            ),
        ),
    ]
