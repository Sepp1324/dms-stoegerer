"""P1/P2: Der Audit-Trail ist append-only – Änderungen/Löschungen sind gesperrt
(Modell-Guards + Admin-Permissions + DB-Trigger gegen ORM-Umgehung)."""
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.test import TestCase

from .admin import AuditLogEntryAdmin
from .models import AuditLogEntry

User = get_user_model()


class AuditLogModelImmutabilityTests(TestCase):
    def test_bestehender_eintrag_nicht_aenderbar(self):
        entry = AuditLogEntry.objects.create(action="create", object_type="Document")
        entry.action = "manipuliert"
        with self.assertRaises(ValidationError):
            entry.save()
        entry.refresh_from_db()
        self.assertEqual(entry.action, "create")

    def test_eintrag_nicht_loeschbar(self):
        entry = AuditLogEntry.objects.create(action="create", object_type="Document")
        with self.assertRaises(ValidationError):
            entry.delete()
        self.assertTrue(AuditLogEntry.objects.filter(pk=entry.pk).exists())


class AuditLogDbTriggerTests(TestCase):
    """P2: Der DB-Trigger erzwingt append-only auch bei ORM-Umgehung
    (QuerySet.update/delete, rohes SQL). Die Fehler kommen aus der DB, daher jeweils
    in einem Savepoint (atomic), damit die Test-Transaktion nutzbar bleibt."""

    def test_queryset_delete_wird_vom_trigger_blockiert(self):
        entry = AuditLogEntry.objects.create(action="create", object_type="Document")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                AuditLogEntry.objects.filter(pk=entry.pk).delete()
        self.assertTrue(AuditLogEntry.objects.filter(pk=entry.pk).exists())

    def test_queryset_update_inhalt_wird_vom_trigger_blockiert(self):
        entry = AuditLogEntry.objects.create(action="create", object_type="Document")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                AuditLogEntry.objects.filter(pk=entry.pk).update(action="manipuliert")
        entry.refresh_from_db()
        self.assertEqual(entry.action, "create")

    def test_actor_anonymisierung_bei_userloeschung_erlaubt(self):
        # Der Trigger MUSS die SET_NULL-Anonymisierung (actor -> NULL beim Löschen des
        # Users) zulassen, sonst bräche die Benutzerlöschung.
        user = User.objects.create_user("audit_actor", password="pw", role="user")
        entry = AuditLogEntry.objects.create(
            actor=user, action="create", object_type="Document"
        )
        user.delete()  # FK on_delete=SET_NULL -> UPDATE actor_id=NULL (erlaubt)
        entry.refresh_from_db()
        self.assertIsNone(entry.actor_id)
        self.assertEqual(entry.action, "create")  # inhaltlich unverändert


class AuditLogAdminPermissionTests(TestCase):
    def setUp(self):
        self.admin = AuditLogEntryAdmin(AuditLogEntry, AdminSite())

    def test_admin_sperrt_add_change_delete(self):
        self.assertFalse(self.admin.has_add_permission(request=None))
        self.assertFalse(self.admin.has_change_permission(request=None))
        self.assertFalse(self.admin.has_delete_permission(request=None))
