"""Kern-Datenmodell des DMS.

Zentrale Idee (Unterschied zu paperless): **Dokument ≠ Datei**.
Ein `Document` ist ein logisches Objekt mit mehreren `DocumentVersion`s.
Jede Version trägt einen SHA-256-Hash und den Hash der Vorgängerversion
(Hash-Kette) – die Grundlage für Versionierung und spätere Revisionssicherheit.
"""
import calendar
import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Revisionssicherheit (Stufe 4): WORM/Immutable + Aufbewahrungsfristen
# ---------------------------------------------------------------------------
class ImmutableVersionError(Exception):
    """Änderung/Löschung einer WORM-geschützten (is_immutable) Version verboten."""


class RetentionError(Exception):
    """Löschung vor Ablauf der Aufbewahrungsfrist (retention_until) verboten."""


def _add_months(dt: datetime.datetime, months: int) -> datetime.datetime:
    """Addiert ``months`` Monate auf ein Datum – ohne dateutil-Abhängigkeit.

    Bewahrt den Tag, korrigiert Monatsüberläufe (z. B. 31.01. + 1 Monat →
    28./29.02.). Grundlage der Fristberechnung ``retention_until``.
    """
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _audit_block(action: str, version, *, actor=None, reason: str = "") -> None:
    """Schreibt einen Sperr-Audit-Eintrag (append-only) für einen WORM-/Fristen-Block."""
    # Lazy, um zirkuläre Referenzen bei Modul-Ladezeit zu vermeiden.
    AuditLogEntry.objects.create(
        actor=actor,
        action=action,
        object_type=type(version).__name__,
        object_id=str(version.pk) if version.pk else "",
        detail={"reason": reason},
    )


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

    # Aufbewahrungsfrist (Stufe 4): Bis zu diesem Zeitpunkt ist Löschen gesperrt.
    # Wird aus der RetentionPolicy des document_type berechnet (compute_retention_until).
    retention_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Löschsperre bis zu diesem Zeitpunkt (Aufbewahrungsfrist).",
    )

    class Meta:
        verbose_name = "Dokument"
        verbose_name_plural = "Dokumente"
        ordering = ["-added_at"]

    def __str__(self) -> str:
        return self.title

    def compute_retention_until(self) -> datetime.datetime | None:
        """Berechnet den Fristende-Zeitpunkt aus der RetentionPolicy des Typs.

        Referenzdatum ist das Belegdatum (``created_at``), sonst das
        Aufnahmedatum (``added_at``). Ohne Typ oder ohne (positive) Frist: ``None``.
        """
        if not self.document_type_id:
            return None
        policy = RetentionPolicy.objects.filter(
            document_type_id=self.document_type_id
        ).first()
        if not policy or policy.retention_months <= 0:
            return None
        reference = self.created_at or self.added_at or timezone.now()
        return _add_months(reference, policy.retention_months)

    def delete(self, *args, actor=None, **kwargs):
        """Löschsperre: Blockt Löschung innerhalb der Aufbewahrungsfrist.

        Zusätzlich WORM-Schutz: Existieren WORM-Versionen ohne definierte Frist
        (kein Verfallsdatum), bleibt das Dokument unbefristet erhalten. Der
        Löschversuch erzeugt einen Audit-Eintrag und wirft einen sauberen Fehler.
        """
        now = timezone.now()
        if self.retention_until and self.retention_until > now:
            _audit_block(
                "retention_block",
                self,
                actor=actor,
                reason=f"retention_until={self.retention_until.isoformat()}",
            )
            raise RetentionError(
                "Löschen gesperrt: Aufbewahrungsfrist läuft bis "
                f"{self.retention_until:%d.%m.%Y}."
            )
        if not self.retention_until and self.versions.filter(is_immutable=True).exists():
            _audit_block(
                "immutable_block",
                self,
                actor=actor,
                reason="worm_versions_without_retention",
            )
            raise ImmutableVersionError(
                "Löschen gesperrt: Dokument enthält revisionssichere "
                "(WORM-)Versionen ohne definierte Aufbewahrungsfrist."
            )
        return super().delete(*args, **kwargs)


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

    def save(self, *args, **kwargs):
        """WORM-Schutz: Eine bereits als ``is_immutable`` persistierte Version
        kann nicht mehr überschrieben werden.

        Der Übergang ``False → True`` (Versiegeln nach der Verarbeitung) bleibt
        erlaubt, weil der geprüfte DB-Zustand dabei noch ``False`` ist. Jeder
        spätere Schreibversuch erzeugt einen Audit-Eintrag und wirft einen Fehler.
        """
        if self.pk:
            locked = (
                type(self)
                .objects.filter(pk=self.pk, is_immutable=True)
                .exists()
            )
            if locked:
                _audit_block("immutable_block", self, reason="save")
                raise ImmutableVersionError(
                    f"Version v{self.version_no} ist revisionssicher (WORM) und "
                    "kann nicht mehr geändert werden."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, actor=None, **kwargs):
        """WORM-Schutz: Eine ``is_immutable``-Version kann nicht gelöscht werden."""
        if self.is_immutable:
            _audit_block("immutable_block", self, actor=actor, reason="delete")
            raise ImmutableVersionError(
                f"Version v{self.version_no} ist revisionssicher (WORM) und "
                "kann nicht gelöscht werden."
            )
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# Aufbewahrungsfristen je Dokumenttyp (Stufe 4 – Revisionssicherheit)
# ---------------------------------------------------------------------------
class RetentionPolicy(models.Model):
    """Aufbewahrungsfrist (in Monaten) je Dokumenttyp.

    Daraus wird ``Document.retention_until`` berechnet (Belegdatum + Monate).
    Beispiel GoBD: Rechnungen 10 Jahre → ``retention_months = 120``.
    """

    document_type = models.OneToOneField(
        DocumentType,
        on_delete=models.CASCADE,
        related_name="retention_policy",
    )
    retention_months = models.PositiveIntegerField(
        default=0,
        help_text="Aufbewahrungsfrist in Monaten (0 = keine Frist).",
    )

    class Meta:
        verbose_name = "Aufbewahrungsfrist"
        verbose_name_plural = "Aufbewahrungsfristen"
        ordering = ["document_type__name"]

    def __str__(self) -> str:
        return f"{self.document_type.name}: {self.retention_months} Monate"


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
