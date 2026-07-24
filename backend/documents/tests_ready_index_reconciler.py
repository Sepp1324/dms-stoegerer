"""P1: Nach READY werden die Pflicht-Findbarkeitsindizes (Suchvektor + Semantik)
bestätigt (indexed_at) – schlägt die Indizierung fehl, holt der Reconciler
(reap_unindexed_versions) sie nach, damit kein Dokument „bereit" aber unauffindbar
bleibt. indexed_at wird WORM-safe per update() gesetzt (Version ist immutable)."""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from documents import pipeline, tasks
from documents.models import Document, DocumentVersion

SEARCH = "documents.services.search_vector.update_search_vector_by_id"
SEM = "documents.services.semantic_index.sync_document_embeddings"


def _ready_version(*, immutable=False, indexed_at=None, changed_min_ago=30, state=None):
    doc = Document.objects.create(title="D")
    v = DocumentVersion.objects.create(
        document=doc, version_no=1, file_path="/tmp/x.pdf", sha256="a" * 64,
        ocr_text="Inhalt", is_immutable=immutable, indexed_at=indexed_at,
        processing_state=state or DocumentVersion.ProcessingState.READY,
    )
    doc.current_version = v
    doc.save(update_fields=["current_version"])
    DocumentVersion.objects.filter(pk=v.pk).update(
        processing_state_changed_at=timezone.now() - timedelta(minutes=changed_min_ago)
    )
    v.refresh_from_db()
    return v


class EnsureFindabilityIndexTests(TestCase):
    def test_beide_erfolg_setzt_indexed_at_worm_safe(self):
        v = _ready_version(immutable=True)  # WORM: save() waere gesperrt
        with mock.patch(SEARCH), mock.patch(SEM, return_value={"status": "indexed"}):
            ok = pipeline.ensure_findability_index(v)
        self.assertTrue(ok)
        v.refresh_from_db()
        self.assertIsNotNone(v.indexed_at)

    def test_index_fehler_laesst_indexed_at_null(self):
        v = _ready_version(immutable=True)
        with mock.patch(SEARCH, side_effect=Exception("boom")), mock.patch(
            SEM, return_value={"status": "indexed"}
        ):
            ok = pipeline.ensure_findability_index(v)
        self.assertFalse(ok)
        v.refresh_from_db()
        self.assertIsNone(v.indexed_at)  # -> Reconciler holt nach

    def test_semantik_error_status_laesst_indexed_at_null(self):
        # KERN-Regression: sync_document_embeddings meldet einen Embedding-Fehler
        # NICHT per Exception, sondern per status="error" (z. B. Backend down).
        # Frueher galt das als Erfolg -> indexed_at gesetzt -> Reconciler nie wieder.
        v = _ready_version(immutable=True)
        with mock.patch(SEARCH), mock.patch(SEM, return_value={"status": "error"}):
            ok = pipeline.ensure_findability_index(v)
        self.assertFalse(ok)
        v.refresh_from_db()
        self.assertIsNone(v.indexed_at)  # -> Reconciler holt nach

    def test_disabled_status_laesst_indexed_at_null(self):
        # EMBEDDING_ENABLED=false -> status="disabled": es existieren KEINE
        # Embeddings. Nicht als Erfolg werten, sonst werden die Dokumente nach
        # spaeterem Aktivieren der AI nie nachindexiert.
        v = _ready_version(immutable=True)
        with mock.patch(SEARCH), mock.patch(SEM, return_value={"status": "disabled"}):
            ok = pipeline.ensure_findability_index(v)
        self.assertFalse(ok)
        v.refresh_from_db()
        self.assertIsNone(v.indexed_at)

    def test_empty_status_ist_erfolg(self):
        # Kein Text zu indexieren (status="empty") ist ein legitimer Abschluss.
        v = _ready_version(immutable=True)
        with mock.patch(SEARCH), mock.patch(SEM, return_value={"status": "empty"}):
            ok = pipeline.ensure_findability_index(v)
        self.assertTrue(ok)
        v.refresh_from_db()
        self.assertIsNotNone(v.indexed_at)


@override_settings(
    INDEX_RECONCILE_AFTER_MINUTES=15, INDEX_RECONCILE_BATCH=50, EMBEDDING_ENABLED=True
)
class ReapUnindexedVersionsTests(TestCase):
    @override_settings(EMBEDDING_ENABLED=False)
    def test_pausiert_bei_deaktivierten_embeddings(self):
        # Kandidat vorhanden, aber AI aus -> keine Dauerlast, indexed_at bleibt NULL.
        v = _ready_version(indexed_at=None, changed_min_ago=30)
        with mock.patch(SEARCH) as search, mock.patch(SEM) as sem:
            res = tasks.reap_unindexed_versions()
        self.assertEqual(res.get("skipped"), "embeddings_disabled")
        self.assertEqual(res["candidates"], 0)
        search.assert_not_called()   # kein Volltext-Neuaufbau
        sem.assert_not_called()
        v.refresh_from_db()
        self.assertIsNone(v.indexed_at)

    def test_reindexiert_ready_ohne_indexed_at(self):
        v = _ready_version(indexed_at=None, changed_min_ago=30)
        with mock.patch(SEARCH), mock.patch(SEM, return_value={"status": "indexed"}):
            res = tasks.reap_unindexed_versions()
        self.assertEqual(res["reindexed"], 1)
        v.refresh_from_db()
        self.assertIsNotNone(v.indexed_at)

    def test_reindex_fehler_laesst_indexed_at_null(self):
        v = _ready_version(indexed_at=None, changed_min_ago=30)
        with mock.patch(SEARCH, side_effect=Exception("boom")), mock.patch(SEM):
            res = tasks.reap_unindexed_versions()
        self.assertEqual(res["reindexed"], 0)
        v.refresh_from_db()
        self.assertIsNone(v.indexed_at)  # bleibt Kandidat fuer den naechsten Lauf

    def test_ueberspringt_zu_junge_ready(self):
        _ready_version(indexed_at=None, changed_min_ago=1)  # < 15 min
        with mock.patch(SEARCH), mock.patch(SEM):
            res = tasks.reap_unindexed_versions()
        self.assertEqual(res["candidates"], 0)

    def test_ueberspringt_bereits_indexierte(self):
        _ready_version(indexed_at=timezone.now(), changed_min_ago=30)
        with mock.patch(SEARCH), mock.patch(SEM):
            res = tasks.reap_unindexed_versions()
        self.assertEqual(res["candidates"], 0)

    def test_ueberspringt_nicht_ready(self):
        _ready_version(
            indexed_at=None, changed_min_ago=30,
            state=DocumentVersion.ProcessingState.OCR_RUNNING,
        )
        with mock.patch(SEARCH), mock.patch(SEM):
            res = tasks.reap_unindexed_versions()
        self.assertEqual(res["candidates"], 0)
