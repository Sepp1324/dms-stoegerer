"""P2: E-Mail-Wiederaufnahme verliert keine Dokumentzuordnungen mehr.

Stürzt der Import NACH dem Speichern eines Anhangs, aber VOR
``ProcessedMail.objects.create()`` ab, wurde der Anhang beim nächsten Lauf per
Hash als Duplikat übersprungen – das bestehende Dokument aber NICHT mit der Mail
verknüpft (die Mail wurde sogar IGNORED). Jetzt: bestehendes Dokument übernehmen,
verknüpfen und als „recovered" werten (Status IMPORTED).
"""
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import mail
from .models import Document, MailAccount, ProcessedMail

User = get_user_model()

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
MSG_ID = "<recover-1@example.com>"


def _message():
    part = MIMEBase("application", "pdf")
    part.set_payload(PDF_BYTES)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename="rechnung.pdf")
    root = MIMEMultipart()
    root["Subject"] = "Rechnung März"
    root["From"] = "Absender <bill@example.com>"
    root["Message-ID"] = MSG_ID
    root.attach(part)
    return root.as_bytes()


class MailRecoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mailrec", password="pw12345!")
        self.account = MailAccount.objects.create(
            name="Rechnungen", host="mail.example.com", username="u", owner=self.user
        )

    def test_wiederaufnahme_verknuepft_bestehendes_dokument(self):
        raw = _message()
        with mock.patch("documents.tasks.process_document_version.delay"):
            first = mail.ingest_message(self.account, raw)
        self.assertEqual(first, 1)
        doc = Document.objects.get(owner=self.user)

        # Crash-Simulation: ProcessedMail wurde NICHT geschrieben (Dokument bleibt).
        ProcessedMail.objects.filter(message_id=MSG_ID).delete()

        with mock.patch("documents.tasks.process_document_version.delay"):
            second = mail.ingest_message(self.account, raw)

        # Kein NEUER Import (Hash-Treffer) ...
        self.assertEqual(second, 0)
        # ... aber genau EIN Dokument (keine Dublette) ...
        self.assertEqual(Document.objects.filter(owner=self.user).count(), 1)
        # ... und die Mail ist wieder erfasst, IMPORTED und mit dem Dokument verknüpft.
        pm = ProcessedMail.objects.get(message_id=MSG_ID)
        self.assertEqual(pm.status, ProcessedMail.Status.IMPORTED)
        self.assertIn(doc, list(pm.documents.all()))
        self.assertIn("Wiederaufnahme", pm.note)

    def test_bereits_verarbeitete_mail_wird_nicht_erneut_verarbeitet(self):
        # Existiert der ProcessedMail-Eintrag noch, greift der Message-ID-Dedup
        # weiterhin (Idempotenz unverändert).
        raw = _message()
        with mock.patch("documents.tasks.process_document_version.delay"):
            mail.ingest_message(self.account, raw)
            again = mail.ingest_message(self.account, raw)
        self.assertIsNone(again)  # per Message-ID übersprungen
        self.assertEqual(ProcessedMail.objects.filter(message_id=MSG_ID).count(), 1)
