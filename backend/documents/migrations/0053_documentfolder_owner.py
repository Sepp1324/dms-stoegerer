from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Additiv: Eigentümer am Ordner (Sicherheits-Anker der Ordnerfreigabe).

    Bestehende Ordner bleiben ownerlos (null) – ihre etwaige Freigabe wird damit
    wirkungslos (exponiert keine fremden Dokumente mehr) und ist nur noch von
    Admins schaltbar. Dokument-Freigaben (Document.shared_with_household) sind
    unberührt.
    """

    dependencies = [
        ("documents", "0052_flashcardsyncentry_last_error"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="documentfolder",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_folders",
                to=settings.AUTH_USER_MODEL,
                help_text="Eigentümer – nur er (oder Admin) darf die Freigabe umschalten.",
            ),
        ),
    ]
