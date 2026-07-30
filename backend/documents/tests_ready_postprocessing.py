"""P1: READY wird gesetzt, BEVOR die Pflicht-Nachbearbeitung (Vertragsabgleich,
Entity Graph, Auto-Ablage, Review-Tasks) läuft. Stirbt der Worker dazwischen,
galt das Dokument dauerhaft als fertig. ``postprocessed_at`` wird erst NACH dem
gesamten Block gesetzt; ``reap_unpostprocessed_versions`` holt einen
unterbrochenen Lauf idempotent nach."""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from documents import pipeline, tasks
from documents.models import Document, DocumentVersion

REVIEW = "documents.services.review_tasks.sync_document_review_tasks"


def _ready_version(*, changed_min_ago=30, postprocessed_at=None):
    doc = Document.objects.create(title="D")
    v = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path="/tmp/x.pdf",
        sha256="a" * 64,
        ocr_text="Inhalt",
        is_immutable=True,  # WORM: save() wäre gesperrt -> Marker muss per update()
        processing_state=DocumentVersion.ProcessingState.READY,
        postprocessed_at=postprocessed_at,
    )
    doc.current_version = v
    doc.save(update_fields=["current_version"])
    DocumentVersion.objects.filter(pk=v.pk).update(
        processing_state_changed_at=timezone.now() - timedelta(minutes=changed_min_ago)
    )
    v.refresh_from_db()
    return v


def _patch_substeps():
    """Alle Nachbearbeitungs-Teilschritte neutralisieren (kein echter Dienst nötig)."""
    return [
        mock.patch.object(pipeline, "_sync_contract_center"),
        mock.patch.object(pipeline, "_sync_entity_graph"),
        mock.patch.object(pipeline, "ensure_findability_index", return_value=True),
        mock.patch.object(pipeline, "_sync_auto_file"),
        mock.patch(REVIEW, return_value=[]),
    ]


class RunPostprocessingTests(TestCase):
    def test_erfolg_setzt_postprocessed_at_worm_safe(self):
        v = _ready_version()
        self.assertIsNone(v.postprocessed_at)
        with mock.patch.object(
            pipeline, "_sync_contract_center", return_value=True
        ), mock.patch.object(
            pipeline, "_sync_entity_graph", return_value=True
        ), mock.patch.object(
            pipeline, "ensure_findability_index", return_value=True
        ), mock.patch.object(
            pipeline, "_sync_auto_file", return_value=True
        ), mock.patch(REVIEW, return_value=[]):
            pipeline.run_postprocessing(v, {"status": "done"})
        v.refresh_from_db()
        self.assertIsNotNone(v.postprocessed_at)

    def test_interner_stufenfehler_laesst_marker_null(self):
        # P1: Eine Stufe fängt ihren Fehler INTERN ab (gibt False zurück, wirft NICHT).
        # postprocessed_at darf dann NICHT gesetzt werden – sonst überspränge der
        # Reconciler das unvollständige Dokument dauerhaft.
        v = _ready_version()
        with mock.patch.object(
            pipeline, "_sync_contract_center", return_value=False  # intern fehlgeschlagen
        ), mock.patch.object(
            pipeline, "_sync_entity_graph", return_value=True
        ), mock.patch.object(
            pipeline, "ensure_findability_index", return_value=True
        ), mock.patch.object(
            pipeline, "_sync_auto_file", return_value=True
        ), mock.patch(REVIEW, return_value=[]):
            pipeline.run_postprocessing(v, {"status": "done"})
        v.refresh_from_db()
        self.assertIsNone(v.postprocessed_at)  # -> Reconciler versucht erneut

    def test_abbruch_mitten_drin_laesst_marker_null(self):
        # Simuliert einen Worker-Crash NACH READY, während der Nachbearbeitung:
        # ein Teilschritt wirft -> _mark_postprocessed wird nie erreicht.
        v = _ready_version()
        with mock.patch.object(pipeline, "_sync_contract_center"), mock.patch.object(
            pipeline, "_sync_entity_graph", side_effect=RuntimeError("crash")
        ):
            with self.assertRaises(RuntimeError):
                pipeline.run_postprocessing(v, {"status": "done"})
        v.refresh_from_db()
        self.assertIsNone(v.postprocessed_at)  # -> Reconciler holt nach


class ReapUnpostprocessedTests(TestCase):
    def test_reaper_holt_unterbrochene_nachbearbeitung_nach(self):
        v = _ready_version(changed_min_ago=30)  # postprocessed_at NULL, alt genug
        with mock.patch.object(pipeline, "run_postprocessing") as rp:
            res = tasks.reap_unpostprocessed_versions()
        rp.assert_called_once()
        self.assertEqual(rp.call_args.args[0].id, v.id)
        self.assertEqual(res["repaired"], 1)

    def test_reaper_ueberspringt_bereits_nachbearbeitete(self):
        _ready_version(changed_min_ago=30, postprocessed_at=timezone.now())
        with mock.patch.object(pipeline, "run_postprocessing") as rp:
            res = tasks.reap_unpostprocessed_versions()
        rp.assert_not_called()
        self.assertEqual(res["repaired"], 0)

    def test_reaper_ueberspringt_zu_frische(self):
        _ready_version(changed_min_ago=1)  # jünger als die Reconcile-Schwelle
        with mock.patch.object(pipeline, "run_postprocessing") as rp:
            tasks.reap_unpostprocessed_versions()
        rp.assert_not_called()
