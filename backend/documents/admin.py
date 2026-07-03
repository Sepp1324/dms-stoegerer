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
)


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    fields = ("version_no", "file_path", "sha256", "prev_hash", "is_immutable", "created_at")
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
    list_display = ("document", "version_no", "mime_type", "size", "is_immutable", "created_at")
    search_fields = ("document__title", "sha256")


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

@admin.register(MailAccount)
class MailAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "username", "host", "folder", "enabled", "last_checked_at")
    list_filter = ("enabled", "use_ssl")
    search_fields = ("name", "username", "host")
    readonly_fields = ("last_checked_at", "last_error")
    fieldsets = (
        (None, {"fields": ("name", "enabled")}),
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


admin.site.site_header = "DMS-Verwaltung"
admin.site.site_title = "DMS"
admin.site.index_title = "Dokumenten-Management"
