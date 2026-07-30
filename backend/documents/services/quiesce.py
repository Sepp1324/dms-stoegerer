"""Backup-Schreibsperre (Quiesce, P1).

Damit ``pg_dump`` und der ``/data``-Tar EINEN konsistenten Zeitpunkt sehen, muss
während beider Sicherungsschritte kurz KEIN neues Dokument geschrieben werden.
Dieses Modul stellt dafür ein prozessübergreifendes Flag bereit.

Design:
* Das Flag liegt als EINZELNER Redis-Key ``dms:backup:quiesce`` – bewusst ohne
  Django-Cache-Präfix, damit die Backup-CronJob es direkt per ``redis-cli`` (oder
  dem Management-Command ``backup_quiesce``) setzen/löschen kann, ohne die
  Django-App im Backup-Pod laufen lassen zu müssen.
* Der Key trägt eine TTL als Notbremse: Stürzt der Backup-Job zwischen ``--on``
  und ``--off`` ab, hebt sich die Sperre nach spätestens ``ttl`` Sekunden von
  selbst auf – ein hängendes Backup wedged die Uploads NICHT dauerhaft.
* ``is_quiesced()`` failt OPEN: Ist Redis nicht erreichbar, wird NICHT gesperrt
  (dann läuft ohnehin kein koordiniertes Backup, und Schreibpfade sollen nicht an
  einem Redis-Ausfall scheitern).
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

# Bewusst UNPREFIXED (kein Django-Cache-KeyPrefix), s. Modul-Docstring.
QUIESCE_KEY = "dms:backup:quiesce"
# Default-TTL der Sperre (Sekunden) – Notbremse gegen ein hängendes Backup.
DEFAULT_QUIESCE_TTL = int(getattr(settings, "BACKUP_QUIESCE_TTL", 3600) or 3600)


class BackupQuiesceActive(APIException):
    """Wird geworfen, wenn während einer Backup-Schreibsperre geschrieben würde.

    Als ``APIException`` mit 503 kümmert sich der DRF-Default-Handler um eine
    saubere HTTP-Antwort; in Celery-Tasks bubblet sie als normale Exception."""

    status_code = 503
    default_detail = (
        "Wartungsfenster: Es läuft gerade eine Sicherung. Bitte in wenigen "
        "Minuten erneut versuchen."
    )
    default_code = "backup_quiesce_active"


def _client():
    """Direkter Redis-Client auf denselben Server wie Broker/Cache.

    Getrennt konfigurierbar über ``QUIESCE_REDIS_URL``; Fallback ist die
    Broker-/Result-URL (``REDIS_URL``)."""
    import redis  # lokal importiert – kein harter Import beim App-Start

    url = getattr(settings, "QUIESCE_REDIS_URL", "") or getattr(
        settings, "REDIS_URL", ""
    ) or getattr(settings, "CELERY_BROKER_URL", "")
    return redis.Redis.from_url(url)


def is_quiesced() -> bool:
    """True, wenn die Backup-Schreibsperre aktiv ist. Fail-open bei Redis-Fehler."""
    try:
        return bool(_client().exists(QUIESCE_KEY))
    except Exception:  # noqa: BLE001 – Redis weg: nicht sperren (fail-open)
        logger.warning("Quiesce-Flag nicht lesbar (Redis?) – Schreibsperre inaktiv.")
        return False


def set_quiesce(active: bool, ttl: int = DEFAULT_QUIESCE_TTL) -> None:
    """Setzt/löscht die Schreibsperre. ``ttl`` ist die Notbremse-Lebensdauer."""
    client = _client()
    if active:
        client.set(QUIESCE_KEY, "1", ex=max(1, int(ttl)))
        logger.info("Backup-Schreibsperre AKTIVIERT (TTL %ss).", ttl)
    else:
        client.delete(QUIESCE_KEY)
        logger.info("Backup-Schreibsperre AUFGEHOBEN.")


def raise_if_quiesced() -> None:
    """Backstop für Schreibpfade: wirft ``BackupQuiesceActive``, wenn gesperrt."""
    if is_quiesced():
        raise BackupQuiesceActive()
