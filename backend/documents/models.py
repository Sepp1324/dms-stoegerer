"""Kern-Datenmodell des DMS.

Zentrale Idee (Unterschied zu paperless): **Dokument ≠ Datei**.
Ein `Document` ist ein logisches Objekt mit mehreren `DocumentVersion`s.
Jede Version trägt einen SHA-256-Hash und den Hash der Vorgängerversion
(Hash-Kette) – die Grundlage für Versionierung und spätere Revisionssicherheit.
"""

import hashlib
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class OCRStatus(models.TextChoices):
    """
    Stufe-2 OCR Statusmodell

    WHY:
    - erlaubt Monitoring
    - erlaubt Retry-Logik
    - verhindert "Black Box OCR"
    """

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


# ---------------------------------------------------------------------------
# Metadaten-Stammdaten (wie paperless: frei pflegbar im Admin)
# ---------------------------------------------------------------------------
class Correspondent(models.Model):
    """Wer? Absender/Empfänger, Firma, Behörde …"""

    name = models.CharField(max_length=255, unique=True)

    class Meta:
        verbose_name = "Korrespondent"
        verbose_name_plural = "Korrespondenten"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DocumentType(models.Model):
    """Was? Rechnung, Vertrag, Bescheid …"""

    name = models.CharField(max_length=255, unique=True)
    retention_months = models.PositiveIntegerField(
        default=0,
        help_text="Aufbewahrungsfrist in Monaten (0 = keine Frist)",
    )

    class Meta:
        verbose_name = "Dokumenttyp"
        verbose_name_plural = "Dokumenttypen"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Tag(models.Model):
    """Freie, farbige Schlagworte (hierarchisch über parent möglich)."""

    name = models.CharField(max_length=255)
    color = models.CharField(max_length=7, default="#3B82F6", help_text="Hex-Farbe")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )

    class Meta:
        verbose_name = "Schlagwort"
        verbose_name_plural = "Schlagworte"
        ordering = ["name"]
        unique_together = ("name", "parent")

    def __str__(self) -> str:
        return self.name


class StoragePath(models.Model):
    """Ablage-Regel: wie Dateien auf der Platte strukturiert werden.

    Beispiel-Template: ``archive/{jahr}/{korrespondent}/{titel}.pdf``
    """

    name = models.CharField(max_length=255, unique=True)
    path_template = models.CharField(
        max_length=512,
        default="archive/{jahr}/{korrespondent}/{titel}",
    )

    class Meta:
        verbose_name = "Ablagepfad"
        verbose_name_plural = "Ablagepfade"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DocumentFolder(models.Model):
    """Fachlicher Akten-/Ordnerbaum nach ecoDMS-Vorbild.

    Anders als ``StoragePath`` steuert dieser Ordner nicht den physischen
    Archivpfad auf der Platte, sondern die Nutzer-Navigation: ein Dokument kann
    optional in genau einem Ordner liegen, Ordner können verschachtelt werden.
    """

    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )

    class Meta:
        verbose_name = "Ordner"
        verbose_name_plural = "Ordner"
        ordering = ["parent__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="documents_folder_unique_sibling_name",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(parent__isnull=True),
                name="documents_folder_unique_root_name",
            ),
        ]

    def __str__(self) -> str:
        return self.full_path

    @property
    def full_path(self) -> str:
        parts = [self.name]
        parent = self.parent
        while parent is not None:
            parts.append(parent.name)
            parent = parent.parent
        return " / ".join(reversed(parts))


# ---------------------------------------------------------------------------
# Custom Fields (typisierte Zusatzattribute – ecoDMS-Stärke)
# ---------------------------------------------------------------------------
class CustomField(models.Model):
    """Definition eines typisierten Zusatzfeldes, z. B. 'Rechnungsbetrag'."""

    class DataType(models.TextChoices):
        TEXT = "text", "Text"
        NUMBER = "number", "Zahl"
        DATE = "date", "Datum"
        CURRENCY = "currency", "Währung"
        BOOLEAN = "boolean", "Ja/Nein"

    name = models.CharField(max_length=255, unique=True)
    data_type = models.CharField(max_length=16, choices=DataType.choices)

    class Meta:
        verbose_name = "Zusatzfeld"
        verbose_name_plural = "Zusatzfelder"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_data_type_display()})"


class CustomFieldValue(models.Model):
    """Wert eines Zusatzfeldes an einem konkreten Dokument."""

    document = models.ForeignKey(
        "Document", on_delete=models.CASCADE, related_name="custom_field_values"
    )
    field = models.ForeignKey(CustomField, on_delete=models.CASCADE)
    value = models.TextField(blank=True)

    class Meta:
        verbose_name = "Zusatzfeld-Wert"
        verbose_name_plural = "Zusatzfeld-Werte"
        unique_together = ("document", "field")

    def __str__(self) -> str:
        return f"{self.field.name}={self.value}"


