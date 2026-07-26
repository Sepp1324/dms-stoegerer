"""P2: Das Mail-Center nutzt den Dokument-Prefetch (kein N+1 je Mail/Ordnertiefe)."""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from .models import (
    Document,
    DocumentFolder,
    DocumentVersion,
    MailAccount,
    ProcessedMail,
)

User = get_user_model()


class MailCenterPrefetchTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("mc_admin", password="pw", role="admin")
        cls.account = MailAccount.objects.create(
            name="Rechnungen", host="mail.example.com", username="u", owner=cls.admin
        )
        # Dreistufiger Ordnerbaum -> full_path traversiert die Kette.
        root = DocumentFolder.objects.create(name="Root", owner=cls.admin)
        child = DocumentFolder.objects.create(name="Kind", parent=root, owner=cls.admin)
        cls.leaf = DocumentFolder.objects.create(name="Blatt", parent=child, owner=cls.admin)

    def _mail_with_docs(self, idx, doc_count):
        mail = ProcessedMail.objects.create(
            account=self.account,
            message_id=f"<m{idx}@example.com>",
            status=ProcessedMail.Status.IMPORTED,
        )
        for d in range(doc_count):
            doc = Document.objects.create(title=f"m{idx}-d{d}", owner=self.admin, folder=self.leaf)
            DocumentVersion.objects.create(
                document=doc, version_no=1, file_path="/tmp/x.pdf",
                sha256=f"{idx}{d}".ljust(64, "0")[:64],
            )
            mail.documents.add(doc)
        return mail

    def test_liste_kein_n_plus_1_ueber_mails_und_dokumente(self):
        self.client.force_authenticate(self.admin)
        self._mail_with_docs(0, doc_count=1)

        with CaptureQueriesContext(connection) as ctx1:
            resp = self.client.get("/api/processed-mails/")
        self.assertEqual(resp.status_code, 200)
        baseline = len(ctx1)

        # Mehr Mails UND mehr Dokumente je Mail -> Query-Zahl bleibt konstant.
        for idx in range(1, 4):
            self._mail_with_docs(idx, doc_count=3)

        with CaptureQueriesContext(connection) as ctx2:
            resp = self.client.get("/api/processed-mails/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 4)
        # folder_path korrekt aufgelöst (Prefetch/Priming greift).
        docs = [
            d
            for row in resp.data["results"]
            for d in row["imported_documents"]
        ]
        self.assertTrue(docs)
        self.assertTrue(all(d["folder_path"] == "Root / Kind / Blatt" for d in docs))
        self.assertEqual(len(ctx2), baseline)
