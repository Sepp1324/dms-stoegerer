from django import forms
from django.contrib import admin

from .models import (
    AuditLogEntry,
    ClassificationRule,
    Correspondent,
    CustomField,
    CustomFieldValue,
    Document,
    DocumentType,
    DocumentVersion,
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


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "correspondent", "document_type", "added_at", "owner")
    list_filter = ("document_type", "correspondent", "tags")
    search_fields = ("title",)
    filter_horizontal = ("tags",)
    inlines = (DocumentVersionInline, CustomFieldValueInline)


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
admin.site.register(CustomField)

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


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "order", "enabled")
    list_editable = ("order", "enabled")
    inlines = [WorkflowTriggerInline, WorkflowActionInline]


admin.site.site_header = "DMS-Verwaltung"
admin.site.site_title = "DMS"
admin.site.index_title = "Dokumenten-Management"
