"""ASN-Service (Archive Serial Number) – gesamte Businesslogik des ASN-Features.

Design-Vorgaben (STOAA-284/285, Vorbild paperless-ngx):

* Jedes **logische** ``Document`` besitzt dauerhaft **genau eine** unveränderliche
  ASN. Die ASN gehört zum Dokument, nie zu einer ``DocumentVersion``, und ändert
  sich über alle Versionen hinweg nie.
* Die ASN-Vergabe ist **fortlaufend, lückenfrei (soweit technisch möglich),
  transaktionssicher und frei von Race Conditions**. Realisiert über einen
  dedizierten Zähler (``ASNCounter``), der bei der Vergabe per
  ``select_for_update()`` gesperrt wird – parallele Vergaben serialisieren sich,
  Duplikate sind ausgeschlossen. Es wird **nicht** über ``count()+1`` oder die
  Datenbank-ID vergeben.
* Die **komplette** ASN-Businesslogik lebt hier (Clean Architecture / SRP):
  Models enthalten nur die Invariante (jedes Dokument bekommt genau eine ASN),
  ViewSets und die Import-Pipeline rufen ausschließlich diesen Service auf.

Öffentliche Kernfunktionen (Spec):

* :func:`generate_asn` – weist einem Dokument atomar die nächste ASN zu.
* :func:`render_qr`    – rendert den QR-Code des Dokuments als PNG-Bytes.
* :func:`parse_asn`    – liest die ASN aus (OCR-)Text (Regex ``ASN\\s*([0-9]+)``).

Zusätzlich (Pipeline/API): :func:`allocate_asn`, :func:`assign_asn`,
:func:`coerce_asn`, :func:`format_asn`, :func:`qr_payload`,
:func:`find_document_by_asn`, :func:`match_and_reconcile`.
"""
from __future__ import annotations

import io
import re

from django.db import transaction

# Breite der Null-Auffüllung in der Anzeige-/QR-Form: ``ASN000123``.
ASN_PAD_WIDTH = 6

# Spec-Regex zur ASN-Erkennung in (OCR-)Text: ``ASN`` gefolgt von optionalem
# Whitespace und mindestens einer Ziffer. Bewusst case-insensitive, damit auch
# ``asn`` erkannt wird. Führende Nullen normalisiert ``int()``.
_ASN_RE = re.compile(r"ASN\s*([0-9]+)", re.IGNORECASE)
# Reine Ziffernfolge (für die lenient API-/Such-Eingabe ``12345``).
_DIGITS_RE = re.compile(r"^[0-9]+$")


# ---------------------------------------------------------------------------
# Formatierung / Parsing
# ---------------------------------------------------------------------------
def format_asn(asn: int, *, width: int = ASN_PAD_WIDTH) -> str:
    """Formt eine ASN-Zahl in die kanonische Anzeigeform ``ASN000123``."""
    return f"ASN{int(asn):0{width}d}"


def parse_asn(text: str | None) -> int | None:
    """Extrahiert die ASN aus **Text** – strikt nach Spec-Regex ``ASN\\s*([0-9]+)``.

    Wird von der OCR-Pipeline genutzt: nur eine ausdrücklich mit ``ASN`` markierte
    Zahl gilt als ASN – beliebige Zahlen im Dokumenttext lösen **keine** Zuordnung
    aus. Gibt die erste gefundene ASN als ``int`` zurück (führende Nullen
    normalisiert), sonst ``None``.
    """
    if not text:
        return None
    match = _ASN_RE.search(str(text))
    return int(match.group(1)) if match else None


