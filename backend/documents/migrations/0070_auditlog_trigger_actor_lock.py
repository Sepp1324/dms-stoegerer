from django.db import migrations

# Nachschärfung des append-only-Triggers (P1): Die erste Fassung (0069) ließ
# JEDE Änderung an ``actor_id`` zu, um die SET_NULL-Anonymisierung beim Löschen
# eines Users abzudecken. Damit blieb aber eine nachträgliche UMATTRIBUTION offen:
#   UPDATE documents_auditlogentry SET actor_id = <anderer_user> WHERE ...
# ließ sich ein Audit-Ereignis einem fremden Nutzer zuschreiben – für die
# Revisionssicherheit ein Loch. Diese Migration ersetzt die Trigger-Funktion so,
# dass sich ``actor_id`` NUR noch im Zuge der Anonymisierung ändern darf:
#   * erlaubt:  OLD.actor_id IS NOT NULL  ->  NEW.actor_id IS NULL   (SET_NULL)
#   * gesperrt: alles andere (X -> Y mit Y NOT NULL, NULL -> Y).
# DELETE bleibt wie bisher generell gesperrt; die inhaltlichen Felder ebenso.
# Der Trigger selbst (0069) bleibt unverändert – nur die Funktion wird ersetzt.

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
    -- actor_id darf sich NUR von einem gesetzten Wert nach NULL aendern
    -- (Anonymisierung via FK on_delete=SET_NULL). Jede Umattribution
    -- (auf einen anderen Nutzer) oder ein Setzen von NULL -> Wert ist gesperrt.
    IF (NEW.actor_id IS DISTINCT FROM OLD.actor_id)
       AND NOT (OLD.actor_id IS NOT NULL AND NEW.actor_id IS NULL) THEN
        RAISE EXCEPTION 'AuditLogEntry ist append-only: Akteur darf nur zu NULL anonymisiert, nicht umattributiert werden (Revisionssicherheit).';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Reverse: die 0069-Fassung wiederherstellen (actor_id frei änderbar).
_REVERSE = r"""
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
"""


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0069_auditlog_append_only_trigger"),
    ]

    operations = [
        migrations.RunSQL(sql=_FORWARD, reverse_sql=_REVERSE),
    ]
