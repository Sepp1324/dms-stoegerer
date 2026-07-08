from django import forms
from django.contrib import admin

from .models import (
    ASNScan,
    AuditLogEntry,
    BackupMonitor,
    CaseFile,
    CaseFileCandidate,
    ClassificationRule,
    Correspondent,
    CustomField,
    CustomFieldValue,
    Document,
    DocumentFolder,
    DocumentPageText,
    DocumentType,
    DocumentVersion,
    ExtractionCandidate,
    MailAccount,
    ProcessedMail,
    StoragePath,
    Tag,
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
)


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    fields = (
        "version_no",
        "file_path",
        "processing_state",
        "sha256",
        "prev_hash",
        "is_immutable",
        "created_at",
    )
    readonly_fields = ("created_at",)


class CustomFieldValueInline(admin.TabularInline):
    model = CustomFieldValue
    extra = 0


class ASNScanInline(admin.TabularInline):
    model = ASNScan
    extra = 0
    fields = ("scanned_at", "matched_by", "confidence", "version")
    readonly_fields = ("scanned_at",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    # ASN (STOAA-284/285): read-only anzeigen, filter-/suchbar und sortierbar.
    list_display = (
        "asn",
        "title",
        "correspondent",
        "document_type",
        "folder",
        "case_file",
        "review_status",
        "added_at",
        "owner",
    )
    list_filter = (
        "review_status",
        "folder",
        "case_file",
        "document_type",
        "correspondent",
        "tags",
    )
    search_fields = ("title", "asn")
    ordering = ("-added_at",)
    readonly_fields = ("asn",)
    filter_horizontal = ("tags",)
    inlines = (DocumentVersionInline, CustomFieldValueInline, ASNScanInline)


@admin.register(ASNScan)
class ASNScanAdmin(admin.ModelAdmin):
    list_display = ("document", "matched_by", "confidence", "version", "scanned_at")
    list_filter = ("matched_by",)
    search_fields = ("document__title", "document__asn")
    ordering = ("-scanned_at",)
    readonly_fields = ("document", "version", "matched_by", "confidence", "scanned_at")


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "version_no",
        "processing_state",
        "mime_type",
        "size",
        "is_immutable",
        "created_at",
    )
    list_filter = ("processing_state", "is_immutable", "mime_type")
    search_fields = ("document__title", "sha256")
    readonly_fields = (
        "processing_error",
        "processing_failed_step",
        "processing_failed_at",
        "processing_attempts",
    )


@admin.register(DocumentPageText)
class DocumentPageTextAdmin(admin.ModelAdmin):
    list_display = ("version", "page_no", "text_preview")
    list_filter = ("page_no",)
    search_fields = ("version__document__title", "text")

    @admin.display(description="Text")
    def text_preview(self, obj):
        text = (obj.text or "").strip()
        return text[:120] + ("…" if len(text) > 120 else "")


@admin.register(CaseFile)
class CaseFileAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "owner",
        "document_count",
        "ai_summary_source",
        "updated_at",
    )
    list_filter = ("status", "owner")
    search_fields = ("title", "description", "ai_summary", "documents__title")
    readonly_fields = (
        "ai_summary",
        "ai_summary_source",
        "ai_summary_generated_at",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Dokumente")
    def document_count(self, obj):
        return obj.documents.count()


@admin.register(ExtractionCandidate)
class ExtractionCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "field",
        "value",
        "confidence",
        "status",
        "source_page",
        "created_at",
    )
    list_filter = ("field", "status", "source")
    search_fields = ("document__title", "value", "normalized_value", "source_snippet")
    readonly_fields = (
        "document",
        "field",
        "value",
        "normalized_value",
        "confidence",
        "reason",
        "source",
        "source_page",
        "source_snippet",
        "source_snippet_html",
        "status",
        "created_at",
        "applied_at",
        "dismissed_at",
    )


@admin.register(CaseFileCandidate)
class CaseFileCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "kind",
        "case_file",
        "suggested_title",
        "score",
        "status",
        "created_at",
    )
    list_filter = ("kind", "status", "source")
    search_fields = ("document__title", "case_file__title", "suggested_title", "reason")
    readonly_fields = (
        "document",
        "case_file",
        "kind",
        "suggested_title",
        "signature",
        "score",
        "reason",
        "signals",
        "source",
        "status",
        "created_at",
        "applied_at",
        "dismissed_at",
    )


@admin.register(ClassificationRule)
class ClassificationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "priority", "enabled")
    list_editable = ("priority", "enabled")


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action", "object_type", "object_id")
    list_filter = ("action", "object_type")
    readonly_fields = ("timestamp", "actor", "action", "object_type", "object_id", "detail")


