"""Tests für die psychosr-Auto-Pipeline (Tag → MC-Karten → push).

Der Sync-Zustand liegt in :class:`FlashcardSyncEntry` (getrennt von der ggf.
unveränderlichen DocumentVersion). Kernpunkte der Absicherung:
* funktioniert auch für versiegelte (``is_immutable=True``) Versionen,
* atomarer Pro-Karte-Claim + stabiler Idempotency-Key gegen Dubletten,
* gebundener Celery-Retry für offene Karten,
* der aktuelle Versionszustand ist maßgeblich (nicht der dokumentweite Tag).
"""
import json
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry
from django.test import TestCase, override_settings

from . import tasks
from .models import Document, DocumentVersion, FlashcardSyncEntry, Tag

_SAMPLE = {
    "questions": [
        {
            "frage": "Was kennzeichnet Psychologie als empirische Wissenschaft?",
            "aussagen": [
                {"text": "Systematische Beobachtung", "richtig": True},
                {"text": "Reine Spekulation", "richtig": False},
                {"text": "Alltagswissen", "richtig": False},
                {"text": "Experimente", "richtig": True},
            ],
            "erklaerung": "empirisch = aus Beobachtung/Experiment",
            "kap": 1,
        },
        # ungültig (nur 2 Aussagen) -> muss verworfen werden
        {"frage": "Kaputt", "aussagen": [{"text": "a", "richtig": True}], "kap": 2},
    ]
}


class _FakeProvider:
    name = "fake"
    available = True

    def complete(self, prompt, *, system=None, max_tokens=1024):
        return "Antwort:\n" + json.dumps(_SAMPLE, ensure_ascii=False)


def _make_version(document, *, ocr_text="", immutable=False):
    version = DocumentVersion.objects.create(
        document=document,
        version_no=(document.versions.count() + 1),
        file_path=f"/data/originals/doc{document.id}-v1.pdf",
        sha256="",
        ocr_text=ocr_text,
        is_immutable=immutable,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])
    return version


class GenerateFlashcardsTests(TestCase):
    def test_only_valid_questions_survive(self):
        from ai.services import generate_flashcards

        with patch("ai.services.get_provider", return_value=_FakeProvider()):
            result = generate_flashcards("irgendein OCR-Text", max_questions=8)

        self.assertEqual(result["source"], "ai")
        self.assertEqual(len(result["questions"]), 1)  # die kaputte fliegt raus
        q = result["questions"][0]
        self.assertEqual(len(q["aussagen"]), 4)
        self.assertTrue(any(a["richtig"] for a in q["aussagen"]))
        self.assertEqual(q["kap"], 1)

    def test_unavailable_provider_returns_empty(self):
        from ai.services import generate_flashcards

        unavail = _FakeProvider()
        unavail.available = False
        with patch("ai.services.get_provider", return_value=unavail):
            result = generate_flashcards("text")
        self.assertEqual(result["source"], "unavailable")
        self.assertEqual(result["questions"], [])


@override_settings(
    PSYCHOSR_URL="http://psychosr.test",
    PSYCHOSR_TOKEN="secret-token",
    PSYCHOSR_DECK="mc",
)
class PushFlashcardClientTests(TestCase):
    def _card(self, kap=3):
        return {
            "frage": f"F{kap}",
            "aussagen": [{"text": "a", "richtig": True}],
            "kap": kap,
        }

    def test_sendet_ext_id_und_body(self):
        from . import psychosr_client

        calls = []

        class _FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, headers=None):
                calls.append((url, json, headers))
                return MagicMock(raise_for_status=lambda: None)

        with patch("documents.psychosr_client.httpx.Client", return_value=_FakeClient()):
            psychosr_client.push_flashcard(
                self._card(), source_title="Skript 4", idempotency_key="dms-v7-c0"
            )

        url, body, headers = calls[0]
        self.assertEqual(url, "http://psychosr.test/api/mc/add")
        self.assertEqual(headers["X-Token"], "secret-token")
        self.assertEqual(body["deck"], "mc")
        self.assertEqual(body["ext_id"], "dms-v7-c0")  # stabiler Idempotency-Key
        self.assertTrue(body["titel"].startswith("DMS: Skript 4"))

    def test_wirft_bei_fehler(self):
        from . import psychosr_client

        class _FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, headers=None):
                return MagicMock(raise_for_status=MagicMock(side_effect=RuntimeError("boom")))

        with patch("documents.psychosr_client.httpx.Client", return_value=_FakeClient()):
            with self.assertRaises(RuntimeError):
                psychosr_client.push_flashcard(
                    self._card(), source_title="T", idempotency_key="k"
                )

    @override_settings(PSYCHOSR_URL="", PSYCHOSR_TOKEN="")
    def test_wirft_wenn_unkonfiguriert(self):
        from . import psychosr_client

        with self.assertRaises(RuntimeError):
            psychosr_client.push_flashcard(self._card(), source_title="T", idempotency_key="k")


