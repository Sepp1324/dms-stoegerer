"""Versionsvergleich Stufe 2 – Metadaten-Snapshot + Siegel-Verkettung (STOAA-315).

Rein additive Migration: zwei neue Felder auf ``DocumentVersion``
(``metadata_snapshot`` JSON, ``seal_hash`` CharField). Beide sind null/leer für
Bestand – der Backfill (current-only) läuft als separater Management-Command
NACH dem Deploy, nicht in der Migration. Kein Datenmigrations-Schritt, keine
Änderung an ``sha256``/``prev_hash`` (Datei-Kette bleibt unangetastet).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0017_asn"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="metadata_snapshot",
            field=models.JSONField(
                blank=True,
                null=True,
                help_text="Kanonischer Metadaten-Snapshot beim Sealing (null = nicht verfügbar)",
            ),
        ),
        migrations.AddField(
            model_name="documentversion",
            name="seal_hash",
            field=models.CharField(
                blank=True,
                max_length=64,
                help_text="sha256(sha256 | prev_hash | canonical_json(metadata_snapshot)) – Siegelkette",
            ),
        ),
    ]
