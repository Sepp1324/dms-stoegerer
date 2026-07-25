"""ReDoS-sichere Auswertung nutzerdefinierter Regexe (P1).

Regel- und Workflow-Trigger erlauben frei definierbare reguläre Ausdrücke gegen
den vollständigen OCR-Text. Mit dem backtracking-basierten ``re`` kann ein
Schreibberechtigter darüber ein Muster mit katastrophalem Backtracking
speichern (``(a+)+$`` o. Ä.) und damit API-Simulationen oder Celery-Worker
blockieren – ``re.search`` lässt sich in Python nicht per Timeout unterbrechen
(C-Level-Aufruf).

Deshalb laufen ALLE nutzerdefinierten Muster über RE2 (google-re2): eine
Automaten-Engine mit **garantiert linearer Laufzeit** ohne Backtracking – ein
ReDoS ist damit strukturell ausgeschlossen. Zusätzlich wird die Eingabelänge
gedeckelt (Defense-in-depth gegen sehr große OCR-Texte).

RE2 unterstützt bewusst KEINE Backreferences/Lookaround; solche Muster werden
beim Speichern abgelehnt (``compile_user_regex`` -> ``InvalidRegex``) und zur
Laufzeit geschlossen als „kein Treffer" behandelt (nie ein Crash).
"""
from __future__ import annotations

import logging

import re2

logger = logging.getLogger(__name__)

# Obergrenze für die Textlänge je Regex-Auswertung. RE2 ist linear, aber sehr
# große OCR-Texte (mehrere MB) sollen trotzdem nicht unnötig Zeit kosten.
MAX_TEXT_CHARS = 200_000


class InvalidRegex(ValueError):
    """Das Muster ist ungültig oder von RE2 nicht unterstützt."""


def compile_user_regex(pattern: str):
    """Kompiliert ein nutzerdefiniertes, case-insensitives Muster mit RE2.

    Wirft ``InvalidRegex`` bei ungültigen oder von RE2 nicht unterstützten
    Ausdrücken (z. B. Backreferences/Lookaround) – gedacht für die Validierung
    beim Speichern, damit solche Muster gar nicht erst persistiert werden.
    """
    try:
        return re2.compile(f"(?i){pattern}")
    except re2.error as exc:  # ungültig / von RE2 nicht unterstützt
        raise InvalidRegex(str(exc)) from exc


def search(pattern: str, text: str) -> bool:
    """True, wenn das (case-insensitive) RE2-Muster im gedeckelten Text matcht.

    Fällt bei ungültigem Muster **geschlossen** auf ``False`` zurück (kein Crash,
    kein ReDoS). So bleibt ein historisch gespeichertes, RE2-inkompatibles Muster
    folgenlos, statt die Verarbeitung zu sprengen.
    """
    if not pattern:
        return False
    try:
        compiled = compile_user_regex(pattern)
    except InvalidRegex:
        logger.warning("Ungültiges Regel-/Trigger-Regex ignoriert (RE2): %r", pattern)
        return False
    return compiled.search(text[:MAX_TEXT_CHARS]) is not None
