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
from rest_framework.test import APITestCase

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

    def test_empfaenger_ist_aktueller_dokument_owner(self):
        # P1: Nach Besitzerwechsel geht die Mail an den AKTUELLEN Owner, nicht mehr
        # an created_by (den früheren Besitzer).
        new_owner = User.objects.create_user(
            "neu", password="pw", email="neu@example.com"
        )
        self.doc.owner = new_owner
        self.doc.save(update_fields=["owner"])
        captured = {}

        def fake_send(**kwargs):
            captured["to"] = kwargs["recipient_list"]
            return 1

        with mock.patch("django.core.mail.send_mail", side_effect=fake_send):
            check_due_reminders()
        self.assertEqual(captured["to"], ["neu@example.com"])

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

    def test_email_sent_at_erst_nach_bestaetigtem_versand(self):
        # P2: email_sent_at bedeutet BESTÄTIGT versendet. Während send_mail läuft, ist
        # es noch NULL (nur die Lease email_claimed_at ist gesetzt); erst NACH Erfolg.
        seen = {}

        def fake_send(**kwargs):
            reminder = DocumentReminder.objects.get(pk=self.reminder.pk)
            seen["sent_at_during_send"] = reminder.email_sent_at
            seen["claimed_during_send"] = reminder.email_claimed_at
            return 1

        with mock.patch("django.core.mail.send_mail", side_effect=fake_send):
            check_due_reminders()
        self.assertIsNone(seen["sent_at_during_send"])       # noch NICHT bestätigt
        self.assertIsNotNone(seen["claimed_during_send"])    # aber geclaimt (Lease)
        self.reminder.refresh_from_db()
        self.assertIsNotNone(self.reminder.email_sent_at)    # nach Erfolg bestätigt

    def test_abgelaufene_lease_wird_neu_versucht(self):
        # P2 (kein dauerhafter Verlust): Simuliert Worker-Crash NACH dem Claim, VOR
        # dem bestätigten Versand -> abgelaufene Lease, email_sent_at NULL. Der nächste
        # Lauf muss erneut senden.
        old = timezone.now() - timedelta(hours=2)
        DocumentReminder.objects.filter(pk=self.reminder.pk).update(email_claimed_at=old)
        with mock.patch("django.core.mail.send_mail", return_value=1) as sm:
            check_due_reminders()
        sm.assert_called_once()  # trotz (abgelaufener) Vor-Lease erneut gesendet
        self.reminder.refresh_from_db()
        self.assertIsNotNone(self.reminder.email_sent_at)

    def test_frische_lease_wird_nicht_doppelt_versendet(self):
        # Eine frische Lease (anderer Worker in-flight) darf NICHT erneut senden.
        DocumentReminder.objects.filter(pk=self.reminder.pk).update(
            email_claimed_at=timezone.now()
        )
        with mock.patch("django.core.mail.send_mail", return_value=1) as sm:
            check_due_reminders()
        sm.assert_not_called()

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

    def test_reschedule_zwischen_claim_und_versand_markiert_nicht_versendet(self):
        # P2: Wird die Erinnerung NACH dem CAS-Claim, WÄHREND des SMTP-Versands neu
        # in die Zukunft terminiert (die Reschedule-Logik setzt die Marker zurück),
        # darf der Worker email_sent_at NICHT setzen – sonst fiele der neue Termin aus.
        # send_mail ist genau der Seam zwischen Claim und Versandbestätigung.
        future = timezone.localdate() + timedelta(days=7)

        def fake_send(**kwargs):
            DocumentReminder.objects.filter(pk=self.reminder.pk).update(
                remind_on=future,
                email_claimed_at=None,
                email_sent_at=None,
                notified_at=None,
            )
            return 1

        with mock.patch("django.core.mail.send_mail", side_effect=fake_send):
            res = check_due_reminders()

        self.reminder.refresh_from_db()
        self.assertEqual(self.reminder.remind_on, future)
        self.assertIsNone(self.reminder.email_sent_at)  # zukünftiger Termin NICHT versendet-markiert
        self.assertEqual(res["emailed"], 0)

    def test_versandbestaetigung_ist_an_den_claim_token_gebunden(self):
        # P2: Auch wenn remind_on<=today und done=False erhalten bleiben, darf die
        # Bestätigung NICHT feuern, wenn der Claim (email_claimed_at) zwischenzeitlich
        # zurückgesetzt wurde – der Versand ist strikt an unseren Claim gebunden.
        def fake_send(**kwargs):
            DocumentReminder.objects.filter(pk=self.reminder.pk).update(
                email_claimed_at=None
            )
            return 1

        with mock.patch("django.core.mail.send_mail", side_effect=fake_send):
            res = check_due_reminders()

        self.reminder.refresh_from_db()
        self.assertIsNone(self.reminder.email_sent_at)
        self.assertEqual(res["emailed"], 0)

    def test_owner_ohne_email_faellt_nicht_auf_created_by_zurueck(self):
        # P1: Hat der AKTUELLE Owner keine E-Mail, darf NICHT auf created_by
        # (früherer Besitzer) zurückgefallen werden – sonst erhielte der alte
        # Eigentümer nach einer Übertragung Dokumentnummer + Notiz.
        new_owner = User.objects.create_user("stumm", password="pw", email="")
        self.doc.owner = new_owner
        self.doc.save(update_fields=["owner"])

        with mock.patch("django.core.mail.send_mail", return_value=1) as sm:
            res = check_due_reminders()

        sm.assert_not_called()  # kein Empfänger -> nichts gesendet
        self.assertEqual(res["emailed"], 0)
        self.reminder.refresh_from_db()
        self.assertIsNone(self.reminder.email_sent_at)   # nicht als versendet markiert
        self.assertIsNone(self.reminder.email_claimed_at)  # Lease wieder freigegeben

    def test_race_reschedule_wird_nicht_faelschlich_geclaimt(self):
        # P2: Wird die Erinnerung ZWISCHEN Kandidatenauswahl und CAS-Claim neu in die
        # Zukunft terminiert, darf der (guarded) Claim sie NICHT mehr treffen – sonst
        # würde der neue, zukünftige Termin sofort als benachrichtigt markiert und
        # fiele später aus. Wir injizieren die Neu-Terminierung deterministisch in
        # genau dieses Fenster: beim Materialisieren der ersten Kandidatenliste (die
        # den pk noch enthält) verschieben wir die Erinnerung in die Zukunft; der
        # nachfolgende atomare UPDATE muss dank ``done=False``/``remind_on<=today``
        # dann rowcount 0 liefern.
        future = timezone.localdate() + timedelta(days=10)
        qs_class = type(DocumentReminder.objects.all())
        original_values_list = qs_class.values_list
        state = {"rescheduled": False}
        reminder_pk = self.reminder.pk

        def patched(qs_self, *args, **kwargs):
            result = original_values_list(qs_self, *args, **kwargs)
            if qs_self.model is DocumentReminder and not state["rescheduled"]:
                # Snapshot VOR der Neu-Terminierung erzwingen (Kandidat enthält pk),
                # dann das Race auslösen.
                materialized = list(result)
                DocumentReminder.objects.filter(pk=reminder_pk).update(
                    remind_on=future
                )
                state["rescheduled"] = True
                return materialized
            return result

        with mock.patch.object(qs_class, "values_list", patched), mock.patch(
            "django.core.mail.send_mail", return_value=1
        ) as sm:
            check_due_reminders()

        self.assertTrue(state["rescheduled"])  # Race wurde tatsächlich injiziert
        sm.assert_not_called()  # zukünftiger Termin wird nicht versendet
        self.reminder.refresh_from_db()
        self.assertIsNone(self.reminder.notified_at)      # notified-CAS guarded
        self.assertIsNone(self.reminder.email_sent_at)
        self.assertIsNone(self.reminder.email_claimed_at)


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


