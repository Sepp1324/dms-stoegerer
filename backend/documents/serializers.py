from rest_framework import serializers

from .models import (
    AuditLogEntry,
    CaseFile,
    CaseFileCandidate,
    ClassificationRule,
    Correspondent,
    ContractRecord,
    CustomField,
    CustomFieldValue,
    Document,
    Dossier,
    ExtractionCandidate,
    DocumentFolder,
    DocumentReminder,
    DocumentReviewTask,
    DocumentShareLink,
    DocumentType,
    DocumentVersion,
    DocumentEntity,
    EntityIdentifier,
    EntityRelation,
    KnowledgeEntity,
    MailAccount,
    ProcessedMail,
    SavedView,
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


class DocumentFolderSerializer(serializers.ModelSerializer):
    full_path = serializers.CharField(read_only=True)
    document_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = DocumentFolder
        fields = ("id", "name", "parent", "full_path", "document_count", "shared_with_household")
        extra_kwargs = {
            "parent": {"required": False},
            # Ordnerweite Familien-Freigabe – per PATCH umschaltbar.
            "shared_with_household": {"required": False},
        }

    def validate(self, attrs):
        parent = attrs.get("parent", getattr(self.instance, "parent", None))
        name = attrs.get("name", getattr(self.instance, "name", None))
        if self.instance is not None and parent is not None:
            if parent.pk == self.instance.pk:
                raise serializers.ValidationError("Ein Ordner kann nicht sein eigener Parent sein.")
            cursor = parent.parent
            while cursor is not None:
                if cursor.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        "Ein Ordner kann nicht in einen eigenen Unterordner verschoben werden."
                    )
                cursor = cursor.parent
        if name:
            siblings = DocumentFolder.objects.filter(name=name, parent=parent)
            if self.instance is not None:
                siblings = siblings.exclude(pk=self.instance.pk)
            if siblings.exists():
                raise serializers.ValidationError(
                    "In diesem Ordner existiert bereits ein Unterordner mit diesem Namen."
                )
        return attrs


