"""Tests für Wiedervorlagen/Erinnerungen (STOAA-372 PR1).

Deckt ab:
  (a) Anlegen einer Erinnerung (created_by aus Request, Audit ``reminder_created``)
  (b) ``due``-Filter (fällig vs. anstehend vs. zukünftig)
  (c) Beat ``check_due_reminders`` setzt ``notified_at`` genau einmal
  (d) Owner-Isolation: fremde Dokumente/Erinnerungen sind unsichtbar (404/leer)
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import AuditLogEntry, Document, DocumentReminder
from .tasks import check_due_reminders

User = get_user_model()


class DocumentReminderTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sebastian = User.objects.create_user(
            username="sebastian", password="pw", role="user", email="seb@example.com"
        )
        cls.manfred = User.objects.create_user(
            username="manfred", password="pw", role="user"
        )
        cls.admin = User.objects.create_user(
            username="admin", password="pw", role="admin"
        )
        cls.doc = Document.objects.create(title="Sebastians Vertrag", owner=cls.sebastian)
        cls.fremd_doc = Document.objects.create(title="Manfreds Akte", owner=cls.manfred)

    # --- (a) Anlegen -----------------------------------------------------
    def test_create_setzt_created_by_und_audit(self):
        self.client.force_authenticate(self.sebastian)
        resp = self.client.post(
            "/api/reminders/",
            {"document": self.doc.id, "remind_on": "2026-08-01", "note": "Frist prüfen"},
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        reminder = DocumentReminder.objects.get(pk=resp.data["id"])
        # created_by server-seitig aus dem Request – nicht fälschbar/read-only.
        self.assertEqual(reminder.created_by, self.sebastian)
        self.assertEqual(resp.data["created_by"], self.sebastian.id)
        self.assertIsNone(reminder.notified_at)
        self.assertFalse(reminder.done)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="reminder_created",
                object_type="DocumentReminder",
                object_id=str(reminder.id),
            ).exists()
        )

    # --- (b) due-Filter --------------------------------------------------
    def test_due_trennt_faellig_anstehend_zukuenftig(self):
        today = timezone.localdate()
        faellig = DocumentReminder.objects.create(
            document=self.doc, remind_on=today - timedelta(days=1), created_by=self.sebastian
        )
        heute = DocumentReminder.objects.create(
            document=self.doc, remind_on=today, created_by=self.sebastian
        )
        anstehend = DocumentReminder.objects.create(
            document=self.doc, remind_on=today + timedelta(days=3), created_by=self.sebastian
        )
        # Außerhalb des Default-Horizonts (7 Tage) → weder fällig noch anstehend.
        DocumentReminder.objects.create(
            document=self.doc, remind_on=today + timedelta(days=30), created_by=self.sebastian
        )
        # Erledigte tauchen nie auf.
        DocumentReminder.objects.create(
            document=self.doc, remind_on=today - timedelta(days=2),
            created_by=self.sebastian, done=True,
        )

        self.client.force_authenticate(self.sebastian)
        resp = self.client.get("/api/reminders/due/")
        self.assertEqual(resp.status_code, 200)
        faellig_ids = {r["id"] for r in resp.data["faellig"]}
        anstehend_ids = {r["id"] for r in resp.data["anstehend"]}
        self.assertEqual(faellig_ids, {faellig.id, heute.id})
        self.assertEqual(anstehend_ids, {anstehend.id})

    def test_due_days_param_erweitert_horizont(self):
        today = timezone.localdate()
        weit = DocumentReminder.objects.create(
            document=self.doc, remind_on=today + timedelta(days=20), created_by=self.sebastian
        )
        self.client.force_authenticate(self.sebastian)
        resp = self.client.get("/api/reminders/due/?days=30")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(weit.id, {r["id"] for r in resp.data["anstehend"]})

    # --- (c) Beat setzt notified_at genau einmal -------------------------
    def test_beat_setzt_notified_at_genau_einmal(self):
        today = timezone.localdate()
        faellig = DocumentReminder.objects.create(
            document=self.doc, remind_on=today - timedelta(days=1), created_by=self.sebastian
        )
        zukunft = DocumentReminder.objects.create(
            document=self.doc, remind_on=today + timedelta(days=5), created_by=self.sebastian
        )

        res1 = check_due_reminders()
        faellig.refresh_from_db()
        zukunft.refresh_from_db()
        self.assertIsNotNone(faellig.notified_at)
        self.assertIsNone(zukunft.notified_at)  # noch nicht fällig → unberührt
        self.assertEqual(res1["notified"], 1)

        first_ts = faellig.notified_at
        # Zweiter Lauf darf nichts mehr ändern (Dedupe via notified_at__isnull).
        res2 = check_due_reminders()
        faellig.refresh_from_db()
        self.assertEqual(faellig.notified_at, first_ts)
        self.assertEqual(res2["notified"], 0)

    def test_beat_ohne_smtp_kein_fehler_keine_mail(self):
        # Ohne EMAIL_HOST (Default leer) läuft der Beat fehlerfrei durch und
        # setzt notified_at – nur der Mailversand entfällt still.
        today = timezone.localdate()
        DocumentReminder.objects.create(
            document=self.doc, remind_on=today, created_by=self.sebastian
        )
        res = check_due_reminders()
        self.assertEqual(res["emailed"], 0)
        self.assertEqual(res["notified"], 1)

    # --- (d) Owner-Isolation --------------------------------------------
    def test_fremder_reminder_nicht_sichtbar(self):
        fremd = DocumentReminder.objects.create(
            document=self.fremd_doc, remind_on="2026-08-01", created_by=self.manfred
        )
        self.client.force_authenticate(self.sebastian)
        # Liste: nur eigene.
        resp = self.client.get("/api/reminders/")
        ids = {r["id"] for r in resp.data["results"]}
        self.assertNotIn(fremd.id, ids)
        # Detail einer fremden ID → 404 (kein Leak).
        self.assertEqual(self.client.get(f"/api/reminders/{fremd.id}/").status_code, 404)
        # done-Action auf fremde ID → 404.
        self.assertEqual(
            self.client.post(f"/api/reminders/{fremd.id}/done/").status_code, 404
        )

    def test_create_auf_fremdes_dokument_verboten(self):
        # Owner-Isolation in Schreibrichtung (STOAA-7): POST mit fremder
        # document-ID darf keine Erinnerung anlegen → 404 (kein Leak), und es
        # entsteht KEIN Datensatz.
        self.client.force_authenticate(self.sebastian)
        vorher = DocumentReminder.objects.count()
        resp = self.client.post(
            "/api/reminders/",
            {"document": self.fremd_doc.id, "remind_on": "2026-08-01"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(DocumentReminder.objects.count(), vorher)

    def test_admin_darf_create_auf_fremdes_dokument(self):
        # DMS-Admin ist bewusst nicht owner-gescoped.
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/reminders/",
            {"document": self.fremd_doc.id, "remind_on": "2026-08-01"},
        )
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_admin_sieht_alle(self):
        fremd = DocumentReminder.objects.create(
            document=self.fremd_doc, remind_on="2026-08-01", created_by=self.manfred
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.get(f"/api/reminders/{fremd.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_done_action_setzt_done_und_audit(self):
        reminder = DocumentReminder.objects.create(
            document=self.doc, remind_on="2026-08-01", created_by=self.sebastian
        )
        self.client.force_authenticate(self.sebastian)
        resp = self.client.post(f"/api/reminders/{reminder.id}/done/")
        self.assertEqual(resp.status_code, 200)
        reminder.refresh_from_db()
        self.assertTrue(reminder.done)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="reminder_done", object_id=str(reminder.id)
            ).exists()
        )
