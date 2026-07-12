"""Smoke-Test für das Kalibrier-Command (calibrate_embeddings).

Deterministischer Fake-Embedder (kein 1-GB-Modell im CI). Prüft, dass das Command
mit echten pgvector-Abfragen durchläuft und die Verteilung ausgibt.
"""
import hashlib
import math
from io import StringIO
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from .models import Document, DocumentVersion
from .services import semantic_index

User = get_user_model()

_DIM = settings.EMBEDDING_DIM


def _fake_vector(text: str) -> list[float]:
    vector = [0.0] * _DIM
    for token in semantic_index.tokenize(text):
        bucket = int(hashlib.blake2b(token.encode("utf-8"), digest_size=8).hexdigest(), 16)
        vector[bucket % _DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _fake_passages(texts):
    return [_fake_vector(t) for t in texts]


_patchers = []


def setUpModule():
    _patchers.append(mock.patch("ai.embeddings.enabled", return_value=True))
    _patchers.append(mock.patch("ai.embeddings.embed_passages", side_effect=_fake_passages))
    _patchers.append(mock.patch("ai.embeddings.embed_query", side_effect=_fake_vector))
    for patcher in _patchers:
        patcher.start()


def tearDownModule():
    for patcher in _patchers:
        patcher.stop()
    _patchers.clear()


class CalibrateCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="cal-u", password="pw", role="user")
        for title, text in [
            ("Rechnung A", "Stromrechnung Januar Betrag IBAN Zahlungsreferenz 4711."),
            ("Rechnung A Scan2", "Stromrechnung Januar Betrag IBAN Zahlungsreferenz 4711."),
            ("Reisepass", "Reisepass Personalausweis Bürgeramt Ausweisnummer."),
        ]:
            doc = Document.objects.create(title=title, owner=cls.user)
            version = DocumentVersion.objects.create(
                document=doc,
                version_no=1,
                file_path=f"/tmp/{title}.pdf",
                sha256=hashlib.sha256(title.encode("utf-8")).hexdigest(),
                ocr_text=text,
            )
            doc.current_version = version
            doc.save(update_fields=["current_version"])
            semantic_index.sync_document_embeddings(doc)

    def test_command_runs_and_reports_distribution(self):
        out = StringIO()
        call_command("calibrate_embeddings", stdout=out)
        output = out.getvalue()

        self.assertIn("Nächste-Nachbar-Ähnlichkeit", output)
        self.assertIn("Aktuelle Schwellen", output)
        self.assertIn("Perzentile", output)
