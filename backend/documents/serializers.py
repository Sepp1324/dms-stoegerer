from rest_framework import serializers

from .models import (
    ClassificationRule,
    Correspondent,
    Document,
    DocumentType,
    DocumentVersion,
    StoragePath,
    Tag,
)


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ("id", "name", "color", "parent")


class CorrespondentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Correspondent
        fields = ("id", "name")


class DocumentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentType
        fields = ("id", "name")


class StoragePathSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoragePath
        fields = ("id", "name", "path_template")
        extra_kwargs = {
            # Beim Inline-Anlegen genügt ein Name; Template hat einen Default.
            "path_template": {"required": False},
        }


class ClassificationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassificationRule
        fields = ("id", "name", "priority", "enabled", "match", "then")


class DocumentVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentVersion
        fields = (
            "id",
            "version_no",
            "sha256",
            "prev_hash",
            "mime_type",
            "size",
            "page_count",
            "is_immutable",
            "created_at",
        )


class DocumentSerializer(serializers.ModelSerializer):
    versions = DocumentVersionSerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    # Schreib-Pfad für Tags: Liste von IDs (die nested `tags` bleiben Read-only).
    tag_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Tag.objects.all(),
        source="tags",
        write_only=True,
        required=False,
    )
    # Anzeige-Namen für die Liste (spart dem Frontend Zusatz-Requests).
    correspondent_name = serializers.CharField(
        source="correspondent.name", read_only=True, default=None
    )
    document_type_name = serializers.CharField(
        source="document_type.name", read_only=True, default=None
    )
    page_count = serializers.IntegerField(
        source="current_version.page_count", read_only=True, default=None
    )
    storage_path_name = serializers.CharField(
        source="storage_path.name", read_only=True, default=None
    )

    class Meta:
        model = Document
        fields = (
            "id",
            "title",
            "created_at",
            "added_at",
            "correspondent",
            "correspondent_name",
            "document_type",
            "document_type_name",
            "storage_path",
            "storage_path_name",
            "tags",
            "tag_ids",
            "owner",
            "current_version",
            "page_count",
            "ai_suggestions",
            "ai_suggested_at",
            "classification",
            "versions",
        )
        read_only_fields = (
            "added_at",
            "current_version",
            "ai_suggestions",
            "ai_suggested_at",
            "classification",
        )
