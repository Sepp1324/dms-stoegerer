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


def push_flashcard(question: dict, *, source_title: str, idempotency_key: str) -> None:
    """Pusht **eine** geprüfte MC-Karte an psychosr; wirft bei Fehler.

    Der Aufrufer (``tasks.push_document_flashcards``) verwaltet den Pro-Karte-
    Zustand durabel in ``FlashcardSyncEntry`` und markiert die Karte erst NACH
    dem erfolgreichen Rückkehren dieser Funktion als ``pushed``. Deshalb genau
    eine Karte pro Aufruf: so ist jeder Erfolg sofort einzeln persistierbar.

    ``idempotency_key`` (stabil pro Version+Karte) wird als ``ext_id`` mitgesendet.

    WICHTIG – Zustellsemantik: Der DMS-seitige atomare Claim + der pro Karte
    durable ``pushed``-Status verhindern Dubletten durch parallele Tasks und
    normale Retries. Das verbleibende Fenster „POST erfolgreich, aber Crash vor
    dem DB-Commit" ist **nur dann** dublettenfrei, wenn **psychosr** den ``ext_id``
    tatsächlich auswertet (Spalte + Unique-Constraint + Insert-or-return-existing).
    Solange psychosr das NICHT tut, ist die Zustellung *at-least-once*: eine spätere
    Reklamation einer verwaisten Karte kann in diesem schmalen Fenster eine Dublette
    erzeugen. Der ``ext_id`` wird bereits vorwärtskompatibel gesendet; die server-
    seitige Dedup ist in psychosr separat umzusetzen.

    ``question`` muss bereits valide sein (4 Aussagen, ≥1 richtig, kap 1..8) –
    siehe :func:`ai.flashcards.parse_and_validate`.

    Wirft, wenn psychosr nicht konfiguriert ist oder der POST fehlschlägt
    (``httpx``-Fehler / non-2xx). ``SoftTimeLimitExceeded`` propagiert unangetastet.
    """
    if not is_configured():
        raise RuntimeError("psychosr nicht konfiguriert")

    base = settings.PSYCHOSR_URL.rstrip("/")
    deck = getattr(settings, "PSYCHOSR_DECK", "mc")
    titel = f"DMS: {source_title}"[:120]
    headers = {"X-Token": settings.PSYCHOSR_TOKEN}
    body = {
        "frage": question["frage"],
        "aussagen": question["aussagen"],
        "kap": question["kap"],
        "titel": titel,
        "deck": deck,
        "ext_id": idempotency_key,  # stabiler Idempotency-Key -> serverseitige Dedup
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{base}/api/mc/add", json=body, headers=headers)
        resp.raise_for_status()
