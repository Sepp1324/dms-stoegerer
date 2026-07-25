"""ReDoS-sichere Auswertung nutzerdefinierter Regexe (P1).

Regel- und Workflow-Trigger erlauben frei definierbare reguläre Ausdrücke gegen
den vollständigen OCR-Text. Mit dem backtracking-basierten ``re`` kann ein
Schreibberechtigter darüber ein Muster mit katastrophalem Backtracking
speichern (``(a+)+$`` o. Ä.) und damit API-Simulationen oder Celery-Worker
blockieren – ``re.search`` lässt sich in Python nicht per Timeout unterbrechen
(C-Level-Aufruf).

Deshalb laufen ALLE nutzerdefinierten Muster über RE2 (google-re2): eine
Automaten-Engine mit **garantiert linearer Laufzeit** ohne Backtracking – ein
ReDoS ist damit strukturell ausgeschlossen. Der GESAMTE Text wird durchsucht:
Weil RE2 linear in der Textlänge ist, ist das gefahrlos – eine künstliche
Deckelung würde nur dazu führen, dass Regeln bei langen Dokumenten still nicht
mehr treffen.

RE2 unterstützt bewusst KEINE Backreferences/Lookaround; solche Muster werden
beim Speichern abgelehnt (``compile_user_regex`` -> ``InvalidRegex``) und zur
Laufzeit geschlossen als „kein Treffer" behandelt (nie ein Crash).
"""
from __future__ import annotations

import logging

import re2

logger = logging.getLogger(__name__)


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
    """True, wenn das (case-insensitive) RE2-Muster im GESAMTEN Text matcht.

    Der vollständige Text wird durchsucht (RE2 ist linear -> gefahrlos); eine
    Deckelung würde Regeln bei langen Dokumenten still nicht mehr treffen lassen.

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
    return compiled.search(text or "") is not None
