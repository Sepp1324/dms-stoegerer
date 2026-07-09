from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import (
    Correspondent,
    Document,
    DocumentEntity,
    DocumentVersion,
    EntityRelation,
    KnowledgeEntity,
)
from .services import entity_graph

User = get_user_model()


class EntityGraphTests(APITestCase):
    """DMS-Gedächtnis: Entitäten, Beziehungen und Owner-Isolation."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="graph_owner", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="graph_other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="graph_guest", password="pw", role="guest"
        )
        cls.correspondent = Correspondent.objects.create(name="Helvetia Versicherungen AG")

    def _doc(self, title, owner, text, *, correspondent=None):
        doc = Document.objects.create(
            title=title,
            owner=owner,
            correspondent=correspondent,
            mail_sender="service@example.test",
        )
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/tmp/entity-{doc.id}.pdf",
            sha256=f"{doc.id:064x}"[-64:],
            ocr_text=text,
            processing_state=DocumentVersion.ProcessingState.READY,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_sync_extracts_entities_links_and_relations(self):
        doc = self._doc(
            "Polizze Cornelia",
            self.owner,
            """
            Frau Cornelia Stögerer
            Helvetia Versicherungen AG
            Vertragsnummer HV-2026-8842
            Kundennummer K-123456
            IBAN AT611904300234573201
            Kontakt service@helvetia.test
            """,
            correspondent=self.correspondent,
        )

        result = entity_graph.sync_document_entities(doc, actor=self.owner)

        self.assertEqual(result["status"], "synced")
        self.assertGreaterEqual(result["entities"], 5)
        self.assertTrue(
            KnowledgeEntity.objects.filter(
                owner=self.owner,
                kind=KnowledgeEntity.Kind.PERSON,
                canonical_name__icontains="cornelia",
            ).exists()
        )
        self.assertTrue(
            KnowledgeEntity.objects.filter(
                owner=self.owner,
                kind=KnowledgeEntity.Kind.IBAN,
                canonical_name="AT611904300234573201",
            ).exists()
        )
        self.assertTrue(DocumentEntity.objects.filter(document=doc).exists())
        self.assertTrue(EntityRelation.objects.filter(document=doc).exists())

    def test_entity_api_is_owner_scoped(self):
        own_doc = self._doc(
            "Eigener Vertrag",
            self.owner,
            "Frau Cornelia Stögerer IBAN AT611904300234573201",
        )
        foreign_doc = self._doc(
            "Fremder Vertrag",
            self.other,
            "Frau Cornelia Stögerer IBAN AT611904300234573201",
        )
        entity_graph.sync_document_entities(own_doc, actor=self.owner)
        entity_graph.sync_document_entities(foreign_doc, actor=self.other)
        foreign_entity = KnowledgeEntity.objects.filter(owner=self.other).first()

        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/knowledge-entities/")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["results"])
        self.assertTrue(all(item["owner"] == self.owner.id for item in resp.data["results"]))

        resp = self.client.get(f"/api/knowledge-entities/{foreign_entity.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_scan_endpoint_processes_only_visible_documents(self):
        own_doc = self._doc(
            "Scan eigen",
            self.owner,
            "Frau Cornelia Stögerer IBAN AT611904300234573201",
        )
        foreign_doc = self._doc(
            "Scan fremd",
            self.other,
            "Frau Cornelia Stögerer IBAN AT611904300234573201",
        )

        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/knowledge-entities/scan/",
            {"ids": [own_doc.id, foreign_doc.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["scanned"], 1)
        self.assertTrue(DocumentEntity.objects.filter(document=own_doc).exists())
        self.assertFalse(DocumentEntity.objects.filter(document=foreign_doc).exists())

    def test_guest_cannot_scan_entities(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post("/api/knowledge-entities/scan/", {}, format="json")
        self.assertEqual(resp.status_code, 403)
