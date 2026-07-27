"""P2: IMAP-Abruf meldet ehrlichen Status (kein „ok" trotz Fehlern).

SELECT/SEARCH/FETCH-Rückgabestatus werden ausgewertet; Teilfehler bleiben als
``last_error`` sichtbar und der Status ist nicht „ok".
"""
from unittest import mock

from django.test import TestCase

from . import mail
from .models import MailAccount


class _FakeConn:
    def __init__(self, *, select_typ="OK", search_typ="OK", uids=b"", fetch_typ="OK"):
        self.select_typ = select_typ
        self.search_typ = search_typ
        self.uids = uids
        self.fetch_typ = fetch_typ
        self.logged_out = False

    def select(self, folder):
        return (self.select_typ, [b"1"])

    def search(self, charset, criterion):
        return (self.search_typ, [self.uids])

    def fetch(self, uid, spec):
        return (self.fetch_typ, [(b"1 (RFC822)", b"raw-bytes")])

    def store(self, *args):
        return ("OK", [])

    def logout(self):
        self.logged_out = True


class ImapStatusTests(TestCase):
    def _account(self):
        return MailAccount.objects.create(
            name="Test", host="imap.example.org", username="u", folder="INBOX"
        )

    def test_select_fehler_ist_kein_ok(self):
        account = self._account()
        conn = _FakeConn(select_typ="NO")
        with mock.patch.object(mail, "connect", return_value=conn):
            stats = mail.fetch_account(account)
        self.assertEqual(stats["status"], "error")
        account.refresh_from_db()
        self.assertIn("SELECT", account.last_error)

    def test_fetch_fehler_ist_partial_error(self):
        account = self._account()
        conn = _FakeConn(uids=b"1", fetch_typ="NO")
        with mock.patch.object(mail, "connect", return_value=conn):
            stats = mail.fetch_account(account)
        self.assertEqual(stats["status"], "partial_error")
        self.assertEqual(stats["errors"], 1)
        account.refresh_from_db()
        self.assertTrue(account.last_error)  # sichtbar, NICHT geleert

    def test_alles_ok_setzt_ok_und_leert_fehler(self):
        account = self._account()
        account.last_error = "alt"
        account.save(update_fields=["last_error"])
        conn = _FakeConn(uids=b"")  # keine ungelesenen Mails
        with mock.patch.object(mail, "connect", return_value=conn):
            stats = mail.fetch_account(account)
        self.assertEqual(stats["status"], "ok")
        account.refresh_from_db()
        self.assertEqual(account.last_error, "")
