from django.db import migrations, models


class Migration(migrations.Migration):
    """Sticker-only-Modell: Document.asn wird optional (nullable).

    Zerstörungsfrei – bestehende ASNs bleiben erhalten. Neue Dokumente werden ohne
    ASN angelegt; die ASN kommt künftig nur noch aus einem erkannten Barcode/QR.
    """

    dependencies = [
        ("documents", "0037_savedview"),
    ]

    operations = [
        migrations.AlterField(
            model_name="document",
            name="asn",
            field=models.PositiveBigIntegerField(
                blank=True, db_index=True, editable=False, null=True, unique=True
            ),
        ),
    ]
