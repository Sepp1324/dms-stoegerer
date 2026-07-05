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
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
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
            "processing_state",
            "processing_error",
            "processing_failed_step",
            "processing_failed_at",
            "processing_attempts",
            "ocr_status",
            "ocr_error",
            "ocr_engine",
            "ocr_duration_ms",
            "ocr_started_at",
            "ocr_finished_at",
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
        # Fehler-/Retry-Felder (STOAA-228) und OCR-State-Machine (STOAA-225) sind
        # beide serverseitig (Pipeline) gesetzt – nur lesbar, damit das Monitoring
        # die Werte über die API sieht.
        read_only_fields = (
            "processing_error",
            "processing_failed_step",
            "processing_failed_at",
            "processing_attempts",
            "ocr_status",
            "ocr_error",
            "ocr_engine",
            "ocr_duration_ms",
            "ocr_started_at",
            "ocr_finished_at",
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
    # OCR-Status der aktuellen Version direkt am Dokument – für Listen-Monitoring
    # ohne die verschachtelte ``versions``-Liste durchsuchen zu müssen (STOAA-225).
    ocr_status = serializers.CharField(
        source="current_version.ocr_status", read_only=True, default=None
    )
    # Rollup des Verarbeitungsstatus der aktuellen Version (STOAA-248): erspart
    # dem Frontend fürs Listen-Badge den Griff in die nested ``versions``-Liste.
    # Read-only; ``None`` wenn (noch) keine current_version existiert.
    processing_state = serializers.CharField(
        source="current_version.processing_state", read_only=True, default=None
    )
    storage_path_name = serializers.CharField(
        source="storage_path.name", read_only=True, default=None
    )
    # Archivnummer (STOAA-284/285): read-only – die ASN ist unveränderlich und
    # wird serverseitig vergeben. ``asn_label`` liefert die kanonische Anzeigeform
    # ``ASN000123`` fürs Frontend (Detailansicht/QR-Download).
    asn_label = serializers.SerializerMethodField()
    # Suchergebnis-Snippet (STOAA-368/370): sicheres HTML mit <mark>-Highlighting
    # rund um den Treffer. Nur bei aktiver Volltextsuche (``?q=``) gesetzt – dann
    # trägt das Objekt die ``snippet_raw``-Annotation aus get_queryset; sonst
    # (Detail, keine Suche, Treffer außerhalb des OCR-Texts) ``None``.
    snippet = serializers.SerializerMethodField()
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
            "ocr_status",
            "processing_state",
            "asn",
            "asn_label",
            "snippet",
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
            "asn",  # unveränderlich, serverseitig vergeben (STOAA-284/285)
            "ai_suggestions",
            "ai_suggested_at",
            "classification",
            "status",  # Statuswechsel NUR über submit/approve/reject – nie per PATCH (STOAA-63)
        )

    def get_asn_label(self, obj) -> str | None:
        """Kanonische Anzeigeform der ASN (``ASN000123``) oder ``None``."""
        if not obj.asn:
            return None
        from .services.asn import format_asn

        return format_asn(obj.asn)

    def get_snippet(self, obj) -> str | None:
        """Sanitiziertes Snippet-HTML (nur ``<mark>``) oder ``None``.

        ``snippet_raw`` trägt nur bei aktiver FTS-Suche die ts_headline-Annotation;
        ``build_snippet`` escaped den Rohtext und ersetzt die Sentinels durch
        ``<mark>``. Ohne Annotation/Treffer → ``None``.
        """
        from .services.search_snippet import build_snippet

        return build_snippet(getattr(obj, "snippet_raw", None))

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


class MailAccountSerializer(serializers.ModelSerializer):
    """CRUD-Serializer für IMAP-Postfächer (STOAA-212).

    Sicherheit:
    - ``password`` ist **write-only**: Es wird nie in einer Response ausgegeben
      (weder Klartext noch Chiffretext). Die Verschlüsselung at-rest übernimmt
      ``MailAccount.save()`` (Fernet, siehe ``crypto.py``).
    - ``has_password`` zeigt der UI, ob ein Passwort hinterlegt ist, ohne es
      preiszugeben.
    - ``last_checked_at`` / ``last_error`` sind Status und nur lesbar.
    """

    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        style={"input_type": "password"},
        help_text="Nur schreiben. Leerer String bei PATCH = unverändert lassen.",
    )
    has_password = serializers.SerializerMethodField()

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
            "password",
            "password_env",
            "has_password",
            "enabled",
            "last_checked_at",
            "last_error",
        )
        read_only_fields = ("id", "last_checked_at", "last_error")

    def get_has_password(self, obj) -> bool:
        return bool(obj.password or obj.password_env)

    def update(self, instance, validated_data):
        # Leeres Passwort bei PATCH bedeutet „nicht ändern" – sonst würde ein
        # UI-Formular ohne erneute Passworteingabe das gespeicherte löschen.
        if validated_data.get("password", None) == "":
            validated_data.pop("password")
        return super().update(instance, validated_data)


# ---------------------------------------------------------------------------
# Workflow-Engine (STOAA-263) – verschachtelte Serializer für Trigger/Aktionen
# ---------------------------------------------------------------------------
class WorkflowTriggerSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowTrigger
        fields = (
            "id",
            "trigger_type",
            "sources",
            "filter_path",
            "filter_correspondent",
            "filter_document_type",
            "filter_has_tags",
            "filter_has_not_tags",
            "filter_text_contains",
            "filter_text_regex",
        )


class WorkflowActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowAction
        fields = (
            "id",
            "order",
            "action_type",
            "assign_title",
            "assign_correspondent",
            "assign_document_type",
            "assign_storage_path",
            "assign_tags",
            "assign_owner",
            "assign_custom_fields",
            "remove_tags",
        )


class WorkflowSerializer(serializers.ModelSerializer):
    """Workflow mit verschachteltem Trigger (1:1) und Aktionsliste (1:n).

    Schreiben (POST/PUT/PATCH) akzeptiert ``trigger`` als Objekt und ``actions``
    als Liste; die Engine liest sie deterministisch in ``order``-Reihenfolge.
    Beim Update werden Aktionen vollständig ersetzt (idempotent, einfaches
    Contract für den Frontend-Editor).
    """

    trigger = WorkflowTriggerSerializer(required=False)
    actions = WorkflowActionSerializer(many=True, required=False)

    class Meta:
        model = Workflow
        fields = ("id", "name", "order", "enabled", "trigger", "actions")

    def _write_nested(self, workflow, trigger_data, actions_data):
        # Trigger (OneToOne) – ersetzen/aktualisieren
        if trigger_data is not None:
            has_tags = trigger_data.pop("filter_has_tags", None)
            has_not_tags = trigger_data.pop("filter_has_not_tags", None)
            trigger, _ = WorkflowTrigger.objects.update_or_create(
                workflow=workflow, defaults=trigger_data
            )
            if has_tags is not None:
                trigger.filter_has_tags.set(has_tags)
            if has_not_tags is not None:
                trigger.filter_has_not_tags.set(has_not_tags)

        # Aktionen (1:n) – vollständig ersetzen
        if actions_data is not None:
            workflow.actions.all().delete()
            for action_data in actions_data:
                assign_tags = action_data.pop("assign_tags", [])
                remove_tags = action_data.pop("remove_tags", [])
                action = WorkflowAction.objects.create(workflow=workflow, **action_data)
                if assign_tags:
                    action.assign_tags.set(assign_tags)
                if remove_tags:
                    action.remove_tags.set(remove_tags)

    def create(self, validated_data):
        trigger_data = validated_data.pop("trigger", None)
        actions_data = validated_data.pop("actions", None)
        workflow = Workflow.objects.create(**validated_data)
        self._write_nested(workflow, trigger_data, actions_data)
        return workflow

    def update(self, instance, validated_data):
        trigger_data = validated_data.pop("trigger", None)
        actions_data = validated_data.pop("actions", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        self._write_nested(instance, trigger_data, actions_data)
        return instance



# ---------------------------------------------------------------------------
# Versionsvergleich-Serializer (STOAA-288) – rein lesend, kein Model gebunden
# ---------------------------------------------------------------------------

from rest_framework import serializers as _s


class FieldChangeSerializer(_s.Serializer):
    old = _s.CharField(allow_null=True)
    new = _s.CharField(allow_null=True)


class TagDiffSerializer(_s.Serializer):
    added = _s.ListField(child=_s.CharField())
    removed = _s.ListField(child=_s.CharField())


class FileDiffSerializer(_s.Serializer):
    old_sha256 = _s.CharField()
    new_sha256 = _s.CharField()
    old_size = _s.IntegerField()
    new_size = _s.IntegerField()
    old_mime = _s.CharField()
    new_mime = _s.CharField()
    changed = _s.BooleanField()
    old_page_count = _s.IntegerField(allow_null=True)
    new_page_count = _s.IntegerField(allow_null=True)
    pages_changed = _s.BooleanField()


class CompareSummarySerializer(_s.Serializer):
    text_changed = _s.BooleanField()
    metadata_changed = _s.BooleanField()
    tags_changed = _s.BooleanField()
    custom_fields_changed = _s.BooleanField()
    binary_changed = _s.BooleanField()
    pages_changed = _s.BooleanField()
    tag_changes = _s.IntegerField()
    field_changes = _s.IntegerField()


class VersionCompareResultSerializer(_s.Serializer):
    document = _s.IntegerField()
    from_version = _s.IntegerField()
    to_version = _s.IntegerField()
    summary = CompareSummarySerializer()
    text_diff = _s.CharField()
    metadata = _s.DictField(child=FieldChangeSerializer())
    tags = TagDiffSerializer()
    custom_fields = _s.DictField(child=FieldChangeSerializer())
    files = FileDiffSerializer()
    # Stufe 1 vergleicht beide Versionen gegen dasselbe ``Document`` – ein
    # echter Metadaten-/Tag-/Feld-Diff pro Version ist erst mit Stufe 2
    # (Metadaten-Versionierung) möglich. Das Flag ist Teil des Contracts, damit
    # das Frontend die entsprechenden Badges gezielt aus-/einblenden kann
    # (STOAA-290). In Stufe 1 immer ``False``.
    metadata_versioning_supported = _s.SerializerMethodField()

    def get_metadata_versioning_supported(self, obj):
        # ``VersionCompareResult`` (dataclass) trägt das Flag in Stufe 1 nicht;
        # per Vertrag ist es hier immer False. Stufe 2 kann das Attribut am
        # Ergebnis setzen, dann wird es hier durchgereicht.
        return bool(getattr(obj, "metadata_versioning_supported", False))
