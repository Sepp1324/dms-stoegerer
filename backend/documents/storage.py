"""Datei-Ablage auf der Platte.

Zwei Bereiche unterhalb von ``DMS_DATA_DIR`` (siehe Settings):

* ``originals/``  – die hochgeladenen Originaldateien (unverändert)
* ``archive/``    – die OCR'ten PDF/A nach Schema
                    ``archive/{jahr}/{korrespondent}/{titel}.pdf``
* ``consume/``    – Eingangsordner (z. B. vom Scanner beschickt)
"""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

DATA_DIR = Path(settings.DMS_DATA_DIR)
ORIGINALS_DIR = DATA_DIR / "originals"
ARCHIVE_DIR = DATA_DIR / "archive"
# Consume-Pfad aus den Settings (per Env ``CONSUME_FOLDER_PATH`` übersteuerbar);
# Default identisch zu ``DATA_DIR/consume`` → kein Verhaltenswechsel ohne Env.
CONSUME_DIR = Path(getattr(settings, "CONSUME_DIR", DATA_DIR / "consume"))

DEFAULT_TEMPLATE = "archive/{jahr}/{korrespondent}/{titel}"


def save_upload(uploaded_file) -> tuple[str, int, str]:
    """Schreibt einen hochgeladenen Datenstrom nach ``originals/``.

    Rückgabe: (Pfad, Größe in Bytes, MIME-Typ).
    Der Dateiname wird randomisiert, um Kollisionen und Pfad-Injektion zu vermeiden;
    der ursprüngliche Name lebt als Dokumenttitel weiter.
    """
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(uploaded_file.name).suffix.lower()
    dest = ORIGINALS_DIR / f"{uuid.uuid4().hex}{ext}"
    with open(dest, "wb") as fh:
        for chunk in uploaded_file.chunks():
            fh.write(chunk)

    mime = (
        getattr(uploaded_file, "content_type", None)
        or mimetypes.guess_type(uploaded_file.name)[0]
        or "application/octet-stream"
    )
    return str(dest), dest.stat().st_size, mime


def save_bytes(data: bytes, ext: str = "") -> Path:
    """Schreibt Roh-Bytes (z. B. einen E-Mail-Anhang) nach ``originals/``.

    Wie ``save_upload``, aber für bereits im Speicher liegende Bytes. Der
    Dateiname wird randomisiert (Kollisions-/Injektionsschutz).
    """
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    ext = ext.lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    dest = ORIGINALS_DIR / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(data)
    return dest


def build_archive_path(document) -> Path:
    """Bildet den Ziel-Pfad des Archiv-PDFs aus dem Ablage-Template.

    Platzhalter: ``{jahr}`` (aus Dokumentdatum, sonst Aufnahmedatum),
    ``{korrespondent}`` und ``{titel}`` (jeweils slugifiziert). Kollisionen
    werden durch ein numerisches Suffix aufgelöst.
    """
    template = DEFAULT_TEMPLATE
    if document.storage_path and document.storage_path.path_template:
        template = document.storage_path.path_template

    reference_date = document.created_at or document.added_at or timezone.now()
    jahr = reference_date.year
    korrespondent = (
        slugify(document.correspondent.name) if document.correspondent else "unbekannt"
    ) or "unbekannt"
    titel = slugify(document.title) or "dokument"

    relative = template.format(jahr=jahr, korrespondent=korrespondent, titel=titel)
    candidate = DATA_DIR / f"{relative}.pdf"
    candidate.parent.mkdir(parents=True, exist_ok=True)

    # Kollisionen auflösen: dokument.pdf → dokument-1.pdf → …
    counter = 1
    stem = candidate.stem
    while candidate.exists():
        candidate = candidate.with_name(f"{stem}-{counter}.pdf")
        counter += 1
    return candidate
