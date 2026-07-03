# Bereinigt die verwaiste E-Mail-Metadaten-Duplizierung aus Stufe 4.
#
# Bei der Integration von STOAA-58 (PR #32) und STOAA-59 (PR #34) — beide
# dasselbe Feature "Regeln auf Mail-Betreff/Absender matchen" — hat die
# Merge-Auflösung die PR-#32-Variante (`mail_subject`/`mail_sender`, inkl.
# Model-Feldern, Wiring in classification.apply_rules und Tests) übernommen,
# aber zwei PR-#34-Artefakte verwaist zurückgelassen:
#   1. die Spalten `email_subject`/`email_from` (Migration 0007_document_email_
#      subject_document_email_from) ohne zugehörige Model-Felder, und
#   2. einen mail.py-Block, der `save(update_fields=["email_subject", ...])`
#      auf eben diese Nicht-Model-Felder aufruft -> ValueError bei jedem
#      Mail-Anhang (Ingestion defekt) und roter `makemigrations --check`.
# Diese Migration entfernt die ungenutzten Spalten wieder; der mail.py-Block
# wird im selben PR gelöscht. Danach gilt Model == Migrationen (single leaf).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0008_merge_stufe4_0007"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="document",
            name="email_subject",
        ),
        migrations.RemoveField(
            model_name="document",
            name="email_from",
        ),
    ]