class CaseFile(models.Model):
    """Fachlicher Vorgang, der mehrere Dokumente zu einer Akte bündelt.

    Ordner beantworten „wo liegt es?", ein Vorgang beantwortet „worum geht es?".
    Die Zuordnung ist bewusst fachlich und unabhängig von Ablagepfad/Ordnerbaum:
    Eine Akte kann Dokumente aus unterschiedlichen Ordnern enthalten und eine
    KI-/Heuristik-Zusammenfassung als Arbeitsgedächtnis tragen.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Aktiv"
        WAITING = "waiting", "Wartet"
        DONE = "done", "Erledigt"
        ARCHIVED = "archived", "Archiviert"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="case_files",
    )
    ai_summary = models.TextField(blank=True, default="")
    ai_summary_source = models.CharField(max_length=32, blank=True, default="")
    ai_summary_generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vorgang"
        verbose_name_plural = "Vorgänge"
        ordering = ["status", "-updated_at", "title"]
        indexes = [
            models.Index(fields=["owner", "status"], name="docs_case_owner_status_idx"),
        ]

    def __str__(self) -> str:
        return self.title


# ---------------------------------------------------------------------------
# Dokument + Versionen (Kern)
# ---------------------------------------------------------------------------
class Document(models.Model):
    """Logisches Dokument. Die eigentlichen Dateien hängen an DocumentVersion."""

    class ApprovalStatus(models.TextChoices):
        """Freigabe-Workflow (Stufe 4). Stored Values = deutsche Slugs,
        Python-Konstanten englisch, Labels deutsch. Statuswechsel NUR über
        die Actions submit/approve/reject – nie per PATCH (Serializer read_only).
        """

        ENTWURF = "entwurf", "Entwurf"
        ZUR_FREIGABE = "zur_freigabe", "Zur Freigabe"
        FREIGEGEBEN = "freigegeben", "Freigegeben"
        ABGELEHNT = "abgelehnt", "Abgelehnt"

    class ReviewStatus(models.TextChoices):
        """Fachliche Inbox-Prüfung nach erfolgreicher Verarbeitung.

        ``processing_state`` sagt, ob die Pipeline technisch fertig ist.
        ``review_status`` sagt, ob ein Mensch die Metadaten/Einordnung schon
        bestätigt hat. Bewusst getrennt, damit Reprocessing kein Review
        implizit simuliert.
        """

        NEEDS_REVIEW = "needs_review", "Zu prüfen"
        REVIEWED = "reviewed", "Geprüft"

    title = models.CharField(max_length=512)
    created_at = models.DateTimeField(
        help_text="Datum des Dokuments selbst (z. B. Rechnungsdatum)",
        null=True,
        blank=True,
    )
    added_at = models.DateTimeField(auto_now_add=True, help_text="Aufnahme ins DMS")

    correspondent = models.ForeignKey(
        Correspondent, null=True, blank=True, on_delete=models.SET_NULL
    )
    document_type = models.ForeignKey(
        DocumentType, null=True, blank=True, on_delete=models.SET_NULL
    )
    storage_path = models.ForeignKey(
        StoragePath, null=True, blank=True, on_delete=models.SET_NULL
    )
    folder = models.ForeignKey(
        DocumentFolder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
        help_text="Fachlicher Ordner/Akte für die UI-Navigation.",
    )
    case_file = models.ForeignKey(
        CaseFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
        help_text="Fachlicher Vorgang/Akte, dem das Dokument zugeordnet ist.",
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="documents")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_documents",
    )

    current_version = models.OneToOneField(
        "DocumentVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    retention_until = models.DateField(
        null=True,
        blank=True,
        help_text="Löschen gesperrt bis zu diesem Datum (aus DocumentType.retention_months berechnet)",
    )

    # KI-Metadatenvorschläge (nach OCR erzeugt) – zum Bestätigen durch den Nutzer,
    # nicht bindend. z. B. {"title": "...", "document_type": "Rechnung",
    # "correspondent": "Stadtwerke", "tags": ["Finanzen"], "summary": "..."}
    ai_suggestions = models.JSONField(default=dict, blank=True)
    ai_suggested_at = models.DateTimeField(null=True, blank=True)

    # Nachvollziehbarkeit der regelbasierten Klassifizierung (erklärbar):
    # {"rules": ["Rechnungen"], "applied": {"document_type": "Rechnung", "tags": ["Finanzen"]}}
    classification = models.JSONField(default=dict, blank=True)

    # Herkunfts-Metadaten aus der E-Mail-Ingestion (IMAP): Betreff und Absender
    # der Quell-Mail. Für Nicht-Mail-Dokumente leer. Die Rule-Engine nutzt sie
    # für ``subject_contains``/``from_contains`` (siehe classification.py).
    mail_subject = models.CharField(max_length=512, blank=True, default="")
    mail_sender = models.CharField(max_length=512, blank=True, default="")

    # Freigabe-Workflow (Stufe 4). Bestand via Spalten-Default in Migration
    # 0007_document_status auf "entwurf" gesetzt. Statuswechsel NUR über die
    # Actions submit/approve/reject (Serializer read_only), nie per PATCH.
    status = models.CharField(
        max_length=16,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.ENTWURF,
    )
    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.NEEDS_REVIEW,
        db_index=True,
        help_text="Fachlicher Inbox-Status: Metadaten/Einordnung geprüft oder offen.",
    )

    # Archive Serial Number (STOAA-284/285): dauerhafte, unveränderliche Identität
    # des logischen Dokuments – gehört zum Document, nie zu einer Version, bleibt
    # über alle Versionen identisch, wird nie geändert oder wiederverwendet. Die
    # eigentliche (transaktionssichere, lückenlose) Vergabe-Logik lebt im Service
    # ``documents.services.asn``; ``save()`` ruft ihn nur als Invarianten-Absicherung
    # auf, damit JEDER Erstellungspfad garantiert genau eine ASN erhält.
    asn = models.PositiveBigIntegerField(unique=True, editable=False, db_index=True)

    class Meta:
        verbose_name = "Dokument"
        verbose_name_plural = "Dokumente"
        ordering = ["-added_at"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        """Sichert die ASN-Invariante ab: jedes neue Dokument erhält genau eine ASN.

        Die Vergabe (transaktionssicher, per ``select_for_update``) delegiert
        vollständig an den Service ``documents.services.asn`` – hier steht bewusst
        keine Businesslogik, nur der Aufruf, der die Invariante an der niedrigsten
        Ebene erzwingt (unabhängig vom Erstellungspfad: Upload, Mail, Consume,
        direktes POST). Eine einmal vergebene ASN wird nie überschrieben.
        """
        if self._state.adding and not self.asn:
            from documents.services.asn import assign_asn

            assign_asn(self)
        super().save(*args, **kwargs)


class DocumentVersion(models.Model):
    """Eine konkrete Fassung eines Dokuments – der Träger von Datei & Hash-Kette."""

    class ProcessingState(models.TextChoices):
        """Fachliche State Machine der asynchronen Dokumentverarbeitung.

        ``ocr_status`` bleibt das technische Detail-Monitoring des OCR-Schritts.
        ``processing_state`` beschreibt dagegen den gesamten DMS-Fluss, den UI,
        Audit und Betrieb gemeinsam verstehen sollen.
        """

        UPLOADED = "uploaded", "Uploaded"
        HASHED = "hashed", "Hashed"
        OCR_RUNNING = "ocr_running", "OCR running"
        OCR_DONE = "ocr_done", "OCR done"
        CLASSIFICATION_RUNNING = "classification_running", "Classification running"
        CLASSIFIED = "classified", "Classified"
        THUMBNAIL_DONE = "thumbnail_done", "Thumbnail done"
        SEALED = "sealed", "Sealed"
        READY = "ready", "Ready"
        # Fehler-/Retry-Layer (STOAA-228): bewusst NICHT als Vorwärtsziele in
        # PROCESSING_TRANSITIONS – die lineare Erfolgs-Map bleibt lesbar. Die
        # Übergänge in/aus diesen States laufen über mark_processing_failed /
        # begin_retry bzw. pipeline.retry_version.
        FAILED = "failed", "Failed"
        RETRY_PENDING = "retry_pending", "Retry pending"

    PROCESSING_TRANSITIONS = {
        ProcessingState.UPLOADED: {ProcessingState.HASHED},
        ProcessingState.HASHED: {ProcessingState.OCR_RUNNING},
        ProcessingState.OCR_RUNNING: {ProcessingState.OCR_DONE},
        ProcessingState.OCR_DONE: {ProcessingState.CLASSIFICATION_RUNNING},
        ProcessingState.CLASSIFICATION_RUNNING: {ProcessingState.CLASSIFIED},
        ProcessingState.CLASSIFIED: {ProcessingState.THUMBNAIL_DONE},
        ProcessingState.THUMBNAIL_DONE: {ProcessingState.SEALED},
        ProcessingState.SEALED: {ProcessingState.READY},
        ProcessingState.READY: set(),
    }

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="versions"
    )
    version_no = models.PositiveIntegerField()

    file_path = models.CharField(max_length=1024, help_text="Original auf der Platte")
    archive_path = models.CharField(
        max_length=1024, blank=True, help_text="OCR'tes PDF/A"
    )
    thumbnail_path = models.CharField(
        max_length=1024, blank=True, help_text="Miniaturbild der ersten Seite (JPEG)"
    )

    sha256 = models.CharField(max_length=64, help_text="Integritäts-Hash der Datei")
    prev_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="sha256 der Vorgängerversion – bildet die Hash-Kette",
    )
    processing_state = models.CharField(
        max_length=32,
        choices=ProcessingState.choices,
        default=ProcessingState.UPLOADED,
        db_index=True,
        help_text="State Machine der Dokumentverarbeitung (uploaded → ready)",
    )

    # Fehler-/Retry-Layer (STOAA-228) – gehört zur technischen Verarbeitung,
    # NICHT zur fachlichen Freigabe (Document.status). Read-only für die UI.
    processing_error = models.TextField(blank=True, default="")
    processing_failed_step = models.CharField(max_length=40, blank=True, default="")
    processing_failed_at = models.DateTimeField(null=True, blank=True)
    processing_attempts = models.PositiveIntegerField(default=0)

    # Ingest-Quelle für die Workflow-Engine (STOAA-263)
    ingest_source = models.CharField(
        max_length=16,
        blank=True,
        default="upload",
        help_text="upload | consume | mail | api | paperless_import | mobile",
    )

    ocr_status = models.CharField(
        max_length=20,
        choices=OCRStatus.choices,
        default=OCRStatus.PENDING,
        db_index=True,
    )

    ocr_error = models.TextField(blank=True)

    ocr_engine = models.CharField(
        max_length=30,
        default="ocrmypdf",
    )

    ocr_duration_ms = models.PositiveIntegerField(default=0)

    ocr_text = models.TextField(blank=True)

    ocr_started_at = models.DateTimeField(null=True, blank=True)
    ocr_finished_at = models.DateTimeField(null=True, blank=True)

    mime_type = models.CharField(max_length=127, blank=True)
    size = models.BigIntegerField(default=0)
    page_count = models.PositiveIntegerField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    is_immutable = models.BooleanField(
        default=False,
        help_text="WORM-Flag – nach erfolgreichem process_version() gesetzt",
    )

    retention_until = models.DateField(
        null=True,
        blank=True,
        help_text="Löschen gesperrt bis zu diesem Datum",
    )

    # Versionsvergleich Stufe 2 (STOAA-312, Option A aus STOAA-292): beim Sealing
    # wird ein deterministischer JSON-Snapshot der Metadaten/Tags/Custom-Fields auf
    # die Version geschrieben (write-once, WORM). ``seal_hash`` bindet den Snapshot
    # kanonisch an die Datei-/prev_hash-Siegelkette – Manipulation an eingefrorenen
    # Metadaten wird damit erkennbar. Ältere (Stufe-1-)Versionen bleiben ``null``
    # ('nicht verfügbar', GoBD – keine erfundenen historischen Zustände).
    metadata_snapshot = models.JSONField(
        null=True,
        blank=True,
        help_text="Eingefrorener Metadaten-/Tag-/Custom-Field-Stand beim Sealing",
    )
    snapshot_schema_version = models.PositiveSmallIntegerField(
        default=0,
        help_text="Schema-Version des metadata_snapshot (0 = nicht vorhanden)",
    )
    snapshot_taken_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Erfassungszeitpunkt des Snapshots (Sealing bzw. Backfill)",
    )
    seal_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="sha256(sha256 · prev_hash · Snapshot-Bytes) – Metadaten-Siegel",
    )

    class Meta:
        verbose_name = "Dokumentversion"
        verbose_name_plural = "Dokumentversionen"
        ordering = ["document", "version_no"]
        unique_together = ("document", "version_no")

    def __str__(self) -> str:
        return f"{self.document.title} · v{self.version_no}"

    def transition_to(self, new_state: str, *, actor=None, detail: dict | None = None) -> None:
        """Wechselt streng kontrolliert in den nächsten Verarbeitungszustand.

        Der Wechsel läuft bewusst über ``QuerySet.update``: Nach ``SEALED`` ist
        die Version WORM-geschützt und ``save()`` darf nicht mehr funktionieren.
        Die State Machine selbst bleibt trotzdem berechtigt, den letzten Schritt
        ``SEALED → READY`` auditierbar zu setzen.
        """
        old_state = self.processing_state
        allowed = self.PROCESSING_TRANSITIONS.get(old_state, set())
        if new_state not in allowed:
            raise ValidationError(
                f"Ungültiger Verarbeitungsübergang: {old_state} → {new_state}"
            )

        DocumentVersion.objects.filter(pk=self.pk).update(processing_state=new_state)
        self.processing_state = new_state

        AuditLogEntry.objects.create(
            actor=actor,
            action="processing_state",
            object_type="DocumentVersion",
            object_id=str(self.id),
            detail={"from": old_state, "to": new_state, **(detail or {})},
        )

    def mark_processing_failed(self, *, step, error, actor=None) -> None:
        """Markiert die Version als fehlgeschlagen (Fehler-Layer, STOAA-228).

        WORM/READY werden NIE auf FAILED gesetzt – eine gesiegelte oder final
        freigegebene Version bleibt unangetastet. Der Schreibvorgang läuft wie
        ``transition_to`` bewusst über ``QuerySet.update``, um den WORM-``save``-
        Guard zu umgehen, und die lokalen Attribute werden nachgezogen.
        """
        if self.is_immutable or self.processing_state in {
            self.ProcessingState.SEALED,
            self.ProcessingState.READY,
        }:
            raise ValidationError(
                "Gesiegelte/READY-Version kann nicht auf FAILED gesetzt werden."
            )

        old_state = self.processing_state
        failed_at = timezone.now()
        error_text = str(error)[:4000]
        DocumentVersion.objects.filter(pk=self.pk).update(
            processing_state=self.ProcessingState.FAILED,
            processing_error=error_text,
            processing_failed_step=step,
            processing_failed_at=failed_at,
        )
        self.processing_state = self.ProcessingState.FAILED
        self.processing_error = error_text
        self.processing_failed_step = step
        self.processing_failed_at = failed_at

        AuditLogEntry.objects.create(
            actor=actor,
            action="processing_failed",
            object_type="DocumentVersion",
            object_id=str(self.id),
            detail={"from": old_state, "step": step, "error": str(error)[:1000]},
        )

    def begin_retry(self, *, actor=None) -> None:
        """Startet einen Retry aus dem FAILED-Zustand (Retry-Layer, STOAA-228).

        Setzt ``processing_state`` auf RETRY_PENDING und zählt ``processing_attempts``
        hoch. Der eigentliche Wiedereinstieg (Vorbedingung setzen + Pipeline ab
        dem fehlgeschlagenen Schritt) übernimmt ``pipeline.retry_version``.
        """
        if self.processing_state != self.ProcessingState.FAILED:
            raise ValidationError("Retry ist nur aus dem Zustand FAILED möglich.")
        if self.is_immutable or self.processing_state in {
            self.ProcessingState.SEALED,
            self.ProcessingState.READY,
        }:
            raise ValidationError(
                "Gesiegelte/READY-Version kann nicht erneut verarbeitet werden."
            )

        DocumentVersion.objects.filter(pk=self.pk).update(
            processing_state=self.ProcessingState.RETRY_PENDING,
            processing_attempts=models.F("processing_attempts") + 1,
        )
        self.refresh_from_db(fields=["processing_state", "processing_attempts"])

        AuditLogEntry.objects.create(
            actor=actor,
            action="processing_retry",
            object_type="DocumentVersion",
            object_id=str(self.id),
            detail={"attempt": self.processing_attempts, "step": self.processing_failed_step},
        )

    def save(self, *args, **kwargs):
        if self.pk:
            original = (
                DocumentVersion.objects.filter(pk=self.pk)
                .values("is_immutable")
                .first()
            )
            if original and original["is_immutable"]:
                from .audit import log_immutable_block

                log_immutable_block("DocumentVersion", self.pk)
                raise ValidationError(
                    "Diese Version ist unveränderlich (WORM) und kann nicht überschrieben werden."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_immutable:
            from .audit import log_immutable_block

            log_immutable_block("DocumentVersion", self.pk)
            raise ValidationError(
                "Diese Version ist unveränderlich (WORM) und kann nicht gelöscht werden."
            )
        today = timezone.now().date()
        if self.retention_until and today < self.retention_until:
            from .audit import log_retention_block

            log_retention_block("DocumentVersion", self.pk, self.retention_until)
            raise ValidationError(
                f"Aufbewahrungsfrist läuft bis {self.retention_until} – Löschen gesperrt."
            )
        super().delete(*args, **kwargs)


class DocumentPageText(models.Model):
    """Seitengenauer OCR-/Text-Index einer Dokumentversion.

    ``DocumentVersion.ocr_text`` bleibt der vollständige Text für bestehende
    Suche/Kompatibilität; dieses Modell macht Quellen im Copilot prüfbar bis
    auf Seitenebene.
    """

    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="page_texts"
    )
    page_no = models.PositiveIntegerField()
    text = models.TextField(blank=True)

    class Meta:
        verbose_name = "Seitentext"
        verbose_name_plural = "Seitentexte"
        ordering = ["version_id", "page_no"]
        unique_together = ("version", "page_no")

    def __str__(self) -> str:
        return f"{self.version} Seite {self.page_no}"


class ExtractionCandidate(models.Model):
    """Smart-Inbox-Vorschlag für ein extrahiertes Strukturdatum."""

    class Field(models.TextChoices):
        DOCUMENT_DATE = "document_date", "Belegdatum"
        AMOUNT = "amount", "Betrag"
        IBAN = "iban", "IBAN"
        CONTRACT_NUMBER = "contract_number", "Vertragsnummer"
        POLICY_NUMBER = "policy_number", "Versicherungsnummer"

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        APPLIED = "applied", "Übernommen"
        DISMISSED = "dismissed", "Verworfen"

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="extraction_candidates"
    )
    field = models.CharField(max_length=40, choices=Field.choices)
    value = models.CharField(max_length=512)
    normalized_value = models.CharField(max_length=512, blank=True)
    confidence = models.PositiveSmallIntegerField(default=50)
    reason = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=32, default="heuristic")
    source_page = models.PositiveIntegerField(null=True, blank=True)
    source_snippet = models.TextField(blank=True)
    source_snippet_html = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Extraktionsvorschlag"
        verbose_name_plural = "Extraktionsvorschläge"
        ordering = ["document_id", "field", "-confidence", "source_page"]
        indexes = [
            models.Index(
                fields=["document", "status"],
                name="documents_e_documen_9deba9_idx",
            ),
            models.Index(
                fields=["field", "status"],
                name="documents_e_field_e2f4ab_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_field_display()}: {self.value}"


class CaseFileCandidate(models.Model):
    """Vorschlag, ein Dokument einer Vorgangsakte zuzuordnen.

    Der Akten-Autopilot arbeitet wie die Smart Inbox: Er schreibt keine
    fachliche Änderung still ins Dokument, sondern legt erklärbare Kandidaten
    mit Score und Signalen an. Erst die explizite Nutzeraktion übernimmt oder
    verwirft den Vorschlag.
    """

    class Kind(models.TextChoices):
        EXISTING_CASE = "existing_case", "Bestehende Akte"
        NEW_CASE = "new_case", "Neue Akte"

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        APPLIED = "applied", "Übernommen"
        DISMISSED = "dismissed", "Verworfen"

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="case_file_candidates"
    )
    case_file = models.ForeignKey(
        CaseFile,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="candidates",
        help_text="Zielakte bei Vorschlägen auf eine bestehende Akte.",
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    suggested_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Titelvorschlag, wenn kind=new_case ist.",
    )
    signature = models.CharField(
        max_length=128,
        help_text="Idempotenz-Schlüssel pro Dokument; verhindert wiederkehrende Duplikate.",
    )
    score = models.PositiveSmallIntegerField(default=50)
    reason = models.CharField(max_length=255, blank=True)
    signals = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=32, default="heuristic")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Aktenvorschlag"
        verbose_name_plural = "Aktenvorschläge"
        ordering = ["document_id", "status", "-score", "-created_at"]
        unique_together = ("document", "signature")
        indexes = [
            models.Index(fields=["document", "status"], name="docs_casecand_doc_status"),
            models.Index(fields=["case_file", "status"], name="docs_casecand_case_stat"),
        ]

    def __str__(self) -> str:
        target = self.case_file.title if self.case_file_id else self.suggested_title
        return f"{self.document_id} → {target} ({self.score}%)"


# ---------------------------------------------------------------------------
# Regelbasierte Klassifizierung (ecoDMS-artige Vorlage – deterministisch)
# ---------------------------------------------------------------------------
class ClassificationRule(models.Model):
    """Wenn Bedingungen zutreffen, setze Metadaten – nachvollziehbar & erklärbar."""

    name = models.CharField(max_length=255)
    priority = models.IntegerField(default=100, help_text="Kleiner = zuerst geprüft")
    enabled = models.BooleanField(default=True)

    # Bedingungen und Zuweisungen bewusst als JSON – flexibel, ohne Schema-Migrationen.
    # match: z. B. {"text_contains": "Rechnung", "correspondent": "Stadtwerke"}
    match = models.JSONField(default=dict, blank=True)
    # then: z. B. {"document_type": "Rechnung", "tags": ["Finanzen"]}
    then = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Klassifizierungsregel"
        verbose_name_plural = "Klassifizierungsregeln"
        ordering = ["priority", "name"]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Audit-Trail (append-only – ab Tag 1, billig & hoher Nutzen)
# ---------------------------------------------------------------------------
class AuditLogEntry(models.Model):
    """Lückenloses Protokoll relevanter Aktionen."""

    timestamp = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=64, help_text="z. B. create, update, delete")
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Audit-Eintrag"
        verbose_name_plural = "Audit-Log"
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.action} {self.object_type}#{self.object_id}"


# ---------------------------------------------------------------------------
# E-Mail-Ingestion (Stufe 3) – IMAP-Postfach + Idempotenz-Log
# ---------------------------------------------------------------------------
class MailAccount(models.Model):
    """IMAP-Postfach, dessen Anhänge periodisch ins DMS gezogen werden.

    Sicherheit: Das Passwort gehört **nicht** in Git. Bevorzugt wird es aus
    einem k8s-Secret über eine Umgebungsvariable (``password_env``) bezogen;
    ``password`` ist nur der Fallback für lokale Entwicklung.

    Eigentümer-Zuordnung (Kohärenz mit der Owner-Isolation, STOAA-7): Der
    ``owner`` ist der Standard-Empfänger dieses Postfachs. Er wird beim Import
    an jedes eingespeiste Dokument durchgereicht (``Document.owner`` /
    ``AuditLogEntry.actor``), damit die für Nicht-Admins geltende Isolation
    (``qs.filter(owner=user)``) die Mail-Dokumente **sichtbar und abrufbar**
    macht. Bleibt ``owner`` leer, ist das Postfach ein bewusstes
    **Admin-Triage-Postfach**: Dokumente ohne Eigentümer sind ausschließlich
    für Nutzer mit ``is_dms_admin`` sichtbar und müssen dort manuell zugeordnet
    werden. Der Leer-Fall ist damit dokumentierte Absicht, nicht Zufall.
    """

    name = models.CharField(max_length=255, help_text="Bezeichnung, z. B. 'Rechnungen'")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mail_accounts",
        help_text=(
            "Standard-Empfänger: Eigentümer der aus diesem Postfach importierten "
            "Dokumente. Leer lassen = Admin-Triage-Postfach (nur für DMS-Admins "
            "sichtbar, bis manuell zugeordnet)."
        ),
    )
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=993)
    use_ssl = models.BooleanField(
        default=True,
        help_text="IMAPS (i. d. R. Port 993). Aus = unverschlüsselt/STARTTLS.",
    )
    username = models.CharField(max_length=255)
    folder = models.CharField(max_length=255, default="INBOX")
    password_env = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name der Umgebungsvariable (k8s-Secret) mit dem Passwort – empfohlen.",
    )
    password = models.TextField(
        blank=True,
        help_text=(
            "Alternativ direkt hinterlegtes App-Passwort (nur ohne Secret-Env). "
            "Wird beim Speichern verschlüsselt (Fernet, siehe crypto.py) – niemals "
            "im Klartext in der DB und niemals über die API ausgegeben."
        ),
    )
    enabled = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "E-Mail-Konto"
        verbose_name_plural = "E-Mail-Konten"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} <{self.username}@{self.host}>"

    def save(self, *args, **kwargs):
        """Passwort at-rest verschlüsseln (idempotent).

        Bereits verschlüsselte Werte (z. B. ein unverändert aus der DB geladenes
        Objekt) werden nicht doppelt verschlüsselt – ``is_encrypted`` erkennt sie.
        """
        from .crypto import encrypt_secret, is_encrypted

        if self.password and not is_encrypted(self.password):
            self.password = encrypt_secret(self.password)
        super().save(*args, **kwargs)

    def resolve_password(self) -> str:
        """Passwort auflösen: Secret-Env hat Vorrang vor dem entschlüsselten DB-Feld."""
        import os

        from .crypto import decrypt_secret

        if self.password_env:
            return os.environ.get(self.password_env, "")
        return decrypt_secret(self.password)


class ProcessedMail(models.Model):
    """Idempotenz-Log bereits verarbeiteter Mails (Dedup über Message-ID)."""

    class Status(models.TextChoices):
        IMPORTED = "imported", "Importiert"
        PARTIAL = "partial", "Teilweise importiert"
        IGNORED = "ignored", "Ignoriert"
        FAILED = "failed", "Fehlerhaft"

    account = models.ForeignKey(
        MailAccount, on_delete=models.CASCADE, related_name="processed_mails"
    )
    message_id = models.CharField(max_length=998, help_text="RFC-822 Message-ID-Header")
    subject = models.CharField(max_length=512, blank=True)
    sender = models.CharField(max_length=512, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.IMPORTED,
        db_index=True,
    )
    attachment_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    attachment_names = models.JSONField(default=list, blank=True)
    documents = models.ManyToManyField(
        Document,
        blank=True,
        related_name="source_mails",
        help_text="Dokumente, die aus Anhängen dieser Mail entstanden sind.",
    )
    note = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Verarbeitete E-Mail"
        verbose_name_plural = "Verarbeitete E-Mails"
        ordering = ["-processed_at"]
        unique_together = ("account", "message_id")
        indexes = [
            models.Index(fields=["account", "status"], name="docs_mail_account_status"),
            models.Index(fields=["status", "-processed_at"], name="docs_mail_status_time"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.subject or '(ohne Betreff)'} · {self.processed_at:%Y-%m-%d %H:%M}"
        )


# ---------------------------------------------------------------------------
# Freigabelinks (STOAA-96/STOAA-190) – tokenbasierter Dokument-Zugriff
# ---------------------------------------------------------------------------
class DocumentShareLink(models.Model):
    """Ein widerrufbarer, ablaufpflichtiger Freigabelink auf ein Dokument.

    Sicherheitsmodell (Login-PFLICHT-Variante):
      * **Nur der SHA-256-Hash** des Tokens wird gespeichert (``token_hash``),
        nie der Klartext. Der Klartext-Token wird ausschließlich **einmalig**
        bei der Erstellung zurückgegeben und ist danach nicht wieder abrufbar –
        selbst bei DB-Leak lässt sich aus dem Hash kein gültiger Link ableiten.
      * ``expires_at`` ist **Pflicht** (NOT NULL): ein Freigabelink gilt nie
        unbegrenzt. Die serverseitige Zukunftsprüfung erfolgt in der API.
      * Widerruf über ``revoked_at`` (Soft-Delete): ein widerrufener Link bleibt
        für den Verlauf sichtbar, ist aber sofort ``is_valid == False``.

    Die eigentlichen Abrufrouten (``/api/share/<token>/…``) sind NICHT Teil
    dieses Modells/Tickets (→ Ticket B); hier entsteht nur das Fundament
    (Model + Verwaltungs-API).
    """

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="share_links"
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,  # unique impliziert bereits einen Index (Lookup beim Abruf)
        help_text=(
            "SHA-256-Hex des Freigabe-Tokens. NUR der Hash wird gespeichert, "
            "nie der Klartext."
        ),
    )
    expires_at = models.DateTimeField(
        help_text="Pflicht-Ablauf – ein Freigabelink gilt nie unbegrenzt."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_share_links",
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Zeitpunkt des Widerrufs (Soft-Delete); gesetzt → is_valid=False.",
    )

    class Meta:
        verbose_name = "Freigabelink"
        verbose_name_plural = "Freigabelinks"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Freigabelink für Dokument #{self.document_id} (…{self.token_hash[:8]})"

    @staticmethod
    def hash_token(token: str) -> str:
        """Bildet den zu speichernden SHA-256-Hex-Hash eines Klartext-Tokens."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_valid(self) -> bool:
        """Nutzbar = weder widerrufen noch abgelaufen."""
        return self.revoked_at is None and not self.is_expired


