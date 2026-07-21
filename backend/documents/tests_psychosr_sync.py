"""Tests für die psychosr-Auto-Pipeline (Tag → MC-Karten → push)."""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from .models import Document, DocumentVersion, Tag

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


def _make_version(document, *, ocr_text=""):
    version = DocumentVersion.objects.create(
        document=document,
        version_no=1,
        file_path=f"/data/originals/doc{document.id}-v1.pdf",
        sha256="",
        ocr_text=ocr_text,
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
    PSYCHOSR_TRIGGER_TAG="Psychologie",
    PSYCHOSR_SYNCED_TAG="psychosr-synced",
)
class PushFlashcardsTests(TestCase):
    def test_posts_each_card_with_token(self):
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

        questions = [
            {
                "frage": "F1",
                "aussagen": [
                    {"text": "a", "richtig": True},
                    {"text": "b", "richtig": False},
                    {"text": "c", "richtig": False},
                    {"text": "d", "richtig": False},
                ],
                "kap": 3,
            }
        ]
        with patch("documents.psychosr_client.httpx.Client", return_value=_FakeClient()):
            res = psychosr_client.push_flashcards(questions, source_title="Skript 4")

        self.assertEqual(res["pushed"], 1)
        self.assertEqual(res["failed"], 0)
        url, body, headers = calls[0]
        self.assertEqual(url, "http://psychosr.test/api/mc/add")
        self.assertEqual(headers["X-Token"], "secret-token")
        self.assertEqual(body["deck"], "mc")
        self.assertEqual(body["kap"], 3)
        self.assertTrue(body["titel"].startswith("DMS: Skript 4"))
        self.assertEqual(len(body["aussagen"]), 4)


@override_settings(
    PSYCHOSR_URL="http://psychosr.test",
    PSYCHOSR_TOKEN="secret-token",
    PSYCHOSR_DECK="mc",
    PSYCHOSR_TRIGGER_TAG="Psychologie",
    PSYCHOSR_SYNCED_TAG="psychosr-synced",
)
class PushDocumentFlashcardsTaskTests(TestCase):
    def _doc(self, text="Psychologie ist die Wissenschaft vom Erleben und Verhalten."):
        doc = Document.objects.create(title="Kapitel 1")
        _make_version(doc, ocr_text=text)
        return doc

    def test_generates_pushes_and_marks_synced(self):
        from .tasks import push_document_flashcards

        doc = self._doc()
        with patch("ai.services.get_provider", return_value=_FakeProvider()), patch(
            "documents.psychosr_client.push_flashcards",
            return_value={"pushed": 1, "failed": 0, "errors": [], "skipped": False},
        ) as push:
            result = push_document_flashcards(doc.id)

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["pushed"], 1)
        push.assert_called_once()
        self.assertTrue(doc.tags.filter(name="psychosr-synced").exists())

    def test_teilfehler_markiert_nicht_synced(self):
        # Mind. eine Karte scheiterte (failed>0): NICHT als synced markieren, sonst
        # würde der Marker-Tag einen erneuten Versuch der Restkarten verhindern.
        from .tasks import push_document_flashcards

        doc = self._doc()
        with patch("ai.services.get_provider", return_value=_FakeProvider()), patch(
            "documents.psychosr_client.push_flashcards",
            return_value={"pushed": 1, "failed": 1, "errors": ["boom"], "skipped": False},
        ):
            result = push_document_flashcards(doc.id)

        self.assertEqual(result["failed"], 1)
        self.assertFalse(doc.tags.filter(name="psychosr-synced").exists())

    def test_idempotent_when_already_synced(self):
        from .tasks import push_document_flashcards

        doc = self._doc()
        marker = Tag.objects.create(name="psychosr-synced")
        doc.tags.add(marker)
        with patch("documents.psychosr_client.push_flashcards") as push:
            result = push_document_flashcards(doc.id)
        self.assertEqual(result["status"], "already_synced")
        push.assert_not_called()

    def test_no_text_skips(self):
        from .tasks import push_document_flashcards

        doc = Document.objects.create(title="Leer")
        _make_version(doc, ocr_text="")
        result = push_document_flashcards(doc.id)
        self.assertEqual(result["status"], "no_text")


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
