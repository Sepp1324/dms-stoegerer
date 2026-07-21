"""Subprozess-Ausführung mit hartem Timeout + Prozessgruppen-Kill.

``ocrmypdf``/``pdftotext`` starten Kindprozesse (z. B. tesseract). Ohne eigenes
Prozess-Timeout kann beim Celery-Hard-Limit nur der Worker sterben – die Enkel-
Prozesse laufen weiter und belegen CPU/RAM; ein Watchdog-Retry startete dann
ZUSÄTZLICHE OCR-Prozesse. ``start_new_session=True`` legt eine eigene
Prozessgruppe an, die bei Timeout via ``killpg(SIGKILL)`` vollständig beendet
wird. Das Timeout liegt (per Setting) unterhalb des Celery-Soft-Limits.
"""
from __future__ import annotations

import os
import signal
import subprocess


def _default_timeout() -> int:
    from django.conf import settings

    # Unter dem Celery-Soft-Limit (Default 1800 s) halten.
    return int(getattr(settings, "OCR_SUBPROCESS_TIMEOUT", 1200))


def run_group(cmd: list[str], *, timeout: int | None = None, capture: bool = False) -> bytes:
    """Führt ``cmd`` aus und killt bei Timeout die GESAMTE Prozessgruppe.

    ``capture=True`` liefert stdout als bytes. Nicht-Null-Exit -> ``CalledProcess
    Error``; Timeout -> ``TimeoutExpired`` (nach Gruppen-Kill).
    """
    to = timeout if timeout is not None else _default_timeout()
    stdout = subprocess.PIPE if capture else None
    with subprocess.Popen(cmd, stdout=stdout, start_new_session=True) as proc:
        try:
            out, _ = proc.communicate(timeout=to)
        except BaseException:
            # Timeout (TimeoutExpired) ODER externer Abbruch während communicate –
            # insbesondere Celerys SoftTimeLimitExceeded. In ALLEN Fällen die
            # GESAMTE Prozessgruppe hart beenden (sonst liefen tesseract-Kinder
            # weiter, und der Popen-Context-Manager würde beim __exit__ unbegrenzt
            # auf den Prozess warten), Zombie einsammeln, dann Ausnahme
            # unverändert weiterreichen.
            _kill_group(proc)
            raise
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=out)
        return out or b""


def _kill_group(proc: "subprocess.Popen") -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.communicate(timeout=5)  # Zombie einsammeln, aber nicht ewig hängen
    except Exception:
        pass