admin.site.register(Correspondent)
admin.site.register(DocumentType)
admin.site.register(Tag)
admin.site.register(StoragePath)
admin.site.register(DocumentFolder)
admin.site.register(CustomField)


@admin.register(BackupMonitor)
class BackupMonitorAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "status",
        "artifact_timestamp",
        "last_success_at",
        "last_started_at",
        "last_finished_at",
        "updated_at",
    )
    list_filter = ("kind", "status")
    readonly_fields = (
        "kind",
        "status",
        "artifact_timestamp",
        "message",
        "last_started_at",
        "last_success_at",
        "last_finished_at",
        "updated_at",
    )

class MailAccountAdminForm(forms.ModelForm):
    """Maskiert das Klartext-Fallback-Passwort im Admin (write-only-Verhalten)."""

    class Meta:
        model = MailAccount
        fields = "__all__"
        widgets = {
            # render_value=False → der gespeicherte Wert wird nie zurückgerendert;
            # das Feld zeigt sich stets leer und maskiert (Punkte statt Klartext).
            "password": forms.PasswordInput(render_value=False),
        }

    def clean_password(self):
        # Leer eingereicht = "unverändert": bestehendes Passwort beibehalten, damit
        # das maskierte (immer leere) Feld beim Speichern nichts versehentlich löscht.
        value = self.cleaned_data.get("password")
        if not value and self.instance and self.instance.pk:
            return self.instance.password
        return value


@admin.register(MailAccount)
class MailAccountAdmin(admin.ModelAdmin):
    form = MailAccountAdminForm
    list_display = ("name", "owner", "username", "host", "folder", "enabled", "last_checked_at")
    list_filter = ("enabled", "use_ssl", "owner")
    search_fields = ("name", "username", "host")
    readonly_fields = ("last_checked_at", "last_error")
    raw_id_fields = ("owner",)
    fieldsets = (
        (None, {"fields": ("name", "enabled")}),
        (
            "Eigentümer",
            {
                "fields": ("owner",),
                "description": (
                    "Standard-Empfänger: Eigentümer der importierten Dokumente. "
                    "Ohne Eigentümer bleibt das Postfach ein Admin-Triage-Postfach "
                    "– die Dokumente sind dann nur für DMS-Admins sichtbar, bis sie "
                    "manuell zugeordnet werden (Kohärenz mit der Owner-Isolation)."
                ),
            },
        ),
        ("Server", {"fields": ("host", "port", "use_ssl", "folder")}),
        (
            "Zugang",
            {
                "fields": ("username", "password_env", "password"),
                "description": (
                    "Passwort möglichst über ein k8s-Secret (password_env verweist "
                    "auf die Umgebungsvariable). Das direkte Passwort-Feld ist nur "
                    "der Fallback für lokale Entwicklung."
                ),
            },
        ),
        ("Status", {"fields": ("last_checked_at", "last_error")}),
    )


@admin.register(ProcessedMail)
class ProcessedMailAdmin(admin.ModelAdmin):
    list_display = ("subject", "sender", "account", "imported_count", "processed_at")
    list_filter = ("account",)
    search_fields = ("subject", "sender", "message_id")
    readonly_fields = (
        "account",
        "message_id",
        "subject",
        "sender",
        "attachment_count",
        "imported_count",
        "processed_at",
    )


class WorkflowTriggerInline(admin.StackedInline):
    model = WorkflowTrigger
    extra = 0
    filter_horizontal = ("filter_has_tags", "filter_has_not_tags")


class WorkflowActionInline(admin.TabularInline):
    model = WorkflowAction
    extra = 0
    filter_horizontal = ("assign_tags", "remove_tags")


class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "order", "enabled")
    list_editable = ("order", "enabled")
    inlines = [WorkflowTriggerInline, WorkflowActionInline]


# Idempotente Registrierung: In manchen Build-Umgebungen (collectstatic während
# des Docker-Builds) werden App-Module durch das Zusammenspiel von Djangos
# App-Population und Celerys ``autodiscover_tasks`` nicht-deterministisch ein
# zweites Mal importiert (sichtbar an den „Model … was already registered"-
# RuntimeWarnings). Der harte ``@admin.register``-Dekorator würde dann mit
# ``AlreadyRegistered`` abbrechen und collectstatic (und damit den Image-Build)
# scheitern lassen. Das ``try/except`` macht die Registrierung robust, ohne die
# fachliche Funktion (Admin-Verwaltung der Workflows) zu verändern.
try:
    admin.site.register(Workflow, WorkflowAdmin)
except admin.sites.AlreadyRegistered:
    pass


admin.site.site_header = "DMS-Verwaltung"
admin.site.site_title = "DMS"
admin.site.index_title = "Dokumenten-Management"
