"""P3-Fixes: Health meldet die echte Build-Version; annotierte 0-Counts werden
nicht fälschlich per Extra-Query nachgezählt (N+1)."""
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from documents.serializers import (
    CaseFileSerializer,
    DossierSerializer,
    KnowledgeEntitySerializer,
)


@override_settings(APP_VERSION="abc1234", GIT_SHA="abc1234")
class HealthVersionTests(SimpleTestCase):
    databases = {"default"}

    def test_health_meldet_build_version(self):
        resp = APIClient().get(reverse("health"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["version"], "abc1234")
        self.assertEqual(resp.json()["commit"], "abc1234")


class LivezTests(SimpleTestCase):
    """Liveness (P2): antwortet 200 OHNE DB-Zugriff.

    Bewusst ``SimpleTestCase`` OHNE ``databases`` – hier ist jeder DB-Zugriff
    verboten. Dass ``livez`` trotzdem 200 liefert, BEWEIST damit, dass es die DB
    nicht anfasst (würde es die DB berühren, schlüge der Test hier fehl). Kein
    ``connection.cursor``-Mock nötig (der kollidiert mit dem DB-Guard von
    SimpleTestCase)."""

    def test_livez_ohne_db_200(self):
        resp = APIClient().get(reverse("livez"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "alive")


class CountAnnotationTests(SimpleTestCase):
    """Annotierter Wert 0 muss zurückgegeben werden – NICHT via or-Fallback neu
    gezählt werden (das war die N+1-Quelle bei leeren Akten/Dossiers/Entitäten).
    Der Stub hat KEINE Relationen; ein Fallback-Count würde hier scheitern."""

    def test_casefile_zero_wird_nicht_nachgezaehlt(self):
        obj = SimpleNamespace(document_count=0)
        self.assertEqual(CaseFileSerializer().get_document_count(obj), 0)

    def test_dossier_zero_wird_nicht_nachgezaehlt(self):
        obj = SimpleNamespace(document_count=0)
        self.assertEqual(DossierSerializer().get_document_count(obj), 0)

    def test_entity_zero_wird_nicht_nachgezaehlt(self):
        obj = SimpleNamespace(document_count=0)
        self.assertEqual(KnowledgeEntitySerializer().get_document_count(obj), 0)
