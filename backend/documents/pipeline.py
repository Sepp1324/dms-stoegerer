"""Verarbeitungs-Pipeline für Dokumente.

Reihenfolge (siehe KONZEPT.md §4):
    UPLOADED → HASHED → OCR_RUNNING → OCR_DONE → CLASSIFICATION_RUNNING
    → CLASSIFIED → THUMBNAIL_DONE → SEALED → READY

Die schweren Schritte laufen als Celery-Task (tasks.py); hier stehen die
reinen Funktionen, damit sie testbar und ohne Celery aufrufbar bleiben.
Die OCR selbst ist hinter ``documents.services.ocr`` gekapselt; diese Pipeline
orchestriert Status, Persistenz, Audit und die nachgelagerten Verarbeitungsschritte.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from . import storage
from .models import AuditLogEntry, Document, DocumentVersion
from .services import page_text
from documents.services.ocr.engine import run_ocr

logger = logging.getLogger(__name__)


def sha256_of(file_path: str | Path) -> str:
    """SHA-256 einer Datei – Baustein von Hash-Kette und Dedup."""
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicate_version(sha256_hex: str) -> DocumentVersion | None:
    """Existierende Version mit identischem Inhalts-Hash (Dedup beim Ingest).

    Grundlage für den Hash-Dedup der E-Mail-Ingestion: gleiche Bytes → gleicher
    SHA-256 → kein Doppel-Import.
    """
    if not sha256_hex:
        return None
    return DocumentVersion.objects.filter(sha256=sha256_hex).first()


def create_document_from_file(
    file_path: str,
    *,
    title: str,
    owner=None,
    mime: str = "",
    size: int | None = None,
    ingest_source: str = "upload",
) -> tuple[Document, DocumentVersion]:
    """Legt Dokument + erste Version an und protokolliert die Aufnahme.

    Führt (noch) keine OCR aus – das übernimmt die Pipeline anschließend.
    """
    path = Path(file_path)
    document = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=document,
        version_no=1,
        file_path=str(path),
        mime_type=mime,
        size=size if size is not None else path.stat().st_size,
        created_by=owner,
        ingest_source=ingest_source,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])

    AuditLogEntry.objects.create(
        actor=owner,
        action="upload",
        object_type="Document",
        object_id=str(document.id),
        detail={"filename": path.name, "size": version.size},
    )
    return document, version


def create_version_for_document(
    document: Document,
    file_path: str,
    *,
    created_by=None,
    mime: str = "",
    size: int | None = None,
) -> DocumentVersion:
    """Hängt eine neue Version an ein bestehendes Dokument (fortlaufende Nr.).

    Setzt die neue Version als ``current_version`` und protokolliert die Aufnahme.
    Hash-Kette (``sha256``/``prev_hash``) füllt anschließend ``process_version``.
    """
    path = Path(file_path)
    last_no = (
        document.versions.order_by("-version_no")
        .values_list("version_no", flat=True)
        .first()
        or 0
    )
    version = DocumentVersion.objects.create(
        document=document,
        version_no=last_no + 1,
        file_path=str(path),
        mime_type=mime,
        size=size if size is not None else path.stat().st_size,
        created_by=created_by,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])

    AuditLogEntry.objects.create(
        actor=created_by,
        action="add_version",
        object_type="Document",
        object_id=str(document.id),
        detail={
            "filename": path.name,
            "size": version.size,
            "version_no": version.version_no,
        },
    )
    return version


def verify_document_integrity(document: Document) -> dict:
    """Prüft die Hash-Kette eines Dokuments – Grundlage der prüfbaren Versionierung.

    Zwei unabhängige Prüfungen je Version:
      * **file_ok** – die Datei auf der Platte wird neu gehasht und mit dem
        gespeicherten ``sha256`` verglichen (Beweis der Unverändertheit).
      * **prev_ok** – der gespeicherte ``prev_hash`` entspricht dem ``sha256``
        der Vorgängerversion (Beweis der lückenlosen Verkettung).

    Rückgabe: ``{"chain_ok": bool, "versions": [ {…}, … ]}`` – aufsteigend nach
    Versionsnummer. ``chain_ok`` ist nur wahr, wenn ALLE Prüfungen bestehen.
    """
    versions = list(document.versions.order_by("version_no"))
    results = []
    chain_ok = True
    prev_sha = ""

    for version in versions:
        source = version.file_path
        file_present = bool(source) and os.path.exists(source)
        computed = sha256_of(source) if file_present else ""
        # Nur prüfbar, wenn ein Hash hinterlegt ist (unverarbeitete Version: offen).
        file_ok = bool(version.sha256) and file_present and computed == version.sha256
        prev_ok = (version.prev_hash or "") == (prev_sha or "")

        if not (file_ok and prev_ok):
            chain_ok = False

        results.append(
            {
                "version_no": version.version_no,
                "sha256": version.sha256,
                "computed_sha256": computed,
                "prev_hash": version.prev_hash,
                "expected_prev_hash": prev_sha,
                "file_present": file_present,
                "file_ok": file_ok,
                "prev_ok": prev_ok,
            }
        )
        prev_sha = version.sha256

    return {"chain_ok": chain_ok, "versions": results}


def extract_text(pdf_path: str | Path) -> str:
    """Extrahiert den gesamten Text eines PDFs via poppler ``pdftotext``."""
    import subprocess

    try:
        result = subprocess.run(
            ["pdftotext", "-q", str(pdf_path), "-"],
            capture_output=True,
            timeout=180,
        )
        return result.stdout.decode("utf-8", errors="ignore")
    except Exception:  # pragma: no cover - Textextraktion ist best effort
        return ""


def generate_thumbnail(version, *, max_width: int = 700) -> str | None:
    """Erzeugt ein JPEG-Miniaturbild der ersten Seite und speichert den Pfad.

    Quelle: bevorzugt das Archiv-PDF, sonst das Original. Für Bild-Originale
    direkt via Pillow. Imports sind lazy, damit das Backend ohne die
    Render-Bibliotheken lädt (z. B. `manage.py check`).
    """
    src = version.archive_path or version.file_path
    if not src or not os.path.exists(src):
        return None

    thumbs_dir = storage.DATA_DIR / "thumbnails"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    dest = thumbs_dir / f"{version.id}.jpg"

    try:
        if src.lower().endswith(".pdf"):
            from pdf2image import convert_from_path

            images = convert_from_path(src, first_page=1, last_page=1, size=(max_width, None))
            if not images:
                return None
            img = images[0].convert("RGB")
        else:
            from PIL import Image

            img = Image.open(src).convert("RGB")
            img.thumbnail((max_width, max_width * 4))
        img.save(dest, "JPEG", quality=80)
    except Exception:  # pragma: no cover - Vorschau ist optional
        return None

    version.thumbnail_path = str(dest)
    version.save(update_fields=["thumbnail_path"])
    return str(dest)


def _page_count(pdf_path: Path) -> int | None:
    try:
        import pikepdf

        with pikepdf.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:  # pragma: no cover - Seitenzahl ist optional
        return None


def hash_version(version: DocumentVersion) -> None:
    """Hash-Kette füllen und State ``HASHED`` setzen."""
    version.sha256 = sha256_of(version.file_path)

    previous = (
        version.document.versions.filter(version_no__lt=version.version_no)
        .order_by("-version_no")
        .first()
    )
    version.prev_hash = previous.sha256 if previous else ""
    version.save(update_fields=["sha256", "prev_hash"])
    version.transition_to(
        DocumentVersion.ProcessingState.HASHED,
        actor=version.created_by,
        detail={"sha256": version.sha256, "prev_hash": version.prev_hash},
    )


def ocr_version(version: DocumentVersion) -> dict:
    """OCR ausführen, technische OCR-Felder speichern und State ``OCR_DONE`` setzen."""
    from django.utils import timezone

    from .models import OCRStatus

    version.transition_to(
        DocumentVersion.ProcessingState.OCR_RUNNING,
        actor=version.created_by,
    )
    started_at = timezone.now()
    DocumentVersion.objects.filter(pk=version.pk).update(
        ocr_status=OCRStatus.RUNNING, ocr_started_at=started_at
    )
    version.ocr_status = OCRStatus.RUNNING
    version.ocr_started_at = started_at

    # Weiche OCR-Fehler (``run_ocr`` liefert ein ``OCRResult`` mit status=FAILED)
    # brechen die Pipeline NICHT ab: ocr_status=failed wird persistiert und die
    # Verarbeitung läuft bis READY weiter (STOAA-225 Blocker 2, Monitoring).
    # Eine *geworfene* Exception dagegen reicht der Retry-Layer bewusst an
    # ``_run_from`` durch → processing_state=FAILED + Retry (STOAA-228). Deshalb
    # hier KEIN eigenes try/except mehr.
    result = run_ocr(version.file_path)

    finished_at = timezone.now()
    archive_candidate = Path(version.file_path).with_suffix(".ocr.pdf")
    archive_path = str(archive_candidate) if archive_candidate.exists() else ""

    version.archive_path = archive_path
    version.ocr_text = result.text
    version.page_count = result.pages
    version.ocr_status = result.status.value
    version.ocr_error = result.error or ""
    version.ocr_engine = result.engine
    version.ocr_duration_ms = result.duration_ms
    version.ocr_finished_at = finished_at
    version.save(
        update_fields=[
            "archive_path",
            "ocr_text",
            "page_count",
            "ocr_status",
            "ocr_error",
            "ocr_engine",
            "ocr_duration_ms",
            "ocr_started_at",
            "ocr_finished_at",
        ]
    )
    page_source = archive_path or version.file_path
    page_indexed = page_text.write_page_texts(
        version,
        page_text.extract_page_texts(page_source, fallback_text=result.text),
    )
    version.transition_to(
        DocumentVersion.ProcessingState.OCR_DONE,
        actor=version.created_by,
        detail={
            "pages": result.pages,
            "ocr_status": result.status.value,
            "archive_path": archive_path,
            "chars": len(result.text),
            "page_texts": page_indexed,
        },
    )
    return {
        "pages": result.pages,
        "chars": len(result.text),
        "ocr_status": result.status.value,
        "archive_path": archive_path,
    }


def classify_version(version: DocumentVersion) -> dict:
    """Regelbasierte Klassifizierung + Workflow-Engine ausführen."""
    from . import classification, workflows
    from .services import case_matching, extraction

    version.refresh_from_db(fields=["processing_state", "ingest_source"])
    version.transition_to(
        DocumentVersion.ProcessingState.CLASSIFICATION_RUNNING,
        actor=version.created_by,
    )
    result = classification.apply_rules(version.document)

    # Workflow-Engine (STOAA-263): document_added nach apply_rules
    source = version.ingest_source or "upload"
    wf_result = workflows.run_workflows(
        version.document,
        trigger_type="document_added",
        source=source,
    )
    result["workflows"] = wf_result.get("workflows", [])

    # Smart Inbox: Strukturvorschläge nach OCR/Klassifizierung vorbereiten.
    # Best effort – Extraktion darf die technische Dokumentverarbeitung nie
    # blockieren; der Nutzer kann die Kandidaten später in der Inbox neu erzeugen.
    try:
        result["extraction_candidates"] = extraction.generate_candidates(
            version.document
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Extraktionskandidaten für Dokument %s fehlgeschlagen",
            version.document_id,
        )
        result["extraction_candidates"] = 0

    # Akten-Autopilot: nach den Strukturvorschlägen können Vertrags-/Polizzen-
    # nummern als starkes Signal dienen. Auch hier gilt: Vorschläge dürfen die
    # technische Verarbeitung nicht blockieren.
    try:
        result["case_file_candidates"] = case_matching.generate_candidates(
            version.document
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Aktenvorschläge für Dokument %s fehlgeschlagen",
            version.document_id,
        )
        result["case_file_candidates"] = 0

    version.document.refresh_from_db(fields=["classification"])
    version.transition_to(
        DocumentVersion.ProcessingState.CLASSIFIED,
        actor=version.created_by,
        detail={"classification": version.document.classification},
    )
    return result


def generate_version_thumbnail(version: DocumentVersion) -> str | None:
    """Miniaturbild erzeugen und State ``THUMBNAIL_DONE`` setzen."""
    thumbnail_path = generate_thumbnail(version)
    version.refresh_from_db(fields=["thumbnail_path", "processing_state"])
    version.transition_to(
        DocumentVersion.ProcessingState.THUMBNAIL_DONE,
        actor=version.created_by,
        detail={"thumbnail_path": thumbnail_path or ""},
    )
    return thumbnail_path


def seal_version(version: DocumentVersion) -> None:
    """WORM-/Retention-Siegel setzen und danach State ``READY`` erreichen."""
    version.refresh_from_db(fields=["processing_state"])
    version.transition_to(
        DocumentVersion.ProcessingState.SEALED,
        actor=version.created_by,
    )
    _seal_version(version)
    version.transition_to(
        DocumentVersion.ProcessingState.READY,
        actor=version.created_by,
    )


# ---------------------------------------------------------------------------
# Pipeline-Schritte als Daten: Name, Funktion und Vorbedingung (der Startzustand,
# den der Schritt selbst per transition_to erwartet). Die Namen MÜSSEN zu
# ``DocumentVersion.processing_failed_step`` passen – sie steuern den Retry-
# Wiedereinstieg (STOAA-228). Die Erfolgs-Map PROCESSING_TRANSITIONS bleibt davon
# bewusst unberührt.
PIPELINE_STEPS = [
    ("hashing",        hash_version,               DocumentVersion.ProcessingState.UPLOADED),
    ("ocr",            ocr_version,                DocumentVersion.ProcessingState.HASHED),
    ("classification", classify_version,           DocumentVersion.ProcessingState.OCR_DONE),
    ("thumbnail",      generate_version_thumbnail, DocumentVersion.ProcessingState.CLASSIFIED),
    ("sealing",        seal_version,               DocumentVersion.ProcessingState.THUMBNAIL_DONE),
]


def _run_from(version: DocumentVersion, start_index: int) -> dict:
    """Läuft die Pipeline ab ``start_index`` und fängt Schrittfehler ab.

    Ein fehlgeschlagener Schritt markiert die Version FAILED (sichtbar, kein
    re-raise – Stil ``scan_consume_folder``) und liefert ein strukturiertes
    Fehlerergebnis zurück. Der Erfolgsfall endet in READY.
    """
    ocr_result = None
    for name, func, _precond in PIPELINE_STEPS[start_index:]:
        try:
            step_result = func(version)
        except Exception as exc:  # noqa: BLE001 – jeder Schritt darf fehlschlagen
            version.mark_processing_failed(
                step=name, error=exc, actor=version.created_by
            )
            logger.exception(
                "Verarbeitungsschritt %r für Version %s fehlgeschlagen",
                name,
                version.id,
            )
            return {
                "version_id": version.id,
                "status": "failed",
                "step": name,
                "processing_state": DocumentVersion.ProcessingState.FAILED,
                "error": str(exc)[:1000],
            }

        if name == "ocr":
            ocr_result = step_result
            # Bestehenden Zwischen-Audit verhaltensgleich erhalten (PR#70).
            AuditLogEntry.objects.create(
                actor=version.created_by,
                action="ocr",
                object_type="DocumentVersion",
                object_id=str(version.id),
                detail={
                    "pages": step_result["pages"],
                    "sha256": version.sha256,
                    "archive_path": step_result["archive_path"],
                    "ocr_status": step_result["ocr_status"],
                    "chars": step_result["chars"],
                },
            )
            # ASN-Integration (STOAA-284/285): Nach dem OCR den Text auf eine ASN
            # prüfen. Erkennt der Service die ASN eines *bestehenden* Dokuments
            # (erneuter Scan eines Papierdokuments), hängt er diese Version als
            # neue Version an das bestehende Dokument und entfernt das Duplikat.
            # Best effort – ein Fehler hier darf die restliche Pipeline nicht
            # abbrechen (Stil ``scan_consume_folder``).
            from documents.services import asn as asn_service

            try:
                asn_service.match_and_reconcile(version, actor=version.created_by)
            except Exception:  # noqa: BLE001 – Zuordnung ist optional/best effort
                logger.exception(
                    "ASN-Reconcile für Version %s fehlgeschlagen", version.id
                )

    return {
        "version_id": version.id,
        "sha256": version.sha256,
        "pages": ocr_result["pages"] if ocr_result else version.page_count,
        "chars": ocr_result["chars"] if ocr_result else len(version.ocr_text or ""),
        "processing_state": DocumentVersion.ProcessingState.READY,
        "status": "done",
    }


def process_version(version: DocumentVersion) -> dict:
    """Vollständige Verarbeitung einer Version entlang der State Machine."""
    result = _run_from(version, 0)
    _sync_contract_center(version, result, actor=version.created_by)
    _sync_entity_graph(version, result, actor=version.created_by)
    try:
        from documents.services import review_tasks

        result["review_tasks"] = review_tasks.sync_document_review_tasks(
            version.document
        )
    except Exception:  # noqa: BLE001 - Review-Tasks dürfen Verarbeitung nicht kippen
        logger.exception("Review-Task-Sync für Version %s fehlgeschlagen", version.id)
    return result


def retry_version(version: DocumentVersion, actor=None) -> dict:
    """Verarbeitet eine FAILED-Version ab dem fehlgeschlagenen Schritt erneut.

    Ablauf (Beispiel OCR-Fehler): ``FAILED → RETRY_PENDING → (HASHED) →
    OCR_RUNNING → …``. ``begin_retry`` zählt den Versuch hoch; anschließend wird
    ``processing_state`` auf die Vorbedingung des fehlgeschlagenen Schritts
    gesetzt, damit der Schritt seine eigene ``transition_to(RUNNING)`` ausführen
    kann.
    """
    version.begin_retry(actor=actor)

    failed_step = version.processing_failed_step
    start_index = 0
    name, _func, precond = PIPELINE_STEPS[0]
    for idx, (step_name, _step_func, step_precond) in enumerate(PIPELINE_STEPS):
        if step_name == failed_step:
            start_index = idx
            name = step_name
            precond = step_precond
            break

    DocumentVersion.objects.filter(pk=version.pk).update(processing_state=precond)
    version.processing_state = precond
    AuditLogEntry.objects.create(
        actor=actor,
        action="processing_resume",
        object_type="DocumentVersion",
        object_id=str(version.id),
        detail={"to": precond, "step": name},
    )

    result = _run_from(version, start_index)
    _sync_contract_center(version, result, actor=actor or version.created_by)
    _sync_entity_graph(version, result, actor=actor or version.created_by)
    try:
        from documents.services import review_tasks

        result["review_tasks"] = review_tasks.sync_document_review_tasks(
            version.document
        )
    except Exception:  # noqa: BLE001 - Review-Tasks dürfen Retry nicht kippen
        logger.exception("Review-Task-Sync für Retry-Version %s fehlgeschlagen", version.id)
    return result


def _sync_contract_center(version: DocumentVersion, result: dict, *, actor=None) -> None:
    """Best-effort-Vertragserkennung nach erfolgreicher Verarbeitung."""
    if result.get("status") != "done":
        return
    try:
        from documents.services import contracts

        result["contract"] = contracts.sync_contract_record(
            version.document, actor=actor
        )
    except Exception:  # noqa: BLE001 - Vertrags-Cockpit darf Pipeline nie kippen
        logger.exception("Contract-Center-Sync für Version %s fehlgeschlagen", version.id)
        result["contract"] = {"status": "failed"}


def _sync_entity_graph(version: DocumentVersion, result: dict, *, actor=None) -> None:
    """Best-effort-Sync des privaten DMS-Gedächtnisses nach READY."""
    if result.get("status") != "done":
        return
    try:
        from documents.services import entity_graph

        result["entity_graph"] = entity_graph.sync_document_entities(
            version.document, actor=actor
        )
    except Exception:  # noqa: BLE001 - Graph darf Pipeline nie kippen
        logger.exception("Entity-Graph-Sync für Version %s fehlgeschlagen", version.id)
        result["entity_graph"] = {"status": "failed"}


def _add_months(d, months: int):
    """Addiert `months` Monate zu einem date-Objekt (kein dateutil nötig)."""
    import calendar
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    from datetime import date
    return date(year, month, day)


def _seal_version(version: DocumentVersion) -> None:
    """Setzt is_immutable=True und schützt die Archiv-Datei (chmod 0444)."""
    import os as _os
    from datetime import date

    # Archiv-Datei schreibschützen
    archive = version.archive_path or version.file_path
    if archive:
        try:
            _os.chmod(archive, 0o444)
        except OSError:
            pass  # Im Test/Mock-Umfeld ggf. kein echtes Dateisystem

    # Aufbewahrungsfrist aus DocumentType berechnen
    retention_until = None
    doc_type = version.document.document_type
    if doc_type and doc_type.retention_months:
        ref = version.document.created_at or version.document.added_at
        base = ref.date() if hasattr(ref, "date") else date.today()
        retention_until = _add_months(base, doc_type.retention_months)

    # Metadaten-Snapshot beim Sealing schreiben (Versionsvergleich Stufe 2,
    # STOAA-312/Option A). Write-once, vor dem WORM-Flag – der Snapshot fließt in
    # die Siegelkette (seal_hash) ein. Best effort: ein Snapshot-Fehler darf das
    # eigentliche WORM-Siegel nicht verhindern (Integrität der Datei-Hash-Kette
    # hat Vorrang; der Snapshot ist additiv).
    from documents.services import version_snapshot

    try:
        version_snapshot.write_snapshot_on_seal(version, actor=version.created_by)
    except Exception:  # noqa: BLE001 – Snapshot ist additiv, blockiert das Siegel nicht
        logger.exception("Metadaten-Snapshot für Version %s fehlgeschlagen", version.id)

    # Direkt auf DB-Ebene setzen, ohne save()-Guard auszulösen
    DocumentVersion.objects.filter(pk=version.pk).update(
        is_immutable=True,
        retention_until=retention_until,
    )
    version.is_immutable = True
    version.retention_until = retention_until

    # Retention auch am Dokument speichern (längste Frist gewinnt)
    doc = version.document
    if retention_until and (doc.retention_until is None or retention_until > doc.retention_until):
        from .models import Document
        Document.objects.filter(pk=doc.pk).update(retention_until=retention_until)
        doc.retention_until = retention_until

    AuditLogEntry.objects.create(
        actor=version.created_by,
        action="immutable_set",
        object_type="DocumentVersion",
        object_id=str(version.id),
        detail={"archive_path": archive, "retention_until": str(retention_until)},
    )
