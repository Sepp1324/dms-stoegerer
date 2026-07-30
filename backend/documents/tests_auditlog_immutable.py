"""P1: Der Audit-Trail ist append-only – Änderungen/Löschungen sind gesperrt
(Modell-Guards + Admin-Permissions)."""
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import TestCase

from .admin import AuditLogEntryAdmin
from .models import AuditLogEntry


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


class AuditLogAdminPermissionTests(TestCase):
    def setUp(self):
        self.admin = AuditLogEntryAdmin(AuditLogEntry, AdminSite())

    def test_admin_sperrt_add_change_delete(self):
        self.assertFalse(self.admin.has_add_permission(request=None))
        self.assertFalse(self.admin.has_change_permission(request=None))
        self.assertFalse(self.admin.has_delete_permission(request=None))
