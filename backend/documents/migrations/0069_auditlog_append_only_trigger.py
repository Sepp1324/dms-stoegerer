from django.db import migrations

# DB-seitige Revisionssicherheit (P2): Die Modell-/Admin-Guards (save()/delete(),
# has_*_permission) verhindern Änderungen NUR über den ORM-Instanzpfad bzw. den
# Admin. ``QuerySet.update()``/``QuerySet.delete()``, Cascades und rohes SQL
# umgehen sie. Dieser Postgres-Trigger erzwingt append-only in der DB selbst:
#   * DELETE ist NIE erlaubt.
#   * UPDATE ist NUR erlaubt, wenn sich AUSSCHLIESSLICH ``actor_id`` ändert – das
#     deckt die Anonymisierung beim Löschen eines Users (FK on_delete=SET_NULL);
#     alle inhaltlichen Felder (timestamp/action/object_type/object_id/detail)
#     bleiben unveränderlich.
# Für maximale Sicherheit könnte zusätzlich eine separate DB-Rolle ohne
# UPDATE/DELETE auf die Tabelle vergeben werden (Betriebs-/Cluster-Entscheidung).

_FORWARD = r"""
CREATE OR REPLACE FUNCTION documents_auditlog_append_only()
RETURNS trigger AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        RAISE EXCEPTION 'AuditLogEntry ist append-only: DELETE ist nicht erlaubt (Revisionssicherheit).';
    END IF;
    IF (NEW.timestamp   IS DISTINCT FROM OLD.timestamp
        OR NEW.action      IS DISTINCT FROM OLD.action
        OR NEW.object_type IS DISTINCT FROM OLD.object_type
        OR NEW.object_id   IS DISTINCT FROM OLD.object_id
        OR NEW.detail      IS DISTINCT FROM OLD.detail) THEN
        RAISE EXCEPTION 'AuditLogEntry ist append-only: Aenderung ist nicht erlaubt (Revisionssicherheit).';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS documents_auditlog_append_only_trg ON documents_auditlogentry;
CREATE TRIGGER documents_auditlog_append_only_trg
    BEFORE UPDATE OR DELETE ON documents_auditlogentry
    FOR EACH ROW EXECUTE FUNCTION documents_auditlog_append_only();
"""

_REVERSE = r"""
DROP TRIGGER IF EXISTS documents_auditlog_append_only_trg ON documents_auditlogentry;
DROP FUNCTION IF EXISTS documents_auditlog_append_only();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0068_documentversion_postprocessed_at"),
    ]

    operations = [
        migrations.RunSQL(sql=_FORWARD, reverse_sql=_REVERSE),
    ]
