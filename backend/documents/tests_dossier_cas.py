"""P2: Dossier-Generate überschreibt ein zwischenzeitlich finalisiertes Dossier
nicht, und Dossier-/Akten-KI-Endpunkte sind gedrosselt."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Dossier, Document
from .services import dossiers as dossier_service
from .throttling import AiRateThrottle
from .views import CaseFileViewSet, DossierViewSet

User = get_user_model()


class DossierGenerateCasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("dos", password="pw", role="user")

    def test_generate_ueberschreibt_finalisiertes_nicht(self):
        dossier = Dossier.objects.create(
            title="Fall", query="Frage", owner=self.user,
            status=Dossier.Status.DRAFT,
        )

        # build_summary steht stellvertretend für den (langsamen) KI-Aufruf: ein
        # paralleler Request finalisiert das Dossier GENAU während dieses Aufrufs.
        def finalize_midway(*args, **kwargs):
            Dossier.objects.filter(pk=dossier.pk).update(status=Dossier.Status.FINAL)
            return ("Zusammenfassung", Dossier.Source.LOCAL)

        with mock.patch.object(
            dossier_service, "build_summary", side_effect=finalize_midway
        ):
            result = dossier_service.generate_dossier(dossier, Document.objects.none())

        # Nicht auf GENERATED zurückgesetzt.
        self.assertEqual(result.status, Dossier.Status.FINAL)
        dossier.refresh_from_db()
        self.assertEqual(dossier.status, Dossier.Status.FINAL)


class AiThrottleWiringTests(TestCase):
    def test_dossier_generate_und_akten_summarize_gedrosselt(self):
        self.assertIn(
            AiRateThrottle, DossierViewSet.generate.kwargs["throttle_classes"]
        )
        self.assertIn(
            AiRateThrottle, CaseFileViewSet.summarize.kwargs["throttle_classes"]
        )
