"""Kern-Datenmodell des DMS.

Zentrale Idee (Unterschied zu paperless): **Dokument ≠ Datei**.
Ein `Document` ist ein logisches Objekt mit mehreren `DocumentVersion`s.
Jede Version trägt einen SHA-256-Hash und den Hash der Vorgängerversion
(Hash-Kette) – die Grundlage für Versionierung und spätere Revisionssicherheit.
"""
from django.conf import settings
from django.db import models


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


# ---------------------------------------------------------------------------
# Dokument + Versionen (Kern)
# ---------------------------------------------------------------------------
class Document(models.Model):
    """Logisches Dokument. Die eigentlichen Dateien hängen an DocumentVersion."""

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

    class Meta:
        verbose_name = "Dokument"
        verbose_name_plural = "Dokumente"
        ordering = ["-added_at"]

    def __str__(self) -> str:
        return self.title


class DocumentVersion(models.Model):
    """Eine konkrete Fassung eines Dokuments – der Träger von Datei & Hash-Kette."""

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

    ocr_text = models.TextField(blank=True, help_text="Volltext (später FTS-indiziert)")
    mime_type = models.CharField(max_length=127, blank=True)
    size = models.BigIntegerField(default=0)
    page_count = models.PositiveIntegerField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    is_immutable = models.BooleanField(
        default=False,
        help_text="WORM-Flag – wird in Stufe 4 (Revisionssicherheit) erzwungen",
    )

    class Meta:
        verbose_name = "Dokumentversion"
        verbose_name_plural = "Dokumentversionen"
        ordering = ["document", "version_no"]
        unique_together = ("document", "version_no")

    def __str__(self) -> str:
        return f"{self.document.title} · v{self.version_no}"


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
        default=True, help_text="IMAPS (i. d. R. Port 993). Aus = unverschlüsselt/STARTTLS."
    )
    username = models.CharField(max_length=255)
    folder = models.CharField(max_length=255, default="INBOX")
    password_env = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name der Umgebungsvariable (k8s-Secret) mit dem Passwort – empfohlen.",
    )
    password = models.CharField(
        max_length=255,
        blank=True,
        help_text="Alternativ direkt hinterlegtes App-Passwort (nur ohne Secret-Env).",
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

    def resolve_password(self) -> str:
        """Passwort auflösen: Secret-Env hat Vorrang vor dem DB-Feld."""
        import os

        if self.password_env:
            return os.environ.get(self.password_env, "")
        return self.password


class ProcessedMail(models.Model):
    """Idempotenz-Log bereits verarbeiteter Mails (Dedup über Message-ID)."""

    account = models.ForeignKey(
        MailAccount, on_delete=models.CASCADE, related_name="processed_mails"
    )
    message_id = models.CharField(max_length=998, help_text="RFC-822 Message-ID-Header")
    subject = models.CharField(max_length=512, blank=True)
    sender = models.CharField(max_length=512, blank=True)
    attachment_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Verarbeitete E-Mail"
        verbose_name_plural = "Verarbeitete E-Mails"
        ordering = ["-processed_at"]
        unique_together = ("account", "message_id")

    def __str__(self) -> str:
        return f"{self.subject or '(ohne Betreff)'} · {self.processed_at:%Y-%m-%d %H:%M}"
