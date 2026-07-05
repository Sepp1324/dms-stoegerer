"""ASN-Erkennung aus aufgeklebten Barcodes/QR-Codes (STOAA-516).

Ergänzt die reine OCR-Text-Erkennung (:func:`documents.services.asn.parse_asn`)
um echte, auf die Seite **gedruckte** ASN-Etiketten – analog paperless-ngx:

* **Code128** (bzw. beliebiger Text-Barcode) mit dem Inhalt ``<PREFIX><Ziffern>``,
  Präfix konfigurierbar über ``ASN_BARCODE_PREFIX`` (Default ``"ASN"``,
  case-insensitiv).
* **QR-Codes** mit exakt der Nutzlast, die :func:`documents.services.asn.qr_payload`
  / :func:`render_qr` erzeugen (``ASN000123``). Damit schließt sich der Round-Trip:
  ein gedrucktes Dokument wieder eingescannt wird demselben Dokument zugeordnet.

Design-Leitplanken:

* **Keine ASN-Vergabe-Logik** – dieser Modul erkennt nur eine ASN-*Zahl* auf den
  Seiten. Zuordnung, Counter, Invarianten und Audit bleiben ausschließlich im
  bestehenden :mod:`documents.services.asn` (``match_and_reconcile``/``assign_asn``).
* **Best effort, nie Pipeline-kritisch.** Fehlt zur Laufzeit die native
  zbar-Bibliothek (``libzbar0``) oder tritt beim Rendern/Scannen ein Fehler auf,
  wird sauber geloggt und ``None`` zurückgegeben – die aufrufende Pipeline fällt
  dann auf die Text-Regex zurück (kein Crash).
* **Lazy Imports** (``pyzbar``, ``pdf2image``, ``PIL``) – das Backend lädt auch
  ohne die Render-/Scan-Bibliotheken (z. B. für ``manage.py check``).
"""
from __future__ import annotations

import logging
import os
import re

from django.conf import settings

logger = logging.getLogger(__name__)


def _prefix() -> str:
    return getattr(settings, "ASN_BARCODE_PREFIX", "ASN") or "ASN"


def _enabled() -> bool:
    return bool(getattr(settings, "ASN_BARCODE_ENABLED", True))


def _page_numbers() -> set[int] | None:
    """Liest ``ASN_BARCODE_PAGES`` als Menge 1-basierter Seitennummern.

    Leer/``"all"`` → ``None`` (alle Seiten). Sonst kommagetrennte Zahlen
    (z. B. ``"1"`` oder ``"1,2"``). Ungültige Einträge werden ignoriert.
    """
    raw = str(getattr(settings, "ASN_BARCODE_PAGES", "") or "").strip()
    if not raw or raw.lower() == "all":
        return None
    pages: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            pages.add(int(part))
    return pages or None


def extract_asn(payload: str | None, prefix: str | None = None) -> int | None:
    """Extrahiert die ASN-Zahl aus einer Barcode-/QR-Nutzlast.

    Akzeptiert ``<PREFIX><Ziffern>`` (Präfix case-insensitiv, optionaler
    Whitespace dazwischen) und normalisiert führende Nullen via ``int()``. Kein
    passendes Präfix → ``None``.
    """
    if not payload:
        return None
    pattern = re.compile(rf"{re.escape(prefix or _prefix())}\s*([0-9]+)", re.IGNORECASE)
    match = pattern.search(str(payload).strip())
    return int(match.group(1)) if match else None


def _render_pages(src: str, page_numbers: set[int] | None):
    """Rendert die zu scannenden Seiten als PIL-Bilder (lazy Imports).

    PDFs über ``pdf2image``/poppler, Einzelbilder direkt über Pillow. Bei einer
    eingeschränkten Seitenauswahl wird für PDFs nur der nötige Bereich gerendert.
    """
    if src.lower().endswith(".pdf"):
        from pdf2image import convert_from_path

        kwargs = {"dpi": 200}
        if page_numbers:
            kwargs["first_page"] = min(page_numbers)
            kwargs["last_page"] = max(page_numbers)
            images = convert_from_path(src, **kwargs)
            first = min(page_numbers)
            return [
                img
                for idx, img in enumerate(images, start=first)
                if idx in page_numbers
            ]
        return convert_from_path(src, **kwargs)

    # Einzelbild (JPG/PNG/…): gilt als Seite 1.
    if page_numbers and 1 not in page_numbers:
        return []
    from PIL import Image

    return [Image.open(src)]


def scan_asn(version, *, pages: set[int] | None = None) -> tuple[int, str] | None:
    """Scannt die Seiten einer Version nach einer ASN in Code128/QR.

    Rückgabe ``(asn, matched_by)`` mit ``matched_by`` ``"QR"`` oder ``"Barcode"``
    – oder ``None``, wenn nichts gefunden wurde bzw. der Scan nicht möglich war
    (Feature deaktiviert, keine Datei, zbar fehlt, Fehler). Wirft nie.
    """
    if not _enabled():
        return None

    src = version.archive_path or version.file_path
    if not src or not os.path.exists(src):
        return None

    try:
        # Import erst hier: ist libzbar0 nicht installiert, schlägt bereits der
        # Import fehl (ImportError/OSError) → sauberer Fallback statt Crash.
        from pyzbar.pyzbar import ZBarSymbol, decode
    except Exception as exc:  # noqa: BLE001 – zbar optional, defensiver Fallback
        logger.warning(
            "ASN-Barcode-Scan übersprungen: pyzbar/libzbar0 nicht verfügbar (%s). "
            "Fallback auf OCR-Text-Erkennung.",
            exc,
        )
        return None

    prefix = _prefix()
    page_numbers = pages if pages is not None else _page_numbers()

    try:
        images = _render_pages(src, page_numbers)
        for image in images:
            for symbol in decode(
                image, symbols=[ZBarSymbol.CODE128, ZBarSymbol.QRCODE]
            ):
                payload = symbol.data.decode("utf-8", errors="ignore")
                asn = extract_asn(payload, prefix)
                if asn is not None:
                    matched_by = "QR" if symbol.type == "QRCODE" else "Barcode"
                    return asn, matched_by
    except Exception:  # noqa: BLE001 – Rendern/Scannen ist best effort
        logger.exception(
            "ASN-Barcode-Scan für Version %s fehlgeschlagen – Fallback auf Text.",
            getattr(version, "id", "?"),
        )
        return None

    return None
