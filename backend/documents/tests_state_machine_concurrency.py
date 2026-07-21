"""Nebenläufigkeitssicherheit der Verarbeitungs-State-Machine (P1).

transition_to/begin_retry nutzen jetzt Compare-and-Swap (UPDATE ... WHERE
processing_state = erwartet). Zwei nebenläufige Tasks/Retry-Klicks führen den
Übergang nicht mehr doppelt aus – der Verlierer bekommt 0 Zeilen und wirft
ConcurrentProcessingTransition. mark_processing_failed überschreibt eine
nebenläufig gesiegelte Version nicht.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    AuditLogEntry,
    ConcurrentProcessingTransition,
    Document,
    DocumentVersion,
)

User = get_user_model()
PS = DocumentVersion.ProcessingState


class TransitionCasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sm", password="pw")
        self.doc = Document.objects.create(title="d", owner=self.user)

    def _version(self, state):
        return DocumentVersion.objects.create(
            document=self.doc,
            version_no=1,
            file_path="/tmp/sm.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            processing_state=state,
            created_by=self.user,
        )

    def test_transition_normal_erfolgreich(self):
        v = self._version(PS.UPLOADED)
        v.transition_to(PS.HASHED)
        v.refresh_from_db()
        self.assertEqual(v.processing_state, PS.HASHED)

    def test_transition_cas_miss_wirft_und_aendert_nichts(self):
        v = self._version(PS.UPLOADED)
        # Ein anderer Task hat den Zustand in der DB bereits weitergeschaltet;
        # unser Objekt hält noch den veralteten In-Memory-Zustand UPLOADED.
        DocumentVersion.objects.filter(pk=v.pk).update(processing_state=PS.HASHED)
        audits_before = AuditLogEntry.objects.filter(
            object_id=str(v.id), action="processing_state"
        ).count()

        with self.assertRaises(ConcurrentProcessingTransition):
            v.transition_to(PS.HASHED)

        # Kein doppelter Übergang, kein zusätzlicher Audit-Eintrag.
        self.assertEqual(
            AuditLogEntry.objects.filter(
                object_id=str(v.id), action="processing_state"
            ).count(),
            audits_before,
        )

    def test_begin_retry_cas_miss_wirft(self):
        v = self._version(PS.FAILED)
        # Erster Retry hat FAILED bereits übernommen (DB = RETRY_PENDING),
        # unser Objekt denkt noch FAILED.
        DocumentVersion.objects.filter(pk=v.pk).update(
            processing_state=PS.RETRY_PENDING
        )
        with self.assertRaises(ConcurrentProcessingTransition):
            v.begin_retry(actor=self.user)

    def test_begin_retry_normal_zaehlt_versuch_hoch(self):
        v = self._version(PS.FAILED)
        v.begin_retry(actor=self.user)
        v.refresh_from_db()
        self.assertEqual(v.processing_state, PS.RETRY_PENDING)
        self.assertEqual(v.processing_attempts, 1)

    def test_mark_failed_ueberschreibt_sealed_nicht(self):
        v = self._version(PS.OCR_RUNNING)
        # Nebenläufig gesiegelt (WORM). mark_processing_failed darf das nicht
        # überschreiben.
        DocumentVersion.objects.filter(pk=v.pk).update(processing_state=PS.SEALED)
        v.mark_processing_failed(step="ocr", error="boom", actor=self.user)
        v.refresh_from_db()
        self.assertEqual(v.processing_state, PS.SEALED)