# ---------------------------------------------------------------------------
# ASN (Archive Serial Number, STOAA-284/285)
# ---------------------------------------------------------------------------
class ASNCounter(models.Model):
    """Singleton-Zähler für die lückenlose, transaktionssichere ASN-Vergabe.

    Genau eine Zeile (``pk=1``). Die Vergabe sperrt sie per ``select_for_update``
    und erhöht ``last_value`` – dadurch serialisieren sich parallele Vergaben und
    es entstehen weder Doppelvergaben noch Race Conditions. Bewusst **nicht** über
    ``Document.objects.count()+1`` oder die Datenbank-ID (siehe Service).
    """

    last_value = models.PositiveBigIntegerField(
        default=0,
        help_text="Zuletzt vergebene ASN. Die nächste Vergabe liefert last_value + 1.",
    )

    class Meta:
        verbose_name = "ASN-Zähler"
        verbose_name_plural = "ASN-Zähler"

    def __str__(self) -> str:
        return f"ASN-Zähler (last_value={self.last_value})"


class ASNScan(models.Model):
    """Import-Historie einer ASN-Erkennung (Erweiterung gegenüber paperless).

    Dokumentiert nachvollziehbar, wann und wodurch eine ASN erkannt wurde und
    welche Version dadurch entstanden ist (z. B. beim erneuten Scan eines
    Papierdokuments mit ASN-Etikett).
    """

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="asn_scans"
    )
    version = models.ForeignKey(
        DocumentVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="asn_scans",
    )
    scanned_at = models.DateTimeField(auto_now_add=True)
    matched_by = models.CharField(
        max_length=64,
        help_text="z. B. OCR, QR, Barcode",
    )
    confidence = models.FloatField(
        default=1.0,
        help_text="OCR-Erkennungswahrscheinlichkeit",
    )

    class Meta:
        verbose_name = "ASN-Scan"
        verbose_name_plural = "ASN-Scans"
        ordering = ["-scanned_at"]

    def __str__(self) -> str:
        return f"ASN-Scan Dok#{self.document_id} via {self.matched_by} @ {self.scanned_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Workflow-Engine (STOAA-263) – Trigger → Bedingungen → Aktionen
