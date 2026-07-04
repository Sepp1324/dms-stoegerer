"""Default-Owner-Auflösung und Ingest-Owner-Audit (STOAA-295).

Zentralisiert die Logik, mit der Mail- und Consume-Ingest einen konfigurierten
Standard-Eigentümer (``MAIL_DEFAULT_OWNER`` / ``CONSUME_DEFAULT_OWNER``)
auflösen. Ziel: ``owner=None`` ist nur noch ein **expliziter, admin-sichtbarer
Triage-Zustand** – kein Ingest-Pfad macht ein Dokument für den vorgesehenen
Nutzer still (durch die Owner-Isolation STOAA-7) unsichtbar.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


def resolve_default_owner(username: str | None):
    """Löst einen konfigurierten Default-Owner-Username zu einem User auf.

    Leerer/whitespace Wert -> ``None`` (bewusste Triage, ohne Log). Ein
    gesetzter, aber unbekannter Username -> ``None`` + Warn-Log: eine
    Fehlkonfiguration darf den Ingest nicht abbrechen; das Dokument landet dann
    im Admin-Triage-Zustand statt owner-los zu verschwinden.
    """
    username = (username or "").strip()
    if not username:
        return None
    user = get_user_model().objects.filter(username__iexact=username).first()
    if user is None:
        logger.warning(
            "Default-Owner '%s' ist konfiguriert, existiert aber nicht – "
            "Dokument bleibt eigentümerlos (Triage).",
            username,
        )
    return user


def log_ingest_owner_audit(document, *, owner, fallback_used, source, reason=""):
    """Protokolliert die Owner-Herkunft eines eingespeisten Dokuments.

    - ``owner_fallback``: der direkte Owner (Konto-/Ordner-Owner) war leer, ein
      konfigurierter Default-Owner (``*_DEFAULT_OWNER``) hat gegriffen.
      ``actor`` = gewählter Owner.
    - ``triage_ingest``: kein Owner ermittelbar -> bewusster, admin-sichtbarer
      Triage-Zustand statt eines stillen ``owner=None`` ohne Spur.

    Der reguläre ``upload``-Eintrag aus ``create_document_from_file`` bleibt
    unberührt; dieser Eintrag ergänzt die Owner-Semantik. Ist ein direkter Owner
    vorhanden (``fallback_used=False`` und ``owner`` gesetzt, z. B. Konto-Owner
    oder Consume-Per-User), wird nichts zusätzlich protokolliert.
    """
    from .models import AuditLogEntry

    if fallback_used and owner is not None:
        AuditLogEntry.objects.create(
            actor=owner,
            action="owner_fallback",
            object_type="Document",
            object_id=str(document.id),
            detail={
                "source": source,
                "chosen_owner": owner.get_username(),
                "reason": reason or "kein_direkter_owner",
            },
        )
    elif owner is None:
        AuditLogEntry.objects.create(
            actor=None,
            action="triage_ingest",
            object_type="Document",
            object_id=str(document.id),
            detail={"source": source},
        )
