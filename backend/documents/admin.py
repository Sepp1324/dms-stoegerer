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

admin.site.site_header = "DMS-Verwaltung"
admin.site.site_title = "DMS"
admin.site.index_title = "Dokumenten-Management"
