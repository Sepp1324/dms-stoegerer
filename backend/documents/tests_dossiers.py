from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from .models import (
    AuditLogEntry,
    Dossier,
    Document,
    DocumentFolder,
    DocumentPageText,
    DocumentVersion,
)

User = get_user_model()


def make_doc(owner, title, text):
    doc = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path="/tmp/dossier.pdf",
        sha256="c" * 64,
        ocr_text=text,
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    DocumentPageText.objects.create(version=version, page_no=1, text=text)
    return doc


class DossierApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="dossier-user", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="dossier-other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="dossier-guest", password="pw", role="guest"
        )
        cls.own_doc = make_doc(
            cls.user,
            "Helvetia Polizze",
            "Helvetia Versicherung Polizze fuer Cornelia mit Praemie 12,50 Euro.",
        )
        cls.foreign_doc = make_doc(
            cls.other,
            "Fremde Helvetia Polizze",
            "Helvetia Versicherung fremder Inhalt darf nicht ins Dossier.",
        )

    def test_generate_dossier_uses_only_visible_documents(self):
        self.client.force_authenticate(self.user)
        create_resp = self.client.post(
            "/api/dossiers/",
            {"title": "Helvetia", "query": "Alles zur Helvetia Polizze"},
            format="json",
        )
        self.assertEqual(create_resp.status_code, 201)

        resp = self.client.post(f"/api/dossiers/{create_resp.data['id']}/generate/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], Dossier.Status.GENERATED)
        source_doc_ids = {source["document"] for source in resp.data["sources"]}
        self.assertIn(self.own_doc.id, source_doc_ids)
        self.assertNotIn(self.foreign_doc.id, source_doc_ids)
        self.assertTrue(resp.data["summary"])

    def test_status_ist_read_only_kein_final_per_patch(self):
        """P2: status ist read-only – ein PATCH kann NICHT ohne Finalisierungs-Audit
        auf FINAL setzen. Andere Felder (Titel) bleiben editierbar (Draft)."""
        self.client.force_authenticate(self.user)
        d = Dossier.objects.create(
            owner=self.user, title="T", query="q", status=Dossier.Status.DRAFT
        )
        resp = self.client.patch(
            f"/api/dossiers/{d.id}/",
            {"status": "final", "title": "Umbenannt"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        d.refresh_from_db()
        self.assertEqual(d.status, Dossier.Status.DRAFT)  # status ignoriert
        self.assertEqual(d.title, "Umbenannt")  # Titel schon editierbar

    def test_finalize_action_setzt_final_und_auditiert(self):
        self.client.force_authenticate(self.user)
        d = Dossier.objects.create(
            owner=self.user, title="T", query="q", status=Dossier.Status.GENERATED
        )
        resp = self.client.post(f"/api/dossiers/{d.id}/finalize/")
        self.assertEqual(resp.status_code, 200)
        d.refresh_from_db()
        self.assertEqual(d.status, Dossier.Status.FINAL)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="dossier_finalize", object_id=str(d.id)
            ).exists()
        )

    def test_finales_dossier_ist_unveraenderlich(self):
        """P2: Ein finalisiertes Dossier lässt sich weder inhaltlich ändern …"""
        self.client.force_authenticate(self.user)
        d = Dossier.objects.create(
            owner=self.user, title="T", query="q", status=Dossier.Status.FINAL
        )
        resp = self.client.patch(
            f"/api/dossiers/{d.id}/", {"title": "Neu"}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        d.refresh_from_db()
        self.assertEqual(d.title, "T")

    def test_finales_dossier_nicht_auf_draft_zurueck(self):
        """… noch über einen PATCH auf status wieder auf DRAFT zurückstellen."""
        self.client.force_authenticate(self.user)
        d = Dossier.objects.create(
            owner=self.user, title="T", query="q", status=Dossier.Status.FINAL
        )
        resp = self.client.patch(
            f"/api/dossiers/{d.id}/", {"status": "draft"}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        d.refresh_from_db()
        self.assertEqual(d.status, Dossier.Status.FINAL)

    def test_export_markdown_contains_sources(self):
        self.client.force_authenticate(self.user)
        dossier = Dossier.objects.create(
            owner=self.user,
            title="Export",
            query="Helvetia",
            status=Dossier.Status.GENERATED,
            summary="Kurzfassung [S1].",
            sources=[
                {
                    "id": "S1",
                    "document": self.own_doc.id,
                    "document_title": self.own_doc.title,
                    "page": 1,
                    "snippet": "Helvetia Versicherung",
                }
            ],
        )
        dossier.documents.add(self.own_doc)

        resp = self.client.get(f"/api/dossiers/{dossier.id}/export-markdown/")

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("# Export", body)
        self.assertIn("Helvetia Versicherung", body)

    def test_guest_cannot_generate(self):
        dossier = Dossier.objects.create(
            owner=self.guest,
            title="Gast",
            query="Helvetia",
        )
        self.client.force_authenticate(self.guest)

        resp = self.client.post(f"/api/dossiers/{dossier.id}/generate/")

        self.assertEqual(resp.status_code, 403)

    def test_dossier_liste_kein_n_plus_1(self):
        # P2: Query-Zahl der Dossierliste bleibt KONSTANT, unabhaengig von der
        # Anzahl verknuepfter Dokumente. Frueher hing get_documents ein
        # select_related() an den Related Manager und verwarf den Prefetch-Cache.
        # Verschachtelte Ordner: folder.full_path traversiert die Eltern-Kette,
        # die daher vorgeladen sein muss (sonst je Ordnerebene eine Query).
        root = DocumentFolder.objects.create(name="Root", owner=self.user)
        child = DocumentFolder.objects.create(
            name="Kind", parent=root, owner=self.user
        )
        leaf = DocumentFolder.objects.create(
            name="Blatt", parent=child, owner=self.user
        )
        dossier = Dossier.objects.create(
            owner=self.user, title="Sammel", query="Helvetia"
        )
        self.own_doc.folder = leaf
        self.own_doc.save(update_fields=["folder"])
        dossier.documents.add(self.own_doc)
        self.client.force_authenticate(self.user)

        with CaptureQueriesContext(connection) as ctx1:
            resp = self.client.get("/api/dossiers/")
        self.assertEqual(resp.status_code, 200)
        baseline = len(ctx1)

        for i in range(5):
            doc = make_doc(self.user, f"Extra {i}", "Helvetia Inhalt")
            doc.folder = leaf
            doc.save(update_fields=["folder"])
            dossier.documents.add(doc)

        with CaptureQueriesContext(connection) as ctx2:
            resp = self.client.get("/api/dossiers/")
        self.assertEqual(resp.status_code, 200)
        docs = resp.data["results"][0]["documents"]
        self.assertEqual(len(docs), 6)
        self.assertEqual(docs[0]["folder_path"], "Root / Kind / Blatt")
        self.assertEqual(len(ctx2), baseline)
