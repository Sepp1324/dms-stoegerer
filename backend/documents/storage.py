"""Datei-Ablage auf der Platte.

Zwei Bereiche unterhalb von ``DMS_DATA_DIR`` (siehe Settings):

* ``originals/``  – die hochgeladenen Originaldateien (unverändert)
* ``archive/``    – die OCR'ten PDF/A nach Schema
                    ``archive/{jahr}/{korrespondent}/{titel}.pdf``
* ``consume/``    – Eingangsordner (z. B. vom Scanner beschickt)
"""
from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from .filetypes import SNIFF_BYTES, UnsupportedFileType, detect


def _max_upload_bytes() -> int:
    """Obergrenze pro Upload in Bytes (P0-2/P1-DoS). Env: UPLOAD_MAX_FILE_MB."""
    return int(getattr(settings, "UPLOAD_MAX_FILE_MB", 200)) * 1024 * 1024


def _sniff_or_reject(header: bytes):
    """Erkennt den Typ am Byte-Header oder wirft ``UnsupportedFileType``."""
    info = detect(header)
    if info is None:
        raise UnsupportedFileType(
            "Dateityp nicht erlaubt. Zulässig sind PDF und gängige Bildformate "
            "(JPEG, PNG, GIF, TIFF, BMP, WebP, HEIC)."
        )
    return info

DATA_DIR = Path(settings.DMS_DATA_DIR)
ORIGINALS_DIR = DATA_DIR / "originals"
ARCHIVE_DIR = DATA_DIR / "archive"
# Eingangsordner: per Env (CONSUME_FOLDER_PATH) auf einen NFS-/NAS-Mount
# umlenkbar; Fallback ist das bisherige DMS_DATA_DIR/consume.
CONSUME_DIR = (
    Path(settings.CONSUME_FOLDER_PATH)
    if getattr(settings, "CONSUME_FOLDER_PATH", "")
    else DATA_DIR / "consume"
)

DEFAULT_TEMPLATE = "archive/{jahr}/{korrespondent}/{titel}"


def save_upload(uploaded_file) -> tuple[str, int, str]:
    """Schreibt einen hochgeladenen Datenstrom nach ``originals/``.

    Rückgabe: (Pfad, Größe in Bytes, MIME-Typ).
    Der Dateiname wird randomisiert, um Kollisionen und Pfad-Injektion zu vermeiden;
    der ursprüngliche Name lebt als Dokumenttitel weiter.

    Sicherheit (P0-2): Der Typ wird an den **Magic Bytes** erkannt, nicht am
    Client-``Content-Type`` oder an der Endung. Nur die Allowlist (PDF + Bilder)
    ist zulässig; alles andere – v. a. HTML/SVG – löst ``UnsupportedFileType``
    aus, bevor irgendetwas geschrieben wird. MIME und Endung stammen aus dem
    erkannten Typ, nicht aus Nutzereingaben. Zusätzlich greift ein Größenlimit.
    """
    max_bytes = _max_upload_bytes()
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > max_bytes:
        raise UnsupportedFileType(
            f"Datei zu groß ({size} Bytes > Limit {max_bytes} Bytes)."
        )

    # Header VOR dem Schreiben prüfen (fail closed), dann Strom zurückspulen.
    header = uploaded_file.read(SNIFF_BYTES)
    info = _sniff_or_reject(header)
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        pass  # Strom nicht spulbar – Header wird unten vorangestellt.

    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ORIGINALS_DIR / f"{uuid.uuid4().hex}{info.ext}"
    written = 0
    with open(dest, "wb") as fh:
        if uploaded_file.tell() != 0:  # nicht spulbar → Header selbst schreiben
            fh.write(header)
            written += len(header)
        for chunk in uploaded_file.chunks():
            written += len(chunk)
            if written > max_bytes:
                fh.close()
                dest.unlink(missing_ok=True)
                raise UnsupportedFileType(
                    f"Datei überschreitet das Limit ({max_bytes} Bytes)."
                )
            fh.write(chunk)

    return str(dest), dest.stat().st_size, info.mime


def save_bytes(data: bytes, ext: str = "") -> Path:
    """Schreibt Roh-Bytes (z. B. einen E-Mail-Anhang) nach ``originals/``.

    Wie ``save_upload``, aber für bereits im Speicher liegende Bytes. Der
    Dateiname wird randomisiert (Kollisions-/Injektionsschutz).

    Sicherheit (P0-2): Auch hier entscheidet die Magic-Byte-Allowlist – die
    übergebene ``ext`` ist nur ein Hinweis und wird durch die erkannte Endung
    ersetzt. Nicht erlaubte Typen lösen ``UnsupportedFileType`` aus.
    """
    if len(data) > _max_upload_bytes():
        raise UnsupportedFileType(
            f"Datei zu groß ({len(data)} Bytes > Limit {_max_upload_bytes()} Bytes)."
        )
    info = _sniff_or_reject(data[:SNIFF_BYTES])
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ORIGINALS_DIR / f"{uuid.uuid4().hex}{info.ext}"
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