# ---------------------------------------------------------------------------
class Workflow(models.Model):
    """Geordnete Regel: Trigger + Bedingungen → Aktionsliste."""

    name = models.CharField(max_length=255)
    order = models.IntegerField(default=100, help_text="Kleiner = früher ausgeführt")
    enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Workflow"
        verbose_name_plural = "Workflows"
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name


class WorkflowTrigger(models.Model):
    """Wann und unter welchen Bedingungen ein Workflow feuert."""

    class TriggerType(models.TextChoices):
        DOCUMENT_ADDED = "document_added", "Dokument hinzugefügt"
        DOCUMENT_UPDATED = "document_updated", "Dokument aktualisiert"

    workflow = models.OneToOneField(
        Workflow, on_delete=models.CASCADE, related_name="trigger"
    )
    trigger_type = models.CharField(
        max_length=32,
        choices=TriggerType.choices,
        default=TriggerType.DOCUMENT_ADDED,
    )

    # Quell-Filter (Mehrfachauswahl als kommagetrennte Liste, leer = alle)
    # Werte: upload, consume, mail, api
    sources = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Kommagetrennte Liste: upload,consume,mail,api – leer = alle",
    )

    # Optionale Bedingungen
    filter_path = models.CharField(
        max_length=512, blank=True, default="",
        help_text="Glob gegen den Dateipfad der Version (optional)",
    )
    filter_correspondent = models.ForeignKey(
        Correspondent, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    filter_document_type = models.ForeignKey(
        DocumentType, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    filter_has_tags = models.ManyToManyField(
        Tag, blank=True, related_name="trigger_has",
        help_text="Dokument muss ALLE diese Tags haben",
    )
    filter_has_not_tags = models.ManyToManyField(
        Tag, blank=True, related_name="trigger_has_not",
        help_text="Dokument darf KEINEN dieser Tags haben",
    )
    # Textbedingungen (nutzen rule_matches-Logik aus classification.py)
    filter_text_contains = models.CharField(max_length=512, blank=True, default="")
    filter_text_regex = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        verbose_name = "Workflow-Trigger"
        verbose_name_plural = "Workflow-Trigger"

    def __str__(self) -> str:
        return f"Trigger[{self.trigger_type}] für {self.workflow}"


class WorkflowAction(models.Model):
    """Eine Aktion, die ein Workflow in gegebener Reihenfolge ausführt."""

    class ActionType(models.TextChoices):
        ASSIGN = "assign", "Zuweisen"
        REMOVE = "remove", "Entfernen"

    workflow = models.ForeignKey(
        Workflow, on_delete=models.CASCADE, related_name="actions"
    )
    order = models.IntegerField(default=10)
    action_type = models.CharField(
        max_length=16, choices=ActionType.choices, default=ActionType.ASSIGN
    )

    # Felder für action_type=assign
    assign_title = models.CharField(
        max_length=512, blank=True, default="",
        help_text="Titel-Template: {correspondent}, {created}, {doc_type} erlaubt",
    )
    assign_correspondent = models.ForeignKey(
        Correspondent, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    assign_document_type = models.ForeignKey(
        DocumentType, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    assign_storage_path = models.ForeignKey(
        StoragePath, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    assign_tags = models.ManyToManyField(
        Tag, blank=True, related_name="action_assign",
        help_text="Tags ergänzen",
    )
    assign_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    # Benutzerdefinierte Feldwerte als JSON: {"field_id": wert, ...}
    assign_custom_fields = models.JSONField(default=dict, blank=True)

    # Felder für action_type=remove
    remove_tags = models.ManyToManyField(
        Tag, blank=True, related_name="action_remove",
        help_text="Tags entfernen",
    )

    class Meta:
        verbose_name = "Workflow-Aktion"
        verbose_name_plural = "Workflow-Aktionen"
        ordering = ["order"]

    def __str__(self) -> str:
        return f"Aktion[{self.action_type}] #{self.order} für {self.workflow}"


# ---------------------------------------------------------------------------
# Wiedervorlage / Erinnerungen (STOAA-369 / STOAA-372 PR1)
# ---------------------------------------------------------------------------
class DocumentReminder(models.Model):
    """Fällig-Datum (Wiedervorlage) je Dokument mit optionaler Notiz.

    CEO-Entscheidung (STOAA-369): KEIN separates Notification-Modell. Die
    In-App-Benachrichtigung ist schlicht die fällig/anstehend-Liste
    (``DocumentReminderViewSet.due``). Der tägliche Beat ``check_due_reminders``
    setzt lediglich ``notified_at`` **genau einmal** (Tages-Dedupe) und versendet
    nur dann eine E-Mail, wenn SMTP konfiguriert ist – fehlt es, wird still
    übersprungen (kein Fehler).
    """

    document = models.ForeignKey(
        "Document", on_delete=models.CASCADE, related_name="reminders"
    )
    remind_on = models.DateField(help_text="Fällig-/Wiedervorlage-Datum")
    note = models.TextField(blank=True, help_text="Optionale Notiz zur Wiedervorlage")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_reminders",
    )
    done = models.BooleanField(
        default=False, help_text="Erledigt – aus der Wiedervorlage-Liste genommen"
    )
    notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Wann der Beat diese fällige Erinnerung erstmals benachrichtigt hat "
            "(genau einmal gesetzt – Dedupe gegen Mehrfach-Benachrichtigung)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Erinnerung"
        verbose_name_plural = "Erinnerungen"
        ordering = ["remind_on"]

    def __str__(self) -> str:
        return f"Erinnerung {self.remind_on} für Dokument #{self.document_id}"


# ---------------------------------------------------------------------------
# Betriebsmonitoring
# ---------------------------------------------------------------------------
class BackupMonitor(models.Model):
    """Letzter bekannter Zustand von Backup-CronJob und Restore-Drill.

    Der Cluster schreibt diese Werte aktiv aus Backup-Job/Restore-Drill heraus.
    Das Backend muss dafür keine Kubernetes-API-Rechte besitzen und kann trotzdem
    in UI/Admin zuverlässig anzeigen, ob Backups still kaputtgehen.
    """

    class Kind(models.TextChoices):
        BACKUP = "backup", "Backup"
        RESTORE_DRILL = "restore_drill", "Restore-Drill"

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Unbekannt"
        RUNNING = "running", "Läuft"
        SUCCESS = "success", "Erfolgreich"
        FAILED = "failed", "Fehlgeschlagen"

    kind = models.CharField(max_length=32, choices=Kind.choices, unique=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.UNKNOWN
    )
    artifact_timestamp = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Backup-Zeitstempel wie 20260706-084501.",
    )
    message = models.TextField(blank=True, default="")
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    size_bytes = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Größe des letzten Backup-Artefakts in Bytes.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Backup-Monitor"
        verbose_name_plural = "Backup-Monitoring"
        ordering = ["kind"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.get_status_display()}"


class BackupRun(models.Model):
    """Historie einzelner Backup-/Restore-Drill-Läufe (für Verlauf/Trend).

    Während ``BackupMonitor`` genau eine Zeile je ``kind`` hält (letzter Zustand),
    speichert dieses Modell je Lauf einen Eintrag. Wird beim terminalen Status
    (success/failed) aus ``record_backup_status`` angelegt.
    """

    kind = models.CharField(max_length=32, choices=BackupMonitor.Kind.choices)
    status = models.CharField(max_length=16, choices=BackupMonitor.Status.choices)
    artifact_timestamp = models.CharField(max_length=32, blank=True, default="")
    size_bytes = models.BigIntegerField(null=True, blank=True)
    message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Backup-Lauf"
        verbose_name_plural = "Backup-Läufe"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.status} @ {self.created_at:%Y-%m-%d %H:%M}"
