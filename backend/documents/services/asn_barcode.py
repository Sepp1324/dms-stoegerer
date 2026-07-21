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
from celery.exceptions import SoftTimeLimitExceeded

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


def _dpi() -> int:
    """Rendering-Auflösung fürs Barcode-Scanning.

    Kleine Etiketten brauchen mehr als 150 DPI. 300 ist ein guter Default für
    Scanner-PDFs; per Env konfigurierbar, falls Backfill deutlich zu langsam ist.
    """
    raw = getattr(settings, "ASN_BARCODE_DPI", 300)
    try:
        return max(150, int(raw))
    except (TypeError, ValueError):
        return 300


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
        from pyzbar.pyzbar import ZBarSymbol, decode as pyzbar_decode
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        logger.warning(
            "pyzbar nicht verfügbar – ASN-Barcode-Erkennung deaktiviert. "
            "Bitte libzbar0 + pyzbar installieren."
        )
        return None

    try:
        from pdf2image import convert_from_path
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        logger.warning("pdf2image nicht verfügbar – ASN-Barcode-Erkennung übersprungen.")
        return None

    prefix = _prefix()
    page_filter = _page_filter()
    dpi = _dpi()

    try:
        images = convert_from_path(pdf_path, dpi=dpi, fmt="ppm")
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.warning("pdf2image Fehler für %s: %s", pdf_path, exc)
        return None

    for idx, image in enumerate(images):
        if page_filter is not None and idx not in page_filter:
            continue
        for variant in _decode_variants(image):
            try:
                # ZBar decodiert sonst alle unterstützten Symbologien. Auf normalen
                # Dokumentseiten kann der DataBar-Decoder dabei noisy C-Assertions
                # auf stderr schreiben. Für ASN brauchen wir ausschließlich QR und
                # Code128, also scannen wir nur diese beiden Typen.
                barcodes = pyzbar_decode(
                    variant,
                    symbols=[ZBarSymbol.QRCODE, ZBarSymbol.CODE128],
                )
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                logger.warning("pyzbar decode Fehler Seite %d: %s", idx + 1, exc)
                continue

            for barcode in barcodes:
                try:
                    payload = barcode.data.decode("utf-8", errors="ignore")
                except SoftTimeLimitExceeded:
                    raise
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


def _decode_variants(image):
    """Liefert Bildvarianten fürs Barcode-Decoding, robust zuerst.

    Echte Scans (Brother-MFP u. a.) sind für QR/Code128 oft erst nach
    Binarisierung lesbar – pyzbar findet auf dem farbigen/graustufigen Rohbild
    nichts, aber auf einem sauber geschwellten Schwarz-Weiß-Bild schon
    (bestätigt an einem realen ASN-QR: nur die Threshold-Variante dekodierte).
    Reihenfolge: Rohbild, Graustufe, Autokontrast, Threshold(128). Sobald eine
    Variante trifft, bricht der Aufrufer ab.
    """
    variants = [image]
    try:
        from PIL import ImageOps

        gray = ImageOps.grayscale(image)
        variants.append(gray)
        variants.append(ImageOps.autocontrast(gray))
        variants.append(gray.point(lambda px: 0 if px < 128 else 255))
    except SoftTimeLimitExceeded:
        raise
    except Exception:  # pragma: no cover - Pillow-Fehler nie fatal
        pass
    return variants
