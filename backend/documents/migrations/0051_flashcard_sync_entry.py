from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def copy_flashcards_sync_forward(apps, schema_editor):
    """Bestehende DocumentVersion.flashcards_sync-Einträge (#294) in das neue,
    veränderliche FlashcardSyncEntry-Modell übernehmen – ohne bereits gepushte
    Karten erneut senden zu müssen. Reines Umschichten transienter Sync-Daten.
    """
    DocumentVersion = apps.get_model("documents", "DocumentVersion")
    FlashcardSyncEntry = apps.get_model("documents", "FlashcardSyncEntry")

    for ver in DocumentVersion.objects.exclude(flashcards_sync=[]).iterator():
        cards = ver.flashcards_sync or []
        for i, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            FlashcardSyncEntry.objects.get_or_create(
                idempotency_key=f"dms-v{ver.id}-c{i}",
                defaults={
                    "version_id": ver.id,
                    "ordinal": i,
                    "payload": {
                        "frage": card.get("frage"),
                        "aussagen": card.get("aussagen"),
                        "kap": card.get("kap"),
                    },
                    "state": "pushed" if card.get("pushed") else "pending",
                },
            )


class Migration(migrations.Migration):
    """Additiv + Umzug: Pro-Karte-Sync-Zustand in ein eigenes, veränderliches
    Modell (kein DocumentVersion.save() mehr -> funktioniert auch für versiegelte
    Versionen). Das alte JSONField wird danach entfernt.
    """

    dependencies = [
        ("documents", "0050_documentversion_flashcards_sync"),
    ]

    operations = [
        migrations.CreateModel(
            name="FlashcardSyncEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ordinal", models.PositiveIntegerField(help_text="Stabile Karten-Nr. innerhalb der Version (Basis des Idempotency-Keys)")),
                ("idempotency_key", models.CharField(help_text="Stabiler Schlüssel dms-v<versionId>-c<ordinal> – an psychosr übertragen", max_length=80, unique=True)),
                ("payload", models.JSONField(help_text="Kartendaten: {frage, aussagen, kap}")),
                ("state", models.CharField(choices=[("pending", "Ausstehend"), ("in_progress", "Wird gesendet"), ("pushed", "Gesendet")], db_index=True, default="pending", max_length=16)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("pushed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("version", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="flashcard_entries", to="documents.documentversion")),
            ],
            options={
                "verbose_name": "Flashcard-Sync-Eintrag",
                "verbose_name_plural": "Flashcard-Sync-Einträge",
                "ordering": ["version_id", "ordinal"],
                "unique_together": {("version", "ordinal")},
            },
        ),
        migrations.RunPython(copy_flashcards_sync_forward, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="documentversion",
            name="flashcards_sync",
        ),
    ]
