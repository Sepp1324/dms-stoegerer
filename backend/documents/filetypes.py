"""Magic-Byte-basierte Dateityp-Erkennung mit enger Allowlist (P0-2 Upload-XSS).

Der vom Client gemeldete ``Content-Type`` und die Dateiendung sind **nicht
vertrauenswürdig** – ein Angreifer kann eine HTML-/SVG-Datei als ``image/png``
hochladen und sie so später als aktives Dokument im DMS-Origin zur Ausführung
bringen (Stored XSS, Diebstahl von Tokens aus ``localStorage``).

Dieses Modul erkennt den Typ **ausschließlich am Byte-Inhalt** (Signaturen/
Magic Bytes) und erlaubt nur Formate, die die Verarbeitungs-Pipeline auch
tatsächlich braucht: PDF und gängige Bildformate. Alles andere – insbesondere
HTML, SVG, XML und Skripte – wird abgewiesen (``detect`` liefert ``None``).

Verwendung:
    info = detect(header_bytes)
    if info is None:
        raise UnsupportedFileType(...)
    mime, ext = info.mime, info.ext
"""
from __future__ import annotations

from dataclasses import dataclass

# So viele Bytes reichen für alle unten geprüften Signaturen (HEIC/ftyp liegt
# am weitesten hinten). Aufrufer sollten mindestens so viele Bytes übergeben.
SNIFF_BYTES = 64


class UnsupportedFileType(ValueError):
    """Der Datei-Inhalt gehört keinem erlaubten (allowlisted) Format an."""


@dataclass(frozen=True)
class FileType:
    name: str
    mime: str
    ext: str  # inkl. führendem Punkt, z. B. ".pdf"


# Kanonische Typen der Allowlist. Der ``mime``/``ext`` hier ist maßgeblich –
# NICHT der vom Client gemeldete Wert.
PDF = FileType("PDF", "application/pdf", ".pdf")
JPEG = FileType("JPEG", "image/jpeg", ".jpg")
PNG = FileType("PNG", "image/png", ".png")
GIF = FileType("GIF", "image/gif", ".gif")
TIFF = FileType("TIFF", "image/tiff", ".tiff")
BMP = FileType("BMP", "image/bmp", ".bmp")
WEBP = FileType("WebP", "image/webp", ".webp")
HEIC = FileType("HEIF/HEIC", "image/heic", ".heic")

# HEIF-/HEIC-Markenkennungen (Bytes 8..12 im ``ftyp``-Box). Bewusst eng: nur
# echte Bild-Brands, keine Video-Container (z. B. ``mp4``/``qt``).
_HEIF_BRANDS = {
    b"heic",
    b"heix",
    b"heim",
    b"heis",
    b"hevc",
    b"hevx",
    b"mif1",
    b"msf1",
    b"heif",
}


def detect(header: bytes) -> FileType | None:
    """Erkennt den Dateityp am Byte-Header. ``None`` = nicht erlaubt.

    Wichtig: ausschließlich Byte-Signaturen; keine Endung, kein Client-MIME.
    """
    if not header:
        return None
    h = header

    # PDF
    if h[:5] == b"%PDF-":
        return PDF
    # JPEG (SOI + Marker)
    if h[:3] == b"\xff\xd8\xff":
        return JPEG
    # PNG
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    # GIF
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return GIF
    # TIFF (little-/big-endian)
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return TIFF
    # BMP
    if h[:2] == b"BM":
        return BMP
    # WebP: RIFF????WEBP
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return WEBP
    # HEIF/HEIC: ....ftyp<brand> (Box-Länge in 0..4, "ftyp" in 4..8)
    if h[4:8] == b"ftyp" and h[8:12] in _HEIF_BRANDS:
        return HEIC
    return None


# MIME-Typen, die im Browser gefahrlos **inline** ausgeliefert werden dürfen.
# PDF wird vom nativen Viewer gerendert (kein Seiten-Skript); Bilder sind
# passiv. HTML/SVG/XML sind hier bewusst NICHT enthalten.
_SAFE_INLINE_EXACT = {t.mime for t in (PDF, JPEG, PNG, GIF, TIFF, BMP, WEBP, HEIC)}


# Bild-MIMEs, die trotz ``image/``-Präfix NICHT inline sicher sind: SVG ist
# XML/aktiv (kann <script> enthalten) und würde im DMS-Origin ausgeführt.
_UNSAFE_IMAGE_MIMES = {"image/svg+xml", "image/svg"}


def is_safe_inline(mime: str | None) -> bool:
    """True, wenn ``mime`` gefahrlos inline (``as_attachment=False``) taugt.

    Exakte Allowlist (PDF + Raster-Bilder) plus generisches ``image/*`` – aber
    SVG ist bewusst ausgeschlossen (aktives XML, kein passives Bild).
    """
    if not mime:
        return False
    m = mime.split(";", 1)[0].strip().lower()
    if m in _UNSAFE_IMAGE_MIMES:
        return False
    return m in _SAFE_INLINE_EXACT or m.startswith("image/")
