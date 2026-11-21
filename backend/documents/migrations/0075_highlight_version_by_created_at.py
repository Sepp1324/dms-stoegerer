from django.db import migrations


def reconstruct_version(apps, schema_editor):
    """Markierungen an die zur Anlagezeit AKTUELLE Version binden.

    0074 band jede (alte) Markierung pauschal an ``current_version`` – wurde nach dem
    Anlegen aber schon eine neue Version hochgeladen, passte Seite/Geometrie nicht mehr
    zum PDF. Hier wird stattdessen die Version rekonstruiert, die beim Anlegen der
    Markierung aktuell war: die Version desselben Dokuments mit dem größten
    ``created_at <= highlight.created_at`` (Fallback: die älteste Version).

    Konservativ: Es werden nur Markierungen umgehängt, deren rekonstruierte Version
    von der aktuellen Bindung abweicht.
    """
    DocumentHighlight = apps.get_model("documents", "DocumentHighlight")
    DocumentVersion = apps.get_model("documents", "DocumentVersion")
    for hl in DocumentHighlight.objects.all():
        best = (
            DocumentVersion.objects.filter(
                document_id=hl.document_id, created_at__lte=hl.created_at
            )
            .order_by("-created_at")
            .first()
        )
        if best is None:
            best = (
                DocumentVersion.objects.filter(document_id=hl.document_id)
                .order_by("created_at")
                .first()
            )
        if best is not None and hl.version_id != best.id:
            hl.version_id = best.id
            hl.save(update_fields=["version"])


class Migration(migrations.Migration):
    """Korrigiert die grobe 0074-Bindung (current_version) auf die Version, die beim
    Anlegen der Markierung aktuell war (Review-Fix). Reine Datenmigration."""

    dependencies = [
        ("documents", "0074_backfill_highlight_version"),
    ]

    operations = [
        migrations.RunPython(reconstruct_version, migrations.RunPython.noop),
    ]
