from django.db import migrations, models


class Migration(migrations.Migration):
    """Additiv: indexed_at markiert erfolgreiche Pflicht-Findbarkeitsindizierung
    (Suchvektor + semantischer Index) einer READY-Version. NULL an READY = noch
    nicht/erfolglos indexiert -> Reconciler holt nach. Bestehende READY-Versionen
    starten mit NULL und werden vom Reconciler nachindexiert (gebatcht)."""

    dependencies = [
        ("documents", "0055_workflow_rule_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="indexed_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