def coerce_asn(value: object) -> int | None:
    """Liest eine ASN aus **Benutzereingaben** – tolerant für API/Suche.

    Akzeptiert sowohl die präfixierte Form (``ASN12345``, ``asn 12345``) als auch
    eine reine Ziffernfolge (``12345``, ``000123``). So liefern die Sucheingaben
    ``ASN12345`` und ``12345`` dasselbe Dokument. Gibt ``None`` zurück, wenn keine
    ASN ableitbar ist.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    prefixed = parse_asn(text)
    if prefixed is not None:
        return prefixed
    if _DIGITS_RE.match(text):
        return int(text)
    return None


# ---------------------------------------------------------------------------
# Vergabe (transaktionssicher, lückenlos, ohne Race Conditions)
# ---------------------------------------------------------------------------
def allocate_asn() -> int:
    """Reserviert atomar die nächste fortlaufende ASN und gibt sie zurück.

    Sperrt die Zählerzeile per ``select_for_update()`` innerhalb einer eigenen
    Transaktion. Konkurrierende Aufrufe serialisieren sich an dieser Sperre –
    keine Doppelvergabe, keine Race Condition. Ein Rollback der umgebenden
    Transaktion macht die Erhöhung rückgängig (lückenlos, soweit technisch
    möglich).
    """
    # Lokaler Import vermeidet einen Import-Zyklus documents.models <-> services.
    from documents.models import ASNCounter

    with transaction.atomic():
        counter = ASNCounter.objects.select_for_update().filter(pk=1).first()
        if counter is None:
            # Erstinitialisierung (falls die Daten-Migration den Zähler nicht
            # anlegen konnte). Danach erneut gesperrt lesen.
            ASNCounter.objects.get_or_create(pk=1, defaults={"last_value": 0})
            counter = ASNCounter.objects.select_for_update().get(pk=1)
        counter.last_value += 1
        counter.save(update_fields=["last_value"])
        return counter.last_value


def assign_asn(document) -> int:
    """Stellt sicher, dass ``document`` genau eine ASN besitzt (idempotent).

    Ist bereits eine ASN gesetzt, wird sie unverändert zurückgegeben (die ASN
    wird **nie** geändert). Sonst wird eine neue ASN reserviert und dem Objekt
    zugewiesen (nur In-Memory – das Persistieren übernimmt der Aufrufer bzw.
    ``Document.save()``). Diese Funktion ist die einzige Stelle, an der die
    Invariante „jedes Dokument bekommt eine ASN" erzwungen wird.
    """
    if getattr(document, "asn", None):
        return document.asn
    document.asn = allocate_asn()
    return document.asn


def generate_asn(document) -> int:
    """Vergibt einem **bestehenden** Dokument die nächste ASN und persistiert sie.

    Spec-Kernfunktion. Idempotent: ein Dokument, das bereits eine ASN besitzt,
    behält sie. Schreibt gezielt nur das ``asn``-Feld (kein WORM-/save()-Umweg
    nötig, ASN ist ``editable=False``).
    """
    from documents.models import Document

    if document.asn:
        return document.asn
    asn = allocate_asn()
    Document.objects.filter(pk=document.pk).update(asn=asn)
    document.asn = asn
    return asn


# ---------------------------------------------------------------------------
# QR-Code
# ---------------------------------------------------------------------------
def qr_payload(document) -> str:
    """Inhalt des QR-Codes: ausschließlich ``ASN000123`` (kein JSON, keine URL)."""
    return format_asn(document.asn)


def render_qr(document) -> bytes:
    """Rendert den QR-Code des Dokuments als PNG und gibt die rohen Bytes zurück.

    Der Code enthält ausschließlich die ASN in der Form ``ASN000123`` – keine
    URL, kein JSON, keine Metadaten (bewusst maximal robust & offline scanbar).
    """
    import qrcode

    qr = qrcode.QRCode(
        version=None,  # automatische, minimale Version passend zum Inhalt
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_payload(document))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Lookup / Re-Scan-Reconcile (Import-Historie)
# ---------------------------------------------------------------------------
def find_document_by_asn(asn: int | None):
    """Findet das Dokument zu einer ASN (systemweit, ohne Owner-Scope) oder ``None``."""
    from documents.models import Document

    if asn is None:
        return None
    return Document.objects.filter(asn=asn).first()


def record_scan(document, version, *, matched_by: str = "OCR", confidence: float = 1.0):
    """Protokolliert eine ASN-Erkennung in der Import-Historie (``ASNScan``)."""
    from documents.models import ASNScan

    return ASNScan.objects.create(
        document=document,
        version=version,
        matched_by=matched_by,
        confidence=confidence,
    )


def _attach_version_to(document, version, *, actor=None) -> int:
    """Hängt eine bestehende Version an ein anderes Dokument (Re-Scan-Merge).

    Setzt fortlaufende ``version_no`` und verkettet den ``prev_hash`` in die
    Hash-Kette des Zieldokuments. Schreibt über ``QuerySet.update`` (die Version
    ist zu diesem Zeitpunkt noch nicht WORM-gesiegelt, das Sealing folgt später
    in der Pipeline). Gibt die neue Versionsnummer zurück.
    """
    from documents.models import AuditLogEntry, Document, DocumentVersion

    with transaction.atomic():
        last = (
            DocumentVersion.objects.select_for_update()
            .filter(document=document)
            .order_by("-version_no")
            .first()
        )
        next_no = (last.version_no if last else 0) + 1
        prev_hash = last.sha256 if last else ""

        DocumentVersion.objects.filter(pk=version.pk).update(
            document=document,
            version_no=next_no,
            prev_hash=prev_hash,
        )
        version.document = document
        version.document_id = document.pk
        version.version_no = next_no
        version.prev_hash = prev_hash

        Document.objects.filter(pk=document.pk).update(current_version=version)
        document.current_version = version

        AuditLogEntry.objects.create(
            actor=actor,
            action="asn_match",
            object_type="DocumentVersion",
            object_id=str(version.id),
            detail={
                "asn": document.asn,
                "document_id": document.pk,
                "version_no": next_no,
            },
        )
    return next_no


def match_and_reconcile(version, *, actor=None) -> dict:
    """OCR-Nachlauf: erkennt eine ASN (Barcode/QR-Vorrang, Fallback Text-Regex).

    Ablauf (Spec „OCR-Integration"):

    0. **Barcode/QR-Erkennung** (pyzbar) – hat Vorrang vor der Text-Regex.
       Fehlt pyzbar/zbar → WARN + Fallback auf Text-Regex (kein Pipeline-Crash).
    1. ASN im OCR-Text erkennen (``parse_asn``) – Fallback wenn kein Barcode.
    2. **Unbekannte/keine ASN** → normale Dokumenterstellung, nichts zu tun.
    3. **Bekannte ASN, dasselbe Dokument** → nur die Erkennung protokollieren.
    4. **Bekannte ASN, anderes Dokument** → Re-Scan eines bestehenden Dokuments:
       die frisch angelegte Version wird als neue Version an das bestehende
       Dokument gehängt (**keine Duplikate**), die Erkennung protokolliert und
       das nun leere, versehentlich neu angelegte Dokument entfernt.

    Best effort: die Funktion ist idempotent im Sinne „bereits zugeordnet →
    kein zweites Verschieben"; sie wird von der Pipeline defensiv aufgerufen
    (ein Fehler hier darf die restliche Verarbeitung nicht abbrechen).
    """
    from documents.models import Document
    from documents.services.asn_barcode import scan_pdf_for_asn

    # Barcode/QR hat Vorrang – nur wenn die Version eine lokale PDF-Datei hat.
    matched_by = "OCR"
    asn = None
    pdf_path = getattr(getattr(version, "file", None), "path", None)
    if pdf_path:
        try:
            asn = scan_pdf_for_asn(pdf_path)
            if asn is not None:
                matched_by = "BARCODE"
        except Exception:
            pass  # Barcode-Fehler darf Pipeline nie abbrechen

    # Fallback: Text-Regex
    if asn is None:
        asn = parse_asn(version.ocr_text or "")

    if asn is None:
        return {"matched": False, "asn": None}

    existing = find_document_by_asn(asn)
    if existing is None:
        # ASN unbekannt → normale Dokumenterstellung (das neue Dokument behält
        # seine eigene, frisch vergebene ASN).
        return {"matched": False, "asn": asn, "reason": "unknown"}

    current = version.document
    if existing.pk == current.pk:
        # Re-Upload auf dasselbe Dokument: nur die Erkennung dokumentieren.
        record_scan(existing, version, matched_by=matched_by)
        return {"matched": True, "asn": asn, "moved": False, "document_id": existing.pk}

    # Re-Scan eines anderen Dokuments → Version übernehmen, Duplikat vermeiden.
    # Zuerst den OneToOne-Zeiger ``current_version`` des Quell-Dokuments lösen:
    # die Version ist dort noch als current_version verlinkt; ohne das Lösen
    # würden nach dem Umhängen ZWEI Dokumente dieselbe Version als current_version
    # führen und den unique-Constraint verletzen.
    Document.objects.filter(pk=current.pk).update(current_version=None)
    current.current_version = None

    _attach_version_to(existing, version, actor=actor)
    record_scan(existing, version, matched_by=matched_by)

    if not current.versions.exists():
        # Das leere, versehentlich neu angelegte Dokument entfernen (keine Duplikate).
        Document.objects.filter(pk=current.pk).delete()

    return {"matched": True, "asn": asn, "moved": True, "document_id": existing.pk}
