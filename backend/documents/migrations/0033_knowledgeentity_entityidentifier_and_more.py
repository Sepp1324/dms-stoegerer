from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0032_contractrecord"),
    ]

    operations = [
        migrations.CreateModel(
            name="KnowledgeEntity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("person", "Person"),
                            ("company", "Firma"),
                            ("authority", "Behörde"),
                            ("iban", "IBAN"),
                            ("email", "E-Mail"),
                            ("phone", "Telefon"),
                            ("contract_number", "Vertragsnummer"),
                            ("policy_number", "Polizzennummer"),
                            ("customer_number", "Kundennummer"),
                            ("tax_number", "Steuernummer"),
                            ("address", "Adresse"),
                            ("other", "Sonstiges"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("canonical_name", models.CharField(max_length=255)),
                ("confidence", models.PositiveSmallIntegerField(default=50)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("ocr", "OCR"),
                            ("metadata", "Metadaten"),
                            ("mail", "E-Mail"),
                            ("contract", "Contract Center"),
                            ("manual", "Manuell"),
                            ("heuristic", "Heuristik"),
                        ],
                        default="heuristic",
                        max_length=24,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("first_seen_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="knowledge_entities",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Entität",
                "verbose_name_plural": "Entitäten",
                "ordering": ["kind", "name"],
            },
        ),
        migrations.CreateModel(
            name="EntityIdentifier",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("person", "Person"),
                            ("company", "Firma"),
                            ("authority", "Behörde"),
                            ("iban", "IBAN"),
                            ("email", "E-Mail"),
                            ("phone", "Telefon"),
                            ("contract_number", "Vertragsnummer"),
                            ("policy_number", "Polizzennummer"),
                            ("customer_number", "Kundennummer"),
                            ("tax_number", "Steuernummer"),
                            ("address", "Adresse"),
                            ("other", "Sonstiges"),
                        ],
                        max_length=32,
                    ),
                ),
                ("value", models.CharField(max_length=255)),
                ("normalized_value", models.CharField(max_length=255)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("ocr", "OCR"),
                            ("metadata", "Metadaten"),
                            ("mail", "E-Mail"),
                            ("contract", "Contract Center"),
                            ("manual", "Manuell"),
                            ("heuristic", "Heuristik"),
                        ],
                        default="heuristic",
                        max_length=24,
                    ),
                ),
                ("confidence", models.PositiveSmallIntegerField(default=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identifiers",
                        to="documents.knowledgeentity",
                    ),
                ),
            ],
            options={
                "verbose_name": "Entitäts-Identifier",
                "verbose_name_plural": "Entitäts-Identifier",
                "indexes": [
                    models.Index(
                        fields=["kind", "normalized_value"],
                        name="docs_ident_kind_value",
                    ),
                ],
                "unique_together": {("entity", "kind", "normalized_value")},
            },
        ),
        migrations.CreateModel(
            name="DocumentEntity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("mention", "Erwähnung"),
                            ("correspondent", "Korrespondent"),
                            ("sender", "Absender"),
                            ("recipient", "Empfänger"),
                            ("subject", "Betreff"),
                            ("contract", "Vertrag"),
                            ("account", "Konto"),
                            ("reference", "Referenz"),
                        ],
                        default="mention",
                        max_length=24,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("ocr", "OCR"),
                            ("metadata", "Metadaten"),
                            ("mail", "E-Mail"),
                            ("contract", "Contract Center"),
                            ("manual", "Manuell"),
                            ("heuristic", "Heuristik"),
                        ],
                        default="heuristic",
                        max_length=24,
                    ),
                ),
                ("confidence", models.PositiveSmallIntegerField(default=50)),
                ("occurrences", models.PositiveIntegerField(default=1)),
                ("source_snippet", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entity_links",
                        to="documents.document",
                    ),
                ),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_links",
                        to="documents.knowledgeentity",
                    ),
                ),
            ],
            options={
                "verbose_name": "Dokument-Entität",
                "verbose_name_plural": "Dokument-Entitäten",
                "indexes": [
                    models.Index(fields=["document", "role"], name="docs_docent_doc_role"),
                    models.Index(fields=["entity", "role"], name="docs_docent_ent_role"),
                ],
                "unique_together": {("document", "entity", "role", "source")},
            },
        ),
        migrations.CreateModel(
            name="EntityRelation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "relation_type",
                    models.CharField(
                        choices=[
                            ("related", "Verbunden"),
                            ("mentioned_with", "Gemeinsam erwähnt"),
                            ("uses_identifier", "Nutzt Identifier"),
                            ("contract_with", "Vertrag mit"),
                            ("same_as", "Identisch"),
                        ],
                        default="related",
                        max_length=32,
                    ),
                ),
                ("confidence", models.PositiveSmallIntegerField(default=50)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("ocr", "OCR"),
                            ("metadata", "Metadaten"),
                            ("mail", "E-Mail"),
                            ("contract", "Contract Center"),
                            ("manual", "Manuell"),
                            ("heuristic", "Heuristik"),
                        ],
                        default="heuristic",
                        max_length=24,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "document",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entity_relations",
                        to="documents.document",
                    ),
                ),
                (
                    "from_entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outgoing_relations",
                        to="documents.knowledgeentity",
                    ),
                ),
                (
                    "to_entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="incoming_relations",
                        to="documents.knowledgeentity",
                    ),
                ),
            ],
            options={
                "verbose_name": "Entitätsbeziehung",
                "verbose_name_plural": "Entitätsbeziehungen",
                "indexes": [
                    models.Index(
                        fields=["from_entity", "relation_type"],
                        name="docs_rel_from_type",
                    ),
                    models.Index(
                        fields=["to_entity", "relation_type"],
                        name="docs_rel_to_type",
                    ),
                ],
                "unique_together": {("from_entity", "to_entity", "relation_type", "document")},
            },
        ),
        migrations.AddIndex(
            model_name="knowledgeentity",
            index=models.Index(fields=["owner", "kind"], name="docs_ent_owner_kind"),
        ),
        migrations.AddIndex(
            model_name="knowledgeentity",
            index=models.Index(fields=["kind", "canonical_name"], name="docs_ent_kind_name"),
        ),
        migrations.AddConstraint(
            model_name="knowledgeentity",
            constraint=models.UniqueConstraint(
                fields=("owner", "kind", "canonical_name"),
                name="docs_ent_owner_kind_name",
            ),
        ),
    ]
