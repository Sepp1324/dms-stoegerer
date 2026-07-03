# Merge-Migration: löst die parallel entstandenen 0007-Leaf-Nodes auf.
#
# Vier Stufe-4-Features wurden unabhängig auf Basis 0006 gemergt und erzeugten
# je eine 0007-Migration (E-Mail-Betreff/Absender, Mail-Betreff/Sender,
# Freigabe-Status, WORM-Retention). Django kann so nicht migrieren
# ("multiple leaf nodes"). Diese Merge-Migration vereint die vier Zweige ohne
# Schemaänderung (alle Feld-Adds sind disjunkt) und macht `migrate` wieder
# deterministisch. Entspricht `python manage.py makemigrations --merge`.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0007_document_email_subject_document_email_from"),
        ("documents", "0007_document_mail_subject_document_mail_sender"),
        ("documents", "0007_document_status"),
        ("documents", "0007_worm_retention"),
    ]

    operations = []
