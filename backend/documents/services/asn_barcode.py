"""ASN-Barcode-Erkennung: Code128 + QR via pyzbar.

Erkennt ASN-Barcodes auf Dokumentseiten BEVOR die Text-Regex greift.
Keine ASN-Vergabe-Logik – nur Extraktion; der bestehende match_and_reconcile
im asn-Service bleibt allein zuständig für Counter/Audit/Reconcile.

Konfiguration (Django-Settings / Env):
  ASN_BARCODE_ENABLED  – default True
  ASN_BARCODE_PREFIX   – default "ASN" (case-insensitiv)
  ASN_BARCODE_PAGES    – komma-getrennte Seitenzahlen (1-basiert) oder leer = alle
"""
from __future__ import annotations

import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return getattr(settings, "ASN_BARCODE_ENABLED", True)


def _prefix() -> str:
    return getattr(settings, "ASN_BARCODE_PREFIX", "ASN")


def _page_filter() -> list[int] | None:
    """Gibt None (= alle Seiten) oder eine Liste 0-basierter Seitenindizes zurück."""
    raw = getattr(settings, "ASN_BARCODE_PAGES", "").strip()
    if not raw:
        return None
    result = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part) - 1)  # 1-basiert → 0-basiert
    return result or None


def _extract_asn_from_payload(payload: str, prefix: str) -> int | None:
    """Extrahiert die ASN-Zahl aus einem Barcode-Payload.

    Akzeptiert:
      - Code128: "<PREFIX><Ziffern>" z. B. "ASN000123"
      - QR: identisches Format (render_qr erzeugt "ASN000123")
    """
    pattern = re.compile(
        r"^" + re.escape(prefix) + r"\s*([0-9]+)$",
        re.IGNORECASE,
    )
    m = pattern.match(payload.strip())
    if m:
        return int(m.group(1))
    return None


def scan_pdf_for_asn(pdf_path: str) -> int | None:
    """Scannt ein PDF nach ASN-Barcodes/-QR-Codes.

    Gibt die erste gefundene ASN zurück, sonst None.
    Wenn pyzbar oder zbar nicht verfügbar ist, wird ein WARN geloggt und None zurückgegeben.
    """
    if not _enabled():
        return None

    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
    except Exception:
        logger.warning(
            "pyzbar nicht verfügbar – ASN-Barcode-Erkennung deaktiviert. "
            "Bitte libzbar0 + pyzbar installieren."
        )
        return None

    try:
        from pdf2image import convert_from_path
    except Exception:
        logger.warning("pdf2image nicht verfügbar – ASN-Barcode-Erkennung übersprungen.")
        return None

    prefix = _prefix()
    page_filter = _page_filter()

    try:
        images = convert_from_path(pdf_path, dpi=150, fmt="ppm")
    except Exception as exc:
        logger.warning("pdf2image Fehler für %s: %s", pdf_path, exc)
        return None

    for idx, image in enumerate(images):
        if page_filter is not None and idx not in page_filter:
            continue
        try:
            barcodes = pyzbar_decode(image)
        except Exception as exc:
            logger.warning("pyzbar decode Fehler Seite %d: %s", idx + 1, exc)
            continue

        for barcode in barcodes:
            try:
                payload = barcode.data.decode("utf-8", errors="ignore")
            except Exception:
                continue
            asn = _extract_asn_from_payload(payload, prefix)
            if asn is not None:
                logger.debug(
                    "ASN %d per Barcode (%s) auf Seite %d erkannt.",
                    asn,
                    barcode.type,
                    idx + 1,
                )
                return asn

    return None
