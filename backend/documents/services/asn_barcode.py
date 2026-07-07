"""ASN-Barcode-Erkennung nach paperless-ngx-Vorbild.

Erkennt ASN-Barcodes auf Dokumentseiten BEVOR die Text-Regex greift.
Keine ASN-Vergabe-Logik – nur Extraktion; der bestehende match_and_reconcile
im asn-Service bleibt allein zuständig für Counter/Audit/Reconcile.

Konfiguration (Django-Settings / Env):
  ASN_BARCODE_ENABLED  – default True
  ASN_BARCODE_PREFIX   – default "ASN" (case-insensitiv)
  ASN_BARCODE_SCANNER  – "ZXING" (default) oder "PYZBAR"
  ASN_BARCODE_DPI      – default 300
  ASN_BARCODE_UPSCALE  – default 2.0
  ASN_BARCODE_MAX_PAGES – 0 = alle Seiten
  ASN_BARCODE_PAGES    – komma-getrennte Seitenzahlen (1-basiert) oder leer = alle
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return getattr(settings, "ASN_BARCODE_ENABLED", True)


def _prefix() -> str:
    return getattr(settings, "ASN_BARCODE_PREFIX", "ASN")


def _scanner() -> str:
    return str(getattr(settings, "ASN_BARCODE_SCANNER", "ZXING")).upper()


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


def _upscale() -> float:
    raw = getattr(settings, "ASN_BARCODE_UPSCALE", 2.0)
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 2.0


def _max_pages() -> int:
    raw = getattr(settings, "ASN_BARCODE_MAX_PAGES", 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _extract_asn_from_payload(payload: str, prefix: str) -> int | None:
    """Extrahiert die ASN-Zahl aus einem Barcode-Payload.

    Paperless-ngx-Logik: Der Barcode muss mit dem ASN-Prefix beginnen. Danach
    werden alle Nicht-Ziffern entfernt und die restliche Zahl wird als ASN
    gelesen. Dadurch funktionieren auch Payloads wie ``ASN: 000123``.
    """
    value = payload.strip()
    if not value.lower().startswith(prefix.lower()):
        return None
    suffix = value[len(prefix) :].strip()
    digits = re.sub(r"\D", "", suffix)
    return int(digits) if digits else None


def _read_barcodes_zxing(image: "Image.Image") -> list[tuple[str, str]]:
    import zxingcpp

    result = []
    for barcode in zxingcpp.read_barcodes(image):
        if barcode.text:
            result.append((str(barcode.format), barcode.text))
    return result


def _read_barcodes_pyzbar(image: "Image.Image") -> list[tuple[str, str]]:
    from pyzbar.pyzbar import ZBarSymbol, decode

    result = []
    for barcode in decode(image, symbols=[ZBarSymbol.QRCODE, ZBarSymbol.CODE128]):
        if barcode.data:
            result.append(
                (barcode.type, barcode.data.decode("utf-8", errors="ignore"))
            )
    return result


def _reader() -> tuple[str, Callable[["Image.Image"], list[tuple[str, str]]]]:
    scanner = _scanner()
    if scanner == "PYZBAR":
        return "PYZBAR", _read_barcodes_pyzbar
    return "ZXING", _read_barcodes_zxing


def _page_numbers(pdf_path: str, page_filter: list[int] | None) -> list[int] | None:
    if page_filter is not None:
        return [idx + 1 for idx in page_filter if idx >= 0]

    max_pages = _max_pages()
    if max_pages == 0:
        return None

    try:
        from pikepdf import Pdf

        with Pdf.open(pdf_path) as pdf:
            count = len(pdf.pages)
    except Exception:
        return list(range(1, max_pages + 1))

    return list(range(1, min(count, max_pages) + 1))


def scan_pdf_for_asn(pdf_path: str) -> int | None:
    """Scannt ein PDF nach ASN-Barcodes/-QR-Codes.

    Gibt die erste gefundene ASN zurück, sonst None.
    Wenn pyzbar oder zbar nicht verfügbar ist, wird ein WARN geloggt und None zurückgegeben.
    """
    if not _enabled():
        return None

    try:
        from pdf2image import convert_from_path
    except Exception:
        logger.warning("pdf2image nicht verfügbar – ASN-Barcode-Erkennung übersprungen.")
        return None

    prefix = _prefix()
    page_filter = _page_filter()
    pages = _page_numbers(pdf_path, page_filter)
    dpi = _dpi()
    upscale = _upscale()

    try:
        scanner_name, reader = _reader()
    except Exception as exc:
        logger.warning("Barcode-Scanner nicht verfügbar: %s", exc)
        return None

    logger.debug(
        "ASN-Barcode-Scan: scanner=%s dpi=%s upscale=%s pages=%s",
        scanner_name,
        dpi,
        upscale,
        pages or "all",
    )

    try:
        if pages is None:
            images = convert_from_path(pdf_path, dpi=dpi, fmt="ppm")
        else:
            images = []
            for page_no in pages:
                images.extend(
                    convert_from_path(
                        pdf_path,
                        dpi=dpi,
                        fmt="ppm",
                        first_page=page_no,
                        last_page=page_no,
                    )
                )
    except Exception as exc:
        logger.warning("pdf2image Fehler für %s: %s", pdf_path, exc)
        return None

    for idx, image in enumerate(images):
        page_no = (pages[idx] if pages is not None and idx < len(pages) else idx + 1)
        if upscale > 1.0:
            width, height = image.size
            image = image.resize((round(width * upscale), round(height * upscale)))
        try:
            barcodes = reader(image)
        except Exception as exc:
            logger.warning("%s decode Fehler Seite %d: %s", scanner_name, page_no, exc)
            continue

        for barcode_type, payload in barcodes:
            asn = _extract_asn_from_payload(payload, prefix)
            if asn is not None:
                logger.debug(
                    "ASN %d per Barcode (%s) auf Seite %d erkannt.",
                    asn,
                    barcode_type,
                    page_no,
                )
                return asn

    return None
