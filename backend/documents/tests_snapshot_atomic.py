"""write_snapshot_on_seal ist atomar write-once (CAS).

Zwei parallele Seal-/Watchdog-Läufe dürfen den Snapshot + seal_hash nicht
nacheinander überschreiben (WORM-Nachweis). Nur der Gewinner schreibt; der
Verlierer lädt den bestehenden Stand neu und meldet False.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Document, DocumentVersion
from .services import version_snapshot

User = get_user_model()


class WriteSnapshotOnSealTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="snap", password="pw")
        self.doc = Document.objects.create(title="d", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=self.doc,
            version_no=1,
            file_path="/tmp/snap.pdf",
            sha256="a" * 64,
            prev_hash="",
            mime_type="application/pdf",
        )

    def test_snapshot_und_audit_atomar_beide_da(self):
        # CAS-Update und Audit laufen in EINER Transaktion -> nach erfolgreichem
        # Schreiben existieren Snapshot UND Audit (nie das eine ohne das andere).
        from .models import AuditLogEntry

        self.assertTrue(version_snapshot.write_snapshot_on_seal(self.version))
        self.assertTrue(
            AuditLogEntry.objects.filter(
                object_id=str(self.version.id), action="metadata_snapshot"
            ).exists()
        )

    def test_erster_schreibt_zweiter_nicht(self):
        self.assertTrue(version_snapshot.write_snapshot_on_seal(self.version))
        self.assertIsNotNone(self.version.metadata_snapshot)
        first_hash = self.version.seal_hash
        # Zweiter Aufruf auf einer frisch geladenen Instanz -> kein Doppelschreiben.
        again = DocumentVersion.objects.get(pk=self.version.pk)
        self.assertFalse(version_snapshot.write_snapshot_on_seal(again))
        again.refresh_from_db()
        self.assertEqual(again.seal_hash, first_hash)

    def test_verlierer_laedt_bestehenden_snapshot_neu(self):
        # Stale-Referenz denkt „kein Snapshot"; parallel schreibt ein anderer Lauf.
        stale = DocumentVersion.objects.get(pk=self.version.pk)
        stale.metadata_snapshot = None  # veralteter In-Memory-Zustand
        DocumentVersion.objects.filter(pk=stale.pk).update(
            metadata_snapshot={"winner": True}, seal_hash="deadbeef"
        )

        result = version_snapshot.write_snapshot_on_seal(stale)

        self.assertFalse(result)
        # Verlierer hat den VERBINDLICHEN Stand übernommen, nicht seinen eigenen.
        self.assertEqual(stale.metadata_snapshot, {"winner": True})
        self.assertEqual(stale.seal_hash, "deadbeef")