class ReminderRescheduleTests(APITestCase):
    """P2: Neu-Terminierung/Wiedereröffnung setzt die Versandmarker zurück."""

    def setUp(self):
        self.user = User.objects.create_user("resched", password="pw", role="user")
        self.doc = Document.objects.create(title="Vertrag", owner=self.user)
        self.reminder = DocumentReminder.objects.create(
            document=self.doc, created_by=self.user, remind_on=timezone.localdate()
        )
        # Bereits benachrichtigt + versendet.
        DocumentReminder.objects.filter(pk=self.reminder.pk).update(
            notified_at=timezone.now(),
            email_sent_at=timezone.now(),
            email_claimed_at=timezone.now(),
        )
        self.client.force_authenticate(self.user)

    def test_neues_datum_resetet_marker(self):
        new_date = (timezone.localdate() + timedelta(days=5)).isoformat()
        resp = self.client.patch(
            f"/api/reminders/{self.reminder.id}/", {"remind_on": new_date}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.reminder.refresh_from_db()
        self.assertIsNone(self.reminder.notified_at)
        self.assertIsNone(self.reminder.email_sent_at)
        self.assertIsNone(self.reminder.email_claimed_at)

    def test_gleiches_datum_laesst_marker_bestehen(self):
        same = self.reminder.remind_on.isoformat()
        resp = self.client.patch(
            f"/api/reminders/{self.reminder.id}/", {"note": "nur Notiz", "remind_on": same},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.reminder.refresh_from_db()
        self.assertIsNotNone(self.reminder.email_sent_at)  # unverändert
