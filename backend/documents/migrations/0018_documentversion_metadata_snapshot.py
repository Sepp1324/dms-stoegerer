"""Versionsvergleich Stufe 2 – additive Metadaten-Snapshot-Felder (STOAA-312).

Rein additiv auf ``DocumentVersion``. Der Snapshot der Metadaten/Tags/Custom-
Fields wird beim Sealing geschrieben (Option A, freigegeben in STOAA-292); die
Bestandsspalten bleiben unverändert. Kein Daten-Backfill in der Migration – das
idempotente Backfill der jeweils aktuellen Version übernimmt der Management-Befehl
``backfill_version_snapshots`` (bewusst nur aktuelle Version, GoBD).
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
                help_text="Eingefrorener Metadaten-/Tag-/Custom-Field-Stand beim Sealing",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="documentversion",
            name="snapshot_schema_version",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Schema-Version des metadata_snapshot (0 = nicht vorhanden)",
            ),
        ),
        migrations.AddField(
            model_name="documentversion",
            name="snapshot_taken_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Erfassungszeitpunkt des Snapshots (Sealing bzw. Backfill)",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="documentversion",
            name="seal_hash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="sha256(sha256 · prev_hash · Snapshot-Bytes) – Metadaten-Siegel",
                max_length=64,
            ),
        ),
    ]