class SavedViewSerializer(serializers.ModelSerializer):
    count = serializers.SerializerMethodField()

    class Meta:
        model = SavedView
        fields = (
            "id",
            "name",
            "description",
            "query",
            "is_default",
            "count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("count", "created_at", "updated_at")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Der Name darf nicht leer sein.")
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            siblings = SavedView.objects.filter(owner=request.user, name=value)
            if self.instance is not None:
                siblings = siblings.exclude(pk=self.instance.pk)
            if siblings.exists():
                raise serializers.ValidationError(
                    "Eine gespeicherte Ansicht mit diesem Namen existiert bereits."
                )
        return value

    def validate_query(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Die gespeicherte Query muss ein Objekt sein.")

        allowed = {
            "q",
            "correspondent",
            "document_type",
            "tag",
            "storage_path",
            "folder",
            "case_file",
            "processing_state",
            "review_status",
            "ordering",
        }
        cleaned = {}
        for key, raw in value.items():
            if key == "customFilters":
                if not isinstance(raw, dict):
                    continue
                custom = {
                    str(custom_key): str(custom_value)
                    for custom_key, custom_value in raw.items()
                    if str(custom_key).startswith("custom_field_")
                    and custom_value not in ("", None)
                }
                if custom:
                    cleaned[key] = custom
            elif key in allowed and raw not in ("", None, [], {}):
                cleaned[key] = raw
        return cleaned

    def get_count(self, obj):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return 0
        from .services.saved_views import count_documents_for_query

        return count_documents_for_query(request.user, obj.query)


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


class ExtractionCandidateSerializer(serializers.ModelSerializer):
    """Smart-Inbox-Vorschlag für strukturierte Metadaten.

    Der Vorschlag ist bewusst ein eigenes Objekt statt direktes PATCH am
    Dokument: Extraktion bleibt prüfbar, kann einzeln übernommen/verworfen
    werden und ist im Audit-Trail nachvollziehbar.
    """

    field_label = serializers.SerializerMethodField()

    class Meta:
        model = ExtractionCandidate
        fields = (
            "id",
            "document",
            "field",
            "field_label",
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
        read_only_fields = fields

    def get_field_label(self, obj) -> str:
        return obj.get_field_display()


class DocumentVersionSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    has_archive = serializers.SerializerMethodField()
    seal_ok = serializers.SerializerMethodField()

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
            "snapshot_schema_version",
            "snapshot_taken_at",
            "seal_hash",
            "seal_ok",
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

    def get_seal_ok(self, obj) -> bool:
        """Metadaten-Siegelprüfung ohne Dateisystemzugriff."""
        from documents.services import version_snapshot

        return version_snapshot.verify_seal(obj)


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


class CaseFileDocumentSerializer(serializers.ModelSerializer):
    """Schmale Dokumentzeile innerhalb einer Vorgangsakte."""

    correspondent_name = serializers.CharField(
        source="correspondent.name", read_only=True, default=None
    )
    document_type_name = serializers.CharField(
        source="document_type.name", read_only=True, default=None
    )
    folder_path = serializers.CharField(
        source="folder.full_path", read_only=True, default=None
    )
    asn_label = serializers.SerializerMethodField()
    page_count = serializers.IntegerField(
        source="current_version.page_count", read_only=True, default=None
    )

    class Meta:
        model = Document
        fields = (
            "id",
            "title",
            "created_at",
            "added_at",
            "correspondent_name",
            "document_type_name",
            "folder_path",
            "asn",
            "asn_label",
            "page_count",
        )

    def get_asn_label(self, obj) -> str | None:
        if not obj.asn:
            return None
        from .services.asn import format_asn

        return format_asn(obj.asn)


class CaseFileSerializer(serializers.ModelSerializer):
    """Vorgangsakte mit Dokument-Timeline und KI-/Heuristik-Zusammenfassung."""

    document_count = serializers.SerializerMethodField()
    latest_document_at = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()

    class Meta:
        model = CaseFile
        fields = (
            "id",
            "title",
            "description",
            "status",
            "status_label",
            "owner",
            "document_count",
            "latest_document_at",
            "ai_summary",
            "ai_summary_source",
            "ai_summary_generated_at",
            "created_at",
            "updated_at",
            "documents",
        )
        read_only_fields = (
            "owner",
            "document_count",
            "latest_document_at",
            "ai_summary",
            "ai_summary_source",
            "ai_summary_generated_at",
            "created_at",
            "updated_at",
            "documents",
        )

    def get_status_label(self, obj) -> str:
        return obj.get_status_display()

    def get_document_count(self, obj) -> int:
        return getattr(obj, "document_count", None) or obj.documents.count()

    def get_latest_document_at(self, obj):
        annotated = getattr(obj, "latest_document_at", None)
        if annotated is not None:
            return annotated
        return obj.documents.order_by("-added_at").values_list("added_at", flat=True).first()

    def get_documents(self, obj):
        docs = obj.documents.all().order_by("-created_at", "-added_at", "-id")
        return CaseFileDocumentSerializer(docs, many=True).data


class DossierSerializer(serializers.ModelSerializer):
    """Gespeicherte Copilot-/Rechercheakte mit Quellenbelegen."""

    status_label = serializers.SerializerMethodField()
    generated_source_label = serializers.SerializerMethodField()
    document_count = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()

    class Meta:
        model = Dossier
        fields = (
            "id",
            "title",
            "query",
            "status",
            "status_label",
            "owner",
            "summary",
            "timeline",
            "sources",
            "entities",
            "contracts",
            "generated_source",
            "generated_source_label",
            "generated_at",
            "created_at",
            "updated_at",
            "document_count",
            "documents",
        )
        read_only_fields = (
            "owner",
            "summary",
            "timeline",
            "sources",
            "entities",
            "contracts",
            "generated_source",
            "generated_source_label",
            "generated_at",
            "created_at",
            "updated_at",
            "document_count",
            "documents",
        )

    def get_status_label(self, obj) -> str:
        return obj.get_status_display()

    def get_generated_source_label(self, obj) -> str:
        return obj.get_generated_source_display()

    def get_document_count(self, obj) -> int:
        return getattr(obj, "document_count", None) or obj.documents.count()

    def get_documents(self, obj):
        docs = obj.documents.all().select_related(
            "correspondent",
            "document_type",
            "folder",
            "current_version",
        )
        return CaseFileDocumentSerializer(docs, many=True).data


class CaseFileCandidateSerializer(serializers.ModelSerializer):
    """Akten-Autopilot-Vorschlag für die Review-Queue."""

    kind_label = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    case_file_title = serializers.CharField(
        source="case_file.title", read_only=True, default=None
    )
    case_file_status = serializers.CharField(
        source="case_file.status", read_only=True, default=None
    )

    class Meta:
        model = CaseFileCandidate
        fields = (
            "id",
            "document",
            "case_file",
            "case_file_title",
            "case_file_status",
            "kind",
            "kind_label",
            "suggested_title",
            "score",
            "reason",
            "signals",
            "source",
            "status",
            "status_label",
            "created_at",
            "applied_at",
            "dismissed_at",
        )
        read_only_fields = fields

    def get_kind_label(self, obj) -> str:
        return obj.get_kind_display()

    def get_status_label(self, obj) -> str:
        return obj.get_status_display()


class DocumentReviewTaskSerializer(serializers.ModelSerializer):
    """Konkreter Klärungsauftrag für die Review-Inbox."""

    kind_label = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    document_title = serializers.CharField(source="document.title", read_only=True)
    asn_label = serializers.SerializerMethodField()

    class Meta:
        model = DocumentReviewTask
        fields = (
            "id",
            "document",
            "document_title",
            "kind",
            "kind_label",
            "status",
            "status_label",
            "priority",
            "message",
            "suggested_action",
            "data",
            "created_at",
            "updated_at",
            "resolved_at",
            "resolved_by",
            "asn_label",
        )
        read_only_fields = fields

    def get_kind_label(self, obj) -> str:
        return obj.get_kind_display()

    def get_status_label(self, obj) -> str:
        return obj.get_status_display()

    def get_asn_label(self, obj) -> str | None:
        if not obj.document.asn:
            return None
        from .services.asn import format_asn

        return format_asn(obj.document.asn)


class ContractRecordSerializer(serializers.ModelSerializer):
    """Vertragsdatensatz für Contract Center."""

    document_title = serializers.CharField(source="document.title", read_only=True)
    case_file_title = serializers.CharField(
        source="case_file.title", read_only=True, default=None
    )
    contract_type_label = serializers.SerializerMethodField()
    billing_cycle_label = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    source_label = serializers.SerializerMethodField()
    provider_display = serializers.SerializerMethodField()

    class Meta:
        model = ContractRecord
        fields = (
            "id",
            "document",
            "document_title",
            "case_file",
            "case_file_title",
            "contract_type",
            "contract_type_label",
            "provider",
            "provider_display",
            "contract_number",
            "amount",
            "currency",
            "billing_cycle",
            "billing_cycle_label",
            "starts_on",
            "ends_on",
            "notice_period_days",
            "cancel_until",
            "next_due_on",
            "status",
            "status_label",
            "confidence",
            "source",
            "source_label",
            "needs_review",
            "extracted_from_version",
            "notes",
            "reviewed_at",
            "reviewed_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "document_title",
            "case_file_title",
            "contract_type_label",
            "billing_cycle_label",
            "status_label",
            "source_label",
            "provider_display",
            "confidence",
            "source",
            "extracted_from_version",
            "reviewed_at",
            "reviewed_by",
            "created_at",
            "updated_at",
        )

    def get_contract_type_label(self, obj) -> str:
        return obj.get_contract_type_display()

    def get_billing_cycle_label(self, obj) -> str:
        return obj.get_billing_cycle_display()

    def get_status_label(self, obj) -> str:
        return obj.get_status_display()

    def get_source_label(self, obj) -> str:
        return obj.get_source_display()

    def get_provider_display(self, obj) -> str:
        return obj.provider or (
            obj.document.correspondent.name
            if obj.document and obj.document.correspondent_id
            else ""
        )


class EntityIdentifierSerializer(serializers.ModelSerializer):
    kind_label = serializers.SerializerMethodField()

    class Meta:
        model = EntityIdentifier
        fields = (
            "id",
            "entity",
            "kind",
            "kind_label",
            "value",
            "normalized_value",
            "source",
            "confidence",
            "created_at",
        )
        read_only_fields = ("normalized_value", "created_at")

    def get_kind_label(self, obj) -> str:
        return obj.get_kind_display()


class DocumentEntitySerializer(serializers.ModelSerializer):
    document_title = serializers.CharField(source="document.title", read_only=True)
    entity_name = serializers.CharField(source="entity.name", read_only=True)
    entity_kind = serializers.CharField(source="entity.kind", read_only=True)
    entity_kind_label = serializers.CharField(
        source="entity.get_kind_display", read_only=True
    )
    role_label = serializers.SerializerMethodField()
    source_label = serializers.SerializerMethodField()

    class Meta:
        model = DocumentEntity
        fields = (
            "id",
            "document",
            "document_title",
            "entity",
            "entity_name",
            "entity_kind",
            "entity_kind_label",
            "role",
            "role_label",
            "source",
            "source_label",
            "confidence",
            "occurrences",
            "source_snippet",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_role_label(self, obj) -> str:
        return obj.get_role_display()

    def get_source_label(self, obj) -> str:
        return obj.get_source_display()


class EntityRelationSerializer(serializers.ModelSerializer):
    from_name = serializers.CharField(source="from_entity.name", read_only=True)
    to_name = serializers.CharField(source="to_entity.name", read_only=True)
    from_kind = serializers.CharField(source="from_entity.kind", read_only=True)
    to_kind = serializers.CharField(source="to_entity.kind", read_only=True)
    document_title = serializers.CharField(source="document.title", read_only=True, default=None)
    relation_type_label = serializers.SerializerMethodField()
    source_label = serializers.SerializerMethodField()

    class Meta:
        model = EntityRelation
        fields = (
            "id",
            "from_entity",
            "from_name",
            "from_kind",
            "to_entity",
            "to_name",
            "to_kind",
            "relation_type",
            "relation_type_label",
            "document",
            "document_title",
            "confidence",
            "source",
            "source_label",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_relation_type_label(self, obj) -> str:
        return obj.get_relation_type_display()

    def get_source_label(self, obj) -> str:
        return obj.get_source_display()


class KnowledgeEntitySerializer(serializers.ModelSerializer):
    kind_label = serializers.SerializerMethodField()
    source_label = serializers.SerializerMethodField()
    identifiers = EntityIdentifierSerializer(many=True, read_only=True)
    document_count = serializers.SerializerMethodField()
    relation_count = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeEntity
        fields = (
            "id",
            "owner",
            "kind",
            "kind_label",
            "name",
            "canonical_name",
            "confidence",
            "source",
            "source_label",
            "metadata",
            "identifiers",
            "document_count",
            "relation_count",
            "first_seen_at",
            "last_seen_at",
        )
        read_only_fields = (
            "owner",
            "canonical_name",
            "confidence",
            "source_label",
            "identifiers",
            "document_count",
            "relation_count",
            "first_seen_at",
            "last_seen_at",
        )

    def get_kind_label(self, obj) -> str:
        return obj.get_kind_display()

    def get_source_label(self, obj) -> str:
        return obj.get_source_display()

    def get_document_count(self, obj) -> int:
        return getattr(obj, "document_count", None) or obj.document_links.values(
            "document_id"
        ).distinct().count()

    def get_relation_count(self, obj) -> int:
        annotated = getattr(obj, "relation_count", None)
        if annotated is not None:
            return annotated
        return obj.outgoing_relations.count() + obj.incoming_relations.count()


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
    folder_name = serializers.CharField(
        source="folder.name", read_only=True, default=None
    )
    folder_path = serializers.CharField(
        source="folder.full_path", read_only=True, default=None
    )
    case_file_title = serializers.CharField(
        source="case_file.title", read_only=True, default=None
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
    review_tasks = serializers.SerializerMethodField()
    review_task_count = serializers.SerializerMethodField()
    archive_status_label = serializers.SerializerMethodField()
    retention_state = serializers.SerializerMethodField()
    # Soft-Merge von Dubletten: Titel des kanonischen Dokuments + Anzahl der
    # Dubletten, die dieses Dokument ersetzt (fürs „ersetzt durch"-Banner).
    superseded_by_title = serializers.CharField(
        source="superseded_by.title", read_only=True, default=None
    )
    supersedes_count = serializers.SerializerMethodField()
    # Familien-Freigabe: Eigentümer-Name, damit für den Haushalt freigegebene
    # Fremd-Dokumente in der Liste/Detail zeigen, von wem sie stammen.
    owner_username = serializers.CharField(
        source="owner.username", read_only=True, default=None
    )
    is_owner = serializers.SerializerMethodField()

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
            "folder",
            "folder_name",
            "folder_path",
            "case_file",
            "case_file_title",
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
            "review_status",
            "retention_until",
            "retention_state",
            "legal_hold",
            "legal_hold_reason",
            "legal_hold_set_at",
            "archive_status",
            "archive_status_label",
            "archive_checked_at",
            "archive_error",
            "review_task_count",
            "review_tasks",
            "superseded_by",
            "superseded_by_title",
            "superseded_at",
            "supersedes_count",
            "shared_with_household",
            "owner_username",
            "is_owner",
            "custom_field_values",
            "versions",
        )
        read_only_fields = (
            "added_at",
            "current_version",
            "superseded_by",
            "superseded_at",
            "shared_with_household",
            "owner",  # Eigentümer serverseitig gesetzt – nicht per Request änderbar (STOAA-7)
            "case_file",  # Zuordnung nur über CaseFileViewSet-Actions (Owner-Scope).
            "asn",  # unveränderlich, serverseitig vergeben (STOAA-284/285)
            "ai_suggestions",
            "ai_suggested_at",
            "classification",
            "status",  # Statuswechsel NUR über submit/approve/reject – nie per PATCH (STOAA-63)
            "review_status",  # Review-Wechsel nur über mark_reviewed (Inbox-Workflow).
            "retention_until",
            "retention_state",
            "legal_hold",
            "legal_hold_reason",
            "legal_hold_set_at",
            "archive_status",
            "archive_status_label",
            "archive_checked_at",
            "archive_error",
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

    def get_review_tasks(self, obj):
        tasks = [
            task
            for task in obj.review_tasks.all()
            if task.status == DocumentReviewTask.Status.OPEN
        ]
        tasks.sort(key=lambda task: (task.priority, task.created_at, task.id))
        return DocumentReviewTaskSerializer(tasks, many=True).data

    def get_review_task_count(self, obj) -> int:
        return sum(
            1
            for task in obj.review_tasks.all()
            if task.status == DocumentReviewTask.Status.OPEN
        )

    def get_archive_status_label(self, obj) -> str:
        return obj.get_archive_status_display()

    def get_is_owner(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and obj.owner_id == getattr(request.user, "id", None))

    def get_supersedes_count(self, obj) -> int:
        # Annotation aus get_queryset bevorzugen (kein N+1); sonst zählen.
        annotated = getattr(obj, "supersedes_count_ann", None)
        if annotated is not None:
            return annotated
        return obj.supersedes.count()

    def get_retention_state(self, obj) -> dict:
        from documents.services import archive as archive_service

        return archive_service.retention_state(obj)

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


class ProcessedMailSerializer(serializers.ModelSerializer):
    """Mail-Center-Zeile für importierte/verarbeitete IMAP-Mails."""

    account_name = serializers.CharField(source="account.name", read_only=True)
    status_label = serializers.SerializerMethodField()
    imported_documents = serializers.SerializerMethodField()

    class Meta:
        model = ProcessedMail
        fields = (
            "id",
            "account",
            "account_name",
            "message_id",
            "subject",
            "sender",
            "received_at",
            "status",
            "status_label",
            "attachment_count",
            "imported_count",
            "attachment_names",
            "imported_documents",
            "note",
            "error",
            "processed_at",
        )
        read_only_fields = (
            "id",
            "account",
            "account_name",
            "message_id",
            "subject",
            "sender",
            "received_at",
            "status_label",
            "attachment_count",
            "imported_count",
            "attachment_names",
            "imported_documents",
            "error",
            "processed_at",
        )

    def get_status_label(self, obj) -> str:
        return obj.get_status_display()

    def get_imported_documents(self, obj):
        documents = obj.documents.select_related(
            "correspondent",
            "document_type",
            "folder",
            "current_version",
        ).order_by("-added_at", "-id")
        return CaseFileDocumentSerializer(documents, many=True).data


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


class DocumentReminderSerializer(serializers.ModelSerializer):
    """Serializer für Wiedervorlagen/Erinnerungen (STOAA-372 PR1).

    ``document`` ist schreibbar (FK-ID). ``created_by`` und ``notified_at``
    sind read-only: der Ersteller wird im ViewSet aus ``request.user`` gesetzt,
    ``notified_at`` ausschließlich vom Beat ``check_due_reminders``.
    """

    class Meta:
        model = DocumentReminder
        fields = [
            "id",
            "document",
            "remind_on",
            "note",
            "done",
            "created_by",
            "notified_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by", "notified_at", "created_at", "updated_at"]
