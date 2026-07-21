"""HTTP-Client zum SR-Trainer **psychosr**.

Schickt generierte MC-Lernkarten an ``POST /api/mc/add`` von psychosr. Die
Authentifizierung läuft über das Shared Secret (Header ``X-Token`` =
``PSYCHOSR_TOKEN``, entspricht dort ``POMODORO_TOKEN``).

Konfiguration (Settings/ENV):
* ``PSYCHOSR_URL``   – Basis-URL, z. B. ``https://psychosr.stoegerer-home.cloud``
* ``PSYCHOSR_TOKEN`` – Shared Secret
* ``PSYCHOSR_DECK``  – Ziel-Deck (Default ``mc``)
"""
from __future__ import annotations

import logging

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(getattr(settings, "PSYCHOSR_URL", "") and getattr(settings, "PSYCHOSR_TOKEN", ""))


def push_flashcards(questions: list[dict], *, source_title: str) -> dict:
    """Pusht geprüfte MC-Fragen an psychosr. Gibt ``{pushed, failed, errors}`` zurück.

    ``questions`` müssen bereits valide sein (4 Aussagen, ≥1 richtig, kap 1..8) –
    siehe :func:`ai.flashcards.parse_and_validate`.
    """
    if not is_configured():
        return {"pushed": 0, "failed": 0, "errors": ["psychosr nicht konfiguriert"], "skipped": True}

    base = settings.PSYCHOSR_URL.rstrip("/")
    deck = getattr(settings, "PSYCHOSR_DECK", "mc")
    titel = f"DMS: {source_title}"[:120]
    headers = {"X-Token": settings.PSYCHOSR_TOKEN}

    pushed = 0
    failed = 0
    errors: list[str] = []
    with httpx.Client(timeout=30) as client:
        for q in questions:
            body = {
                "frage": q["frage"],
                "aussagen": q["aussagen"],
                "kap": q["kap"],
                "titel": titel,
                "deck": deck,
            }
            try:
                resp = client.post(f"{base}/api/mc/add", json=body, headers=headers)
                resp.raise_for_status()
                pushed += 1
            except SoftTimeLimitExceeded:
                raise  # Soft-Time-Limit nie verschlucken (Task muss abbrechen)
            except Exception as exc:  # noqa: BLE001 – einzelne Karte scheitert, Rest weiter
                failed += 1
                errors.append(str(exc))
                logger.warning("psychosr /api/mc/add fehlgeschlagen: %s", exc)

    return {"pushed": pushed, "failed": failed, "errors": errors, "skipped": False}
