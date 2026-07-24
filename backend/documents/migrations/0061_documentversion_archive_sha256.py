from django.db import migrations, models


class Migration(migrations.Migration):
    """Additiv: archive_sha256 am DocumentVersion (Integritäts-Hash des Archiv-PDFs).

    Wird beim Ablegen des Archivs (pipeline._place_archive_at_storage_path) gesetzt.
    Alt-Versionen bleiben leer; der Restore-Drill fällt dort auf Größe/PDF-Magic
    zurück. PVC-/WORM-neutral (Feld ist blank, kein Backfill nötig)."""

    dependencies = [
        ("documents", "0060_repair_folder_node_owner_consistency"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="archive_sha256",
            field=models.CharField(
                blank=True,
                max_length=64,
                help_text=(
                    "Integritäts-Hash des Archiv-PDFs (beim Ablegen gesetzt). Leer "
                    "bei Alt-Versionen ohne hinterlegten Archivhash."
                ),
            ),
        ),
    ]
