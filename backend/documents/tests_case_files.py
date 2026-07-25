from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from .models import (
    AuditLogEntry,
    CaseFile,
    Document,
    DocumentFolder,
    DocumentVersion,
)

User = get_user_model()


class CaseFileTests(APITestCase):
    """Vorgangsakten: Owner-Scope, Dokumentzuordnung und Zusammenfassung."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="case_owner", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="case_other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="case_guest", password="pw", role="guest"
        )
        cls.owner_doc = cls._doc(
            "Wüstenrot Polizze",
            cls.owner,
            "Polizze Wüstenrot. Beitrag 225,74 Euro monatlich.",
        )
        cls.other_doc = cls._doc("Fremdes Dokument", cls.other, "Vertraulich")

    @classmethod
    def _doc(cls, title, owner, text):
        doc = Document.objects.create(title=title, owner=owner)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/tmp/{title}.pdf",
            sha256=title.encode().hex().ljust(64, "0")[:64],
            ocr_text=text,
            processing_state=DocumentVersion.ProcessingState.READY,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_case_file_create_sets_owner_and_audit(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/case-files/",
            {"title": "Versicherung Wüstenrot", "description": "Polizzenvorgang"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        case_file = CaseFile.objects.get(id=resp.data["id"])
        self.assertEqual(case_file.owner, self.owner)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="case_file_create",
                object_type="CaseFile",
                object_id=str(case_file.id),
            ).exists()
        )

    def test_owner_scope_verhindert_fremde_akten(self):
        CaseFile.objects.create(title="Fremdakte", owner=self.other)
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/case-files/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"], [])

    def test_add_documents_ordner_nur_sichtbare_dokumente_zu(self):
        case_file = CaseFile.objects.create(title="Versicherung", owner=self.owner)
        self.client.force_authenticate(self.owner)

        resp = self.client.post(
            f"/api/case-files/{case_file.id}/add-documents/",
            {"ids": [self.owner_doc.id, self.other_doc.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.owner_doc.refresh_from_db()
        self.other_doc.refresh_from_db()
        self.assertEqual(self.owner_doc.case_file, case_file)
        self.assertIsNone(self.other_doc.case_file)
        self.assertEqual(resp.data["document_count"], 1)

    def test_admin_darf_fremde_dokumente_nicht_in_benutzerakte_legen(self):
        # P2: Fuer Admins liefert _visible_documents() alle Dokumente
        # owner-unabhaengig. Trotzdem darf eine Akte nur Dokumente DESSELBEN
        # Eigentuemers buendeln – sonst landet ein fremdes Dokument in einer
        # benutzereigenen Akte (Owner-Mix).
        admin = User.objects.create_user(
            username="case_admin", password="pw", role="admin"
        )
        case_file = CaseFile.objects.create(title="Owner-Akte", owner=self.owner)
        self.client.force_authenticate(admin)

        resp = self.client.post(
            f"/api/case-files/{case_file.id}/add-documents/",
            {"ids": [self.owner_doc.id, self.other_doc.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.owner_doc.refresh_from_db()
        self.other_doc.refresh_from_db()
        # Nur das Dokument des Akten-Eigentuemers wird verknuepft.
        self.assertEqual(self.owner_doc.case_file, case_file)
        self.assertIsNone(self.other_doc.case_file)
        self.assertEqual(resp.data["document_count"], 1)

    def test_remove_documents_entfernt_zuordnung(self):
        case_file = CaseFile.objects.create(title="Versicherung", owner=self.owner)
        self.owner_doc.case_file = case_file
        self.owner_doc.save(update_fields=["case_file"])
        self.client.force_authenticate(self.owner)

        resp = self.client.post(
            f"/api/case-files/{case_file.id}/remove-documents/",
            {"ids": [self.owner_doc.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.owner_doc.refresh_from_db()
        self.assertIsNone(self.owner_doc.case_file)
        self.assertEqual(resp.data["document_count"], 0)

    def test_summarize_speichert_fallback_summary_mit_quellen(self):
        case_file = CaseFile.objects.create(title="Versicherung", owner=self.owner)
        self.owner_doc.case_file = case_file
        self.owner_doc.save(update_fields=["case_file"])
        self.client.force_authenticate(self.owner)

        resp = self.client.post(f"/api/case-files/{case_file.id}/summarize/")

        self.assertEqual(resp.status_code, 200)
        case_file.refresh_from_db()
        self.assertTrue(case_file.ai_summary)
        self.assertIn(resp.data["source"], {"local", "unavailable", "ai", "error"})
        self.assertEqual(resp.data["sources"][0]["document"], self.owner_doc.id)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="case_file_summarize",
                object_id=str(case_file.id),
            ).exists()
        )

    def test_guest_darf_akten_nicht_schreiben(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post("/api/case-files/", {"title": "Nein"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_akten_liste_kein_n_plus_1(self):
        # P2: Die Query-Zahl der Aktenliste bleibt KONSTANT, egal wie viele
        # Dokumente die Akten enthalten. Frueher hing get_documents ein
        # order_by() an den Related Manager und verwarf den Prefetch-Cache -> je
        # Dokument eine Extra-Query. Die Dokumente liegen in VERSCHACHTELTEN
        # Ordnern, damit auch folder.full_path (Eltern-Traversierung) vorgeladen
        # sein muss und nicht je Ordnerebene erneut anfragt.
        root = DocumentFolder.objects.create(name="Root", owner=self.owner)
        child = DocumentFolder.objects.create(
            name="Kind", parent=root, owner=self.owner
        )
        leaf = DocumentFolder.objects.create(
            name="Blatt", parent=child, owner=self.owner
        )
        case_file = CaseFile.objects.create(title="Sammelakte", owner=self.owner)
        self.owner_doc.case_file = case_file
        self.owner_doc.folder = leaf
        self.owner_doc.save(update_fields=["case_file", "folder"])
        self.client.force_authenticate(self.owner)

        with CaptureQueriesContext(connection) as ctx1:
            resp = self.client.get("/api/case-files/")
        self.assertEqual(resp.status_code, 200)
        baseline = len(ctx1)

        # Weitere Dokumente (ebenfalls im tiefen Ordner) -> Query-Zahl KONSTANT.
        for i in range(5):
            doc = self._doc(f"Beleg {i}", self.owner, "Inhalt")
            doc.case_file = case_file
            doc.folder = leaf
            doc.save(update_fields=["case_file", "folder"])

        with CaptureQueriesContext(connection) as ctx2:
            resp = self.client.get("/api/case-files/")
        self.assertEqual(resp.status_code, 200)
        docs = resp.data["results"][0]["documents"]
        self.assertEqual(len(docs), 6)
        self.assertEqual(docs[0]["folder_path"], "Root / Kind / Blatt")
        self.assertEqual(len(ctx2), baseline)

    def test_akten_liste_kein_n_plus_1_bei_tiefen_ordnern(self):
        # P3: full_path traversiert die GANZE Kette; select_related laedt nur zwei
        # Ebenen. Bei tiefer Verschachtelung (hier 5 Ebenen) muss die Query-Zahl
        # dank Ahnen-Priming trotzdem KONSTANT bleiben (haengt nur an der Tiefe,
        # nicht an der Dokumentanzahl).
        names = ["E1", "E2", "E3", "E4", "E5"]
        parent = None
        for name in names:
            parent = DocumentFolder.objects.create(
                name=name, parent=parent, owner=self.owner
            )
        leaf = parent
        expected_path = " / ".join(names)

        case_file = CaseFile.objects.create(title="Tiefakte", owner=self.owner)
        self.owner_doc.case_file = case_file
        self.owner_doc.folder = leaf
        self.owner_doc.save(update_fields=["case_file", "folder"])
        self.client.force_authenticate(self.owner)

        with CaptureQueriesContext(connection) as ctx1:
            resp = self.client.get("/api/case-files/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"][0]["documents"][0]["folder_path"], expected_path)
        baseline = len(ctx1)

        for i in range(5):
            doc = self._doc(f"Tief {i}", self.owner, "Inhalt")
            doc.case_file = case_file
            doc.folder = leaf
            doc.save(update_fields=["case_file", "folder"])

        with CaptureQueriesContext(connection) as ctx2:
            resp = self.client.get("/api/case-files/")
        self.assertEqual(resp.status_code, 200)
        docs = resp.data["results"][0]["documents"]
        self.assertEqual(len(docs), 6)
        self.assertTrue(all(d["folder_path"] == expected_path for d in docs))
        self.assertEqual(len(ctx2), baseline)

    def _case_with_deep_folder(self, idx):
        # Eigene 4-stufige Ordnerkette + eigene Akte mit einem Dokument darin.
        parent = None
        for level in range(4):
            parent = DocumentFolder.objects.create(
                name=f"A{idx}-L{level}", parent=parent, owner=self.owner
            )
        case_file = CaseFile.objects.create(title=f"Akte {idx}", owner=self.owner)
        doc = self._doc(f"Doc {idx}", self.owner, "Inhalt")
        doc.case_file = case_file
        doc.folder = parent
        doc.save(update_fields=["case_file", "folder"])

    def test_akten_liste_kein_n_plus_1_bei_vielen_akten(self):
        # P2: Der Ancestor-Prime läuft auf LISTEN-Ebene (einmal für alle Akten der
        # Antwort). Die Query-Zahl darf daher nicht mit der ANZAHL AKTEN skalieren.
        self.owner_doc.delete()
        self.other_doc.delete()
        self.client.force_authenticate(self.owner)

        self._case_with_deep_folder(0)
        with CaptureQueriesContext(connection) as ctx1:
            resp = self.client.get("/api/case-files/")
        self.assertEqual(resp.status_code, 200)
        baseline = len(ctx1)

        for idx in range(1, 5):
            self._case_with_deep_folder(idx)

        with CaptureQueriesContext(connection) as ctx2:
            resp = self.client.get("/api/case-files/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 5)
        # Konstante Query-Zahl trotz 5x so vieler Akten (jede mit tiefem Ordner).
        self.assertEqual(len(ctx2), baseline)
