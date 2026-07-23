from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    """Root-Ordner-Namen pro Owner eindeutig statt global.

    Der bisherige globale ``documents_folder_unique_root_name`` (unique(name)
    where parent IS NULL) blockierte, dass zwei Nutzer denselben Root-Ordnernamen
    (z. B. "Steuer") verwenden – und ließ die Kollision als IntegrityError/500
    statt als saubere Validierung enden. Zusammen mit dem Serializer-Guard
    (Unterordner nur unter eigenem Parent) bleibt jeder Teilbaum single-owner;
    daher ist die Eindeutigkeit korrekt pro Owner zu verankern.

    Rein schema-seitig (kein Datenzugriff): der neue Constraint ist STRICT LOOSER
    als der alte (owner kommt als Dimension hinzu), es können also keine neuen
    Verletzungen bestehender Daten entstehen. PVC-/WORM-neutral.
    """

    dependencies = [
        ("documents", "0056_documentversion_indexed_at"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="documentfolder",
            name="documents_folder_unique_root_name",
        ),
        migrations.AddConstraint(
            model_name="documentfolder",
            constraint=models.UniqueConstraint(
                fields=["owner", "name"],
                condition=Q(parent__isnull=True),
                name="documents_folder_unique_root_name_per_owner",
            ),
        ),
    ]
