"""P1: IMAP-Abruf begrenzt Nachrichten- und Anhanggröße gegen Worker-OOM."""
from email.message import EmailMessage
from unittest import mock

from django.test import TestCase, override_settings

from . import mail
from .models import MailAccount, ProcessedMail
from .storage import UnsupportedFileType


class _SizeConn:
    """Fake-IMAP: SELECT/SEARCH ok, FETCH beantwortet RFC822.SIZE und RFC822."""

    def __init__(self, *, size: int):
        self.size = size
        self.rfc822_fetched = False

    def select(self, folder):
        return ("OK", [b"1"])

    def search(self, charset, criterion):
        return ("OK", [b"1"])

    def fetch(self, uid, spec):
        if "RFC822.SIZE" in spec:
            return ("OK", [b"1 (RFC822.SIZE %d)" % self.size])
        if spec == "(RFC822)":
            self.rfc822_fetched = True
            return ("OK", [(b"1 (RFC822 {..})", b"raw-body")])
        return ("OK", [b""])

    def store(self, *a):
        return ("OK", [])

    def logout(self):
        pass


def _mail_with_attachments(n: int) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "Viele Anhänge"
    msg["From"] = "abs@example.org"
    msg["Message-ID"] = "<many@test>"
    msg.set_content("Body")
    for i in range(n):
        msg.add_attachment(
            b"%PDF-1.4 xxxx", maintype="application", subtype="pdf",
            filename=f"anhang{i}.pdf",
        )
    return msg.as_bytes()


class MailLimitTests(TestCase):
    def _account(self):
        return MailAccount.objects.create(
            name="Test", host="imap.example.org", username="u", folder="INBOX"
        )

    @override_settings(MAIL_MAX_MESSAGE_MB=1)
    def test_zu_grosse_mail_wird_nicht_geladen(self):
        account = self._account()
        conn = _SizeConn(size=5 * 1024 * 1024)  # 5 MB > 1 MB Limit
        with mock.patch.object(mail, "connect", return_value=conn), mock.patch.object(
            mail, "ingest_message"
        ) as ingest:
            stats = mail.fetch_account(account)
        ingest.assert_not_called()  # Body nie verarbeitet
        self.assertFalse(conn.rfc822_fetched)  # Vollabruf fand NICHT statt
        self.assertEqual(stats["skipped"], 1)

    @override_settings(MAIL_MAX_ATTACHMENTS=1)
    def test_anhang_anzahl_gedeckelt(self):
        account = self._account()
        raw = _mail_with_attachments(3)
        # save_bytes wird nach dem Cap gar nicht mehr aufgerufen; hier abgewiesen,
        # damit keine echten Dokumente entstehen – die Zählung reicht als Nachweis.
        with mock.patch(
            "documents.storage.save_bytes", side_effect=UnsupportedFileType("nope")
        ):
            mail.ingest_message(account, raw)
        pm = ProcessedMail.objects.get()
        self.assertEqual(pm.attachment_count, 1)  # nur 1 statt 3 verarbeitet
