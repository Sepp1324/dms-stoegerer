from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    AuditLogEntry,
    ClassificationRule,
    Correspondent,
    CustomField,
    CustomFieldValue,
    Document,
    DocumentShareLink,
    DocumentType,
    DocumentVersion,
    MailAccount,
    StoragePath,
    Tag,
)


class DocumentShareLinkSerializer(serializers.ModelSerializer):
    """Ausgabe-Serializer für Freigabelinks (STOAA-190).

    Enthält bewusst **weder** ``token_hash`` **noch** den Klartext-Token –
    der Klartext wird ausschließlich einmalig in der Create-Response ergänzt
    (siehe ``DocumentShareLinkViewSet.create``). ``is_valid`` kommt aus der
    Model-Property (nicht widerrufen UND nicht abgelaufen).
    """

    class Meta:
        model = DocumentShareLink
        fields = (
            "id",
            "document",
            "created_at",
            "expires_at",
            "revoked_at",
            "is_valid",
        )


class MailAccountSerializer(serializers.ModelSerializer):
    """CRUD-Serializer für IMAP-Postfächer (STOAA-214).

    Sicherheit:
      * ``password`` ist **write_only** – es taucht NIE in einer Response auf.
        Bei ``update`` (PATCH) überschreibt ein **leeres** Passwort das
        gespeicherte NICHT (kein versehentliches Löschen bei Teil-Updates).
      * ``password_env`` (Name der Secret-Env), ``last_checked_at`` und
        ``last_error`` sind **read_only** – sie werden vom Server / Test-Call
        gepflegt, nicht vom Client gesetzt.
      * ``owner`` ist ein **optionales** Auswahlfeld (Standard-Empfänger, PK
        oder ``null``) und wird NICHT automatisch auf den Request-User gesetzt
        (Mail-Konten sind DMS-Infrastruktur, keine Nutzer-Ressource).
    """

    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        style={"input_type": "password"},
    )
    owner = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = MailAccount
        fields = (
            "id",
            "name",
            "owner",
            "host",
            "port",
            "use_ssl",
            "username",
            "folder",
            "enabled",
            "password",
            "password_env",
            "last_checked_at",
            "last_error",
        )
        read_only_fields = ("password_env", "last_checked_at", "last_error")

    def update(self, instance, validated_data):
        # Leeres Passwort bei (Teil-)Update bedeutet "unverändert" – nicht löschen.
        if not validated_data.get("password"):
            validated_data.pop("password", None)
        return super().update(instance, validated_data)


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


class CustomFieldSerializer(serializers.ModelSerializer):
    """Definition eines Zusatzfeldes.

    ``data_type`` ist beim Update read-only: ein nachträglicher Typwechsel wäre
    breaking (bestehende ``CustomFieldValue``-Texte würden zum neuen Typ nicht
    mehr passen, Filter/FE-Formatierung brächen). Beim Anlegen ist er schreibbar.
    """

    class Meta:
        model = CustomField
        fields = ("id", "name", "data_type")

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:  # Update (PATCH/PUT) → Typ einfrieren
            fields["data_type"].read_only = True
        return fields


class CustomFieldValueSerializer(serializers.ModelSerializer):
    """Ein Zusatzfeld-Wert an einem Dokument.

    ``field`` ist als PK schreibbar (Upsert-Kontrakt); ``field_name`` und
    ``data_type`` sind read-only Zusatzangaben, damit das FE den Wert im
    Document-GET ohne Zweit-Request typkorrekt formatieren kann.
    """

    field_name = serializers.CharField(source="field.name", read_only=True)
    data_type = serializers.CharField(source="field.data_type", read_only=True)

    class Meta:
        model = CustomFieldValue
        fields = ("field", "value", "field_name", "data_type")


class DocumentVersionSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    has_archive = serializers.SerializerMethodField()

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
            "retention_until",
            "created_by",
            "created_by_name",
            "has_archive",
            "created_at",
        )

    def get_created_by_name(self, obj) -> str | None:
        """Anzeigename des Erstellers (voller Name, sonst Login) – Altdaten: ``None``."""
        user = obj.created_by
        if user is None:
            return None
        return user.get_full_name() or user.get_username()

    def get_has_archive(self, obj) -> bool:
        """Ob ein OCR-Archiv-PDF existiert (bestimmt die Inline-Vorschaubarkeit)."""
        return bool(obj.archive_path)


class AuditLogEntrySerializer(serializers.ModelSerializer):
    """Ein Audit-Eintrag für die Verlauf-Ansicht (read-only, append-only)."""

    # Anzeigename des Akteurs; „System" für automatische Schritte (actor=None).
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLogEntry
        fields = (
            "id",
            "timestamp",
            "actor",
            "actor_name",
            "action",
            "object_type",
            "object_id",
            "detail",
        )
        read_only_fields = fields

    def get_actor_name(self, obj) -> str:
        if obj.actor is None:
            return "System"
        return obj.actor.get_full_name() or obj.actor.username


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
    # Zusatzfeld-Werte: GET = nested Liste; PATCH = Upsert per (document, field)
    # in ``update()``/``create()`` (unique_together). ``required=False``, damit
    # ein PATCH ohne diesen Schlüssel die bestehenden Werte unangetastet lässt.
    custom_field_values = CustomFieldValueSerializer(many=True, required=False)

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
            "status",
            "custom_field_values",
            "versions",
        )
        read_only_fields = (
            "added_at",
            "current_version",
            "owner",  # Eigentümer serverseitig gesetzt – nicht per Request änderbar (STOAA-7)
            "ai_suggestions",
            "ai_suggested_at",
            "classification",
            "status",  # Statuswechsel NUR über submit/approve/reject – nie per PATCH (STOAA-63)
        )

    def _upsert_custom_field_values(self, document, values):
        """Upsert der Zusatzfeld-Werte per unique_together (document, field).

        Es werden ausschließlich die übergebenen Werte angelegt/aktualisiert;
        nicht genannte bestehende Werte bleiben erhalten (Upsert, kein Replace).
        """
        for item in values:
            CustomFieldValue.objects.update_or_create(
                document=document,
                field=item["field"],
                defaults={"value": item.get("value", "")},
            )

    def create(self, validated_data):
        cfv = validated_data.pop("custom_field_values", None)
        document = super().create(validated_data)
        if cfv:
            self._upsert_custom_field_values(document, cfv)
        return document

    def update(self, instance, validated_data):
        # ``None`` = Schlüssel nicht im PATCH → Werte unverändert lassen.
        cfv = validated_data.pop("custom_field_values", None)
        document = super().update(instance, validated_data)
        if cfv is not None:
            self._upsert_custom_field_values(document, cfv)
        return document
