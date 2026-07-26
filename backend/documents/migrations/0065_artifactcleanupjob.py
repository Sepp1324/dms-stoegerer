from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0064_disable_re2_invalid_regex_rules"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArtifactCleanupJob",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("paths", models.JSONField(default=list)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Artefakt-Cleanup-Auftrag",
                "verbose_name_plural": "Artefakt-Cleanup-Aufträge",
                "ordering": ["created_at"],
            },
        ),
    ]
