"""P2: Reminder-Mails zuverlässig + nebenläufigkeitssicher.

- In-App-``notified_at`` wird atomar (CAS) genau einmal gesetzt.
- Der E-Mail-Versand hat einen EIGENEN Status (``email_sent_at``), der erst nach
  BESTÄTIGTEM Versand gesetzt wird -> ein fehlgeschlagener Versand wird erneut
  versucht (nicht am In-App-Dedupe hängengeblieben).
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Document, DocumentReminder
from .tasks import check_due_reminders

User = get_user_model()


@override_settings(EMAIL_HOST="smtp.test", DEFAULT_FROM_EMAIL="dms@test")
class ReminderEmailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("erin", password="pw", email="erin@example.com")
        self.doc = Document.objects.create(title="Vertrag", owner=self.user)
        self.reminder = DocumentReminder.objects.create(
            document=self.doc, created_by=self.user, remind_on=timezone.localdate()
        )

    def test_fehlgeschlagener_versand_wird_erneut_versucht(self):
        with mock.patch("django.core.mail.send_mail", side_effect=Exception("smtp down")):
            res1 = check_due_reminders()
        self.reminder.refresh_from_db()
        self.assertIsNotNone(self.reminder.notified_at)   # In-App gesetzt
        self.assertIsNone(self.reminder.email_sent_at)    # E-Mail NICHT als versendet markiert
        self.assertEqual(res1["emailed"], 0)

        with mock.patch("django.core.mail.send_mail", return_value=1) as sm:
            res2 = check_due_reminders()
        sm.assert_called_once()  # erneuter Versuch
        self.reminder.refresh_from_db()
        self.assertIsNotNone(self.reminder.email_sent_at)
        self.assertEqual(res2["emailed"], 1)
        self.assertEqual(res2["notified"], 0)  # In-App nicht erneut

    def test_null_zustellung_markiert_nicht_versendet(self):
        # send_mail liefert 0 (nichts zugestellt) -> NICHT als versendet markieren.
        with mock.patch("django.core.mail.send_mail", return_value=0):
            check_due_reminders()
        self.reminder.refresh_from_db()
        self.assertIsNone(self.reminder.email_sent_at)

    def test_notified_at_wird_nur_einmal_gesetzt(self):
        with mock.patch("django.core.mail.send_mail", return_value=1):
            first = check_due_reminders()
            second = check_due_reminders()
        self.assertEqual(first["notified"], 1)
        self.assertEqual(second["notified"], 0)  # CAS: kein Doppel-Notify

    def test_erfolgreicher_versand_wird_nicht_wiederholt(self):
        with mock.patch("django.core.mail.send_mail", return_value=1) as sm:
            check_due_reminders()
            check_due_reminders()
        sm.assert_called_once()  # zweiter Lauf sendet nicht erneut (email_sent_at gesetzt)


@override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL="dms@test")
class ReminderWithoutSmtpTests(TestCase):
    def test_ohne_smtp_nur_in_app(self):
        user = User.objects.create_user("erin2", password="pw", email="e2@example.com")
        doc = Document.objects.create(title="X", owner=user)
        DocumentReminder.objects.create(document=doc, created_by=user, remind_on=timezone.localdate())
        with mock.patch("django.core.mail.send_mail") as sm:
            res = check_due_reminders()
        sm.assert_not_called()
        self.assertEqual(res["notified"], 1)
        self.assertEqual(res["emailed"], 0)