@override_settings(
    PSYCHOSR_URL="http://psychosr.test",
    PSYCHOSR_TOKEN="secret-token",
    PSYCHOSR_DECK="mc",
    PSYCHOSR_TRIGGER_TAG="Psychologie",
    PSYCHOSR_SYNCED_TAG="psychosr-synced",
)
class SyncDocumentFlashcardsTests(TestCase):
    """Kernlogik über den reinen Helper ``_sync_document_flashcards``."""

    def _doc(self, text="Psychologie ist die Wissenschaft vom Erleben und Verhalten.", **kw):
        doc = Document.objects.create(title="Kapitel 1")
        _make_version(doc, ocr_text=text, **kw)
        return doc

    def test_generiert_pusht_und_markiert_synced(self):
        doc = self._doc()
        with patch("ai.services.get_provider", return_value=_FakeProvider()), patch(
            "documents.psychosr_client.push_flashcard"
        ) as push:
            result = tasks._sync_document_flashcards(doc.id)

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["pushed"], 1)
        self.assertEqual(result["open"], 0)
        push.assert_called_once()
        # Idempotency-Key an psychosr übertragen
        self.assertEqual(push.call_args.kwargs["idempotency_key"], f"dms-v{doc.current_version_id}-c0")
        self.assertTrue(doc.tags.filter(name="psychosr-synced").exists())
        entries = FlashcardSyncEntry.objects.filter(version_id=doc.current_version_id)
        self.assertEqual([e.state for e in entries], ["pushed"])

    def test_funktioniert_fuer_versiegelte_version(self):
        # P1a: versiegelte (is_immutable) Version -> KEIN version.save(), also KEIN
        # ValidationError. Der Sync-Zustand lebt in FlashcardSyncEntry.
        doc = self._doc(immutable=True)
        self.assertTrue(doc.current_version.is_immutable)
        with patch("ai.services.get_provider", return_value=_FakeProvider()), patch(
            "documents.psychosr_client.push_flashcard"
        ):
            result = tasks._sync_document_flashcards(doc.id)

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["open"], 0)
        self.assertTrue(doc.tags.filter(name="psychosr-synced").exists())

    def test_teilfehler_laesst_offene_karte_pending_und_setzt_keinen_marker(self):
        doc = self._doc()
        # 2 gültige Karten generieren lassen:
        with patch(
            "ai.services.generate_flashcards",
            return_value={"source": "ai", "questions": [
                {"frage": "F1", "aussagen": [{"text": "a", "richtig": True}], "kap": 1},
                {"frage": "F2", "aussagen": [{"text": "b", "richtig": True}], "kap": 2},
            ]},
        ), patch(
            "documents.psychosr_client.push_flashcard",
            side_effect=[None, RuntimeError("boom")],  # Karte 1 ok, Karte 2 scheitert
        ):
            result = tasks._sync_document_flashcards(doc.id)

        self.assertEqual(result["pushed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["open"], 1)
        self.assertFalse(doc.tags.filter(name="psychosr-synced").exists())
        states = sorted(
            FlashcardSyncEntry.objects.filter(version_id=doc.current_version_id).values_list("state", flat=True)
        )
        self.assertEqual(states, ["pending", "pushed"])  # offene Karte wieder freigegeben

    def test_retry_sendet_nur_offene_ohne_neu_zu_generieren(self):
        doc = self._doc()
        gen = MagicMock(return_value={"source": "ai", "questions": [
            {"frage": "F1", "aussagen": [{"text": "a", "richtig": True}], "kap": 1},
            {"frage": "F2", "aussagen": [{"text": "b", "richtig": True}], "kap": 2},
        ]})
        sent = []

        def _push(question, *, source_title, idempotency_key):
            sent.append((question["frage"], idempotency_key))
            if len(sent) == 1:  # erster Lauf: Karte 1 ok
                return None
            raise RuntimeError("boom")  # Karte 2 scheitert im ersten Lauf

        with patch("ai.services.generate_flashcards", gen), patch(
            "documents.psychosr_client.push_flashcard", side_effect=_push
        ):
            first = tasks._sync_document_flashcards(doc.id)

        self.assertEqual(first["open"], 1)

        # Zweiter Lauf: nur die offene Karte F2 wird gesendet, KEINE Neugenerierung.
        sent.clear()
        with patch("ai.services.generate_flashcards", gen), patch(
            "documents.psychosr_client.push_flashcard", return_value=None
        ):
            second = tasks._sync_document_flashcards(doc.id)

        gen.assert_called_once()  # nur im ersten Lauf generiert
        self.assertEqual([f for f, _ in sent], ["F2"])
        self.assertEqual(second["open"], 0)
        self.assertTrue(doc.tags.filter(name="psychosr-synced").exists())

    def test_neue_version_wird_trotz_altem_marker_synchronisiert(self):
        # P2b: nach Sync von Version 1 bleibt der Marker-Tag am Dokument. Eine neue
        # Version muss dennoch generiert + gepusht werden (Versionszustand zählt).
        doc = self._doc()
        with patch("ai.services.get_provider", return_value=_FakeProvider()), patch(
            "documents.psychosr_client.push_flashcard"
        ):
            tasks._sync_document_flashcards(doc.id)
        self.assertTrue(doc.tags.filter(name="psychosr-synced").exists())
        v1 = doc.current_version_id

        # Neue Version hochladen (anderer Text) -> neue current_version:
        _make_version(doc, ocr_text="Ganz neuer Kapiteltext zur zweiten Version.")
        self.assertNotEqual(doc.current_version_id, v1)

        with patch("ai.services.get_provider", return_value=_FakeProvider()), patch(
            "documents.psychosr_client.push_flashcard"
        ) as push2:
            result = tasks._sync_document_flashcards(doc.id)

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["version_id"], doc.current_version_id)
        push2.assert_called_once()  # neue Version wurde gepusht (nicht "already_synced")
        self.assertTrue(
            FlashcardSyncEntry.objects.filter(version_id=doc.current_version_id).exists()
        )

    def test_disabled_wenn_unkonfiguriert(self):
        doc = self._doc()
        with override_settings(PSYCHOSR_URL="", PSYCHOSR_TOKEN=""):
            result = tasks._sync_document_flashcards(doc.id)
        self.assertEqual(result["status"], "disabled")

    def test_no_text(self):
        doc = Document.objects.create(title="Leer")
        _make_version(doc, ocr_text="")
        result = tasks._sync_document_flashcards(doc.id)
        self.assertEqual(result["status"], "no_text")

    def test_missing_document(self):
        result = tasks._sync_document_flashcards(999999)
        self.assertEqual(result["status"], "missing")


@override_settings(
    PSYCHOSR_URL="http://psychosr.test",
    PSYCHOSR_TOKEN="secret-token",
    PSYCHOSR_TRIGGER_TAG="Psychologie",
    PSYCHOSR_SYNCED_TAG="psychosr-synced",
)
class ClaimAtomicityTests(TestCase):
    """P1b: der atomare CAS-Claim verhindert doppeltes Senden."""

    def _version_with_entries(self, n=2):
        doc = Document.objects.create(title="D")
        ver = _make_version(doc, ocr_text="text")
        FlashcardSyncEntry.objects.bulk_create([
            FlashcardSyncEntry(
                version_id=ver.pk, ordinal=i, idempotency_key=f"dms-v{ver.pk}-c{i}",
                payload={"frage": f"F{i}", "aussagen": [], "kap": 1},
            ) for i in range(n)
        ])
        return ver

    def test_bereits_in_progress_wird_nicht_erneut_geclaimt(self):
        from datetime import timedelta

        ver = self._version_with_entries(2)
        # Eine Karte ist frisch in_progress (anderer Worker) -> nicht claimbar.
        FlashcardSyncEntry.objects.filter(version_id=ver.pk, ordinal=0).update(
            state="in_progress", claimed_at=tasks.timezone.now()
        )
        claimed = tasks._claim_flashcard_entries(ver.pk, stale_after=timedelta(minutes=15))
        self.assertEqual([e.ordinal for e in claimed], [1])  # nur die pending Karte

    def test_verwaiste_in_progress_wird_reklamiert(self):
        from datetime import timedelta

        ver = self._version_with_entries(1)
        FlashcardSyncEntry.objects.filter(version_id=ver.pk, ordinal=0).update(
            state="in_progress",
            claimed_at=tasks.timezone.now() - timedelta(minutes=30),  # verwaist
        )
        claimed = tasks._claim_flashcard_entries(ver.pk, stale_after=timedelta(minutes=15))
        self.assertEqual([e.ordinal for e in claimed], [0])

    def test_pushed_wird_nie_geclaimt(self):
        from datetime import timedelta

        ver = self._version_with_entries(1)
        FlashcardSyncEntry.objects.filter(version_id=ver.pk, ordinal=0).update(state="pushed")
        claimed = tasks._claim_flashcard_entries(ver.pk, stale_after=timedelta(minutes=15))
        self.assertEqual(claimed, [])


class RetryWrapperTests(TestCase):
    """P2a: offene Karten -> gebundener Celery-Retry."""

    def test_retry_bei_offenen_karten(self):
        with patch.object(
            tasks, "_sync_document_flashcards", return_value={"status": "done", "open": 2}
        ), patch.object(tasks.push_document_flashcards, "retry", side_effect=Retry()) as retry:
            tasks.push_document_flashcards.apply(args=[123])
        retry.assert_called_once()

    def test_kein_retry_wenn_alles_gepusht(self):
        with patch.object(
            tasks, "_sync_document_flashcards", return_value={"status": "done", "open": 0}
        ), patch.object(tasks.push_document_flashcards, "retry") as retry:
            res = tasks.push_document_flashcards.apply(args=[123]).get()
        retry.assert_not_called()
        self.assertEqual(res["status"], "done")


@override_settings(
    PSYCHOSR_URL="http://psychosr.test",
    PSYCHOSR_TOKEN="secret-token",
    PSYCHOSR_TRIGGER_TAG="Psychologie",
)
class SignalTriggerTests(TestCase):
    def test_trigger_tag_dispatches_task(self):
        doc = Document.objects.create(title="Doc")
        tag = Tag.objects.create(name="Psychologie")
        with patch("documents.tasks.push_document_flashcards.delay") as delay:
            doc.tags.add(tag)
        delay.assert_called_once_with(doc.id)

    def test_other_tag_does_not_dispatch(self):
        doc = Document.objects.create(title="Doc")
        tag = Tag.objects.create(name="Finanzen")
        with patch("documents.tasks.push_document_flashcards.delay") as delay:
            doc.tags.add(tag)
        delay.assert_not_called()

    @override_settings(PSYCHOSR_URL="", PSYCHOSR_TOKEN="")
    def test_disabled_when_unconfigured(self):
        doc = Document.objects.create(title="Doc")
        tag = Tag.objects.create(name="Psychologie")
        with patch("documents.tasks.push_document_flashcards.delay") as delay:
            doc.tags.add(tag)
        delay.assert_not_called()
