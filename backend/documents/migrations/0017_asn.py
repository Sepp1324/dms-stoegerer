"""ASN (Archive Serial Number) – Datenmodell + lückenlose Bestandsvergabe.

Fügt ``Document.asn`` (unveränderlich, unique), den Vergabe-Zähler ``ASNCounter``
und die Import-Historie ``ASNScan`` hinzu. Bestehende Dokumente erhalten in einem
Daten-Schritt fortlaufend eine ASN (nach Aufnahmedatum), der Zähler wird auf den
höchsten vergebenen Wert gesetzt. Erst danach wird die Spalte auf NOT NULL/unique
gehoben (STOAA-284/285).
"""
from django.db import migrations, models
import django.db.models.deletion


def assign_initial_asns(apps, schema_editor):
    """Vergibt bestehenden Dokumenten fortlaufend eine ASN und initialisiert den Zähler."""
    Document = apps.get_model("documents", "Document")
    ASNCounter = apps.get_model("documents", "ASNCounter")

    value = 0
    for document in Document.objects.order_by("added_at", "id").iterator():
        value += 1
        document.asn = value
        document.save(update_fields=["asn"])

    ASNCounter.objects.update_or_create(pk=1, defaults={"last_value": value})


def noop_reverse(apps, schema_editor):
    """Rückwärts: der Zähler/das Feld verschwinden mit den Reverse-Schema-Ops."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0016_workflow_engine"),
    ]

    operations = [
        migrations.CreateModel(
            name="ASNCounter",
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
                (
                    "last_value",
                    models.PositiveBigIntegerField(
                        default=0,
                        help_text="Zuletzt vergebene ASN. Die nächste Vergabe liefert last_value + 1.",
                    ),
                ),
            ],
            options={
                "verbose_name": "ASN-Zähler",
                "verbose_name_plural": "ASN-Zähler",
            },
        ),
        # 1) Spalte zunächst nullable und ohne Constraint anlegen (Bestandsdaten
        #    haben noch keine ASN).
        migrations.AddField(
            model_name="document",
            name="asn",
            field=models.PositiveBigIntegerField(editable=False, null=True),
        ),
        # 2) Bestandsdokumente durchnummerieren + Zähler setzen.
        migrations.RunPython(assign_initial_asns, noop_reverse),
        # 3) Constraint scharf schalten: unveränderlich, indiziert, unique, NOT NULL.
        migrations.AlterField(
            model_name="document",
            name="asn",
            field=models.PositiveBigIntegerField(
                db_index=True, editable=False, unique=True
            ),
        ),
        migrations.CreateModel(
            name="ASNScan",
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
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="asn_scans",
                        to="documents.document",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="asn_scans",
                        to="documents.documentversion",
                    ),
                ),
                ("scanned_at", models.DateTimeField(auto_now_add=True)),
                (
                    "matched_by",
                    models.CharField(help_text="z. B. OCR, QR, Barcode", max_length=64),
                ),
                (
                    "confidence",
                    models.FloatField(
                        default=1.0, help_text="OCR-Erkennungswahrscheinlichkeit"
                    ),
                ),
            ],
            options={
                "verbose_name": "ASN-Scan",
                "verbose_name_plural": "ASN-Scans",
                "ordering": ["-scanned_at"],
            },
        ),
    ]
