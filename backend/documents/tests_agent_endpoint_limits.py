"""P2: Agent-/Such-Endpunkte sind begrenzt (AI-Throttle + Aktions-Deckel)."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .throttling import AiRateThrottle
from .views import AgentExecuteView, AgentPlanView, HybridSearchView

User = get_user_model()


class AgentExecuteLimitTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("ael", password="pw", role="user")
        self.client.force_authenticate(self.user)

    def test_zu_viele_aktionen_400(self):
        actions = [
            {"action": "add_tag", "document": 1, "params": {"tag": "x"}}
            for _ in range(AgentExecuteView.MAX_ACTIONS + 1)
        ]
        resp = self.client.post("/api/agent/execute/", {"actions": actions}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_actions_kein_list_400(self):
        resp = self.client.post("/api/agent/execute/", {"actions": "hack"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_genau_am_limit_erlaubt(self):
        # MAX_ACTIONS Einträge sind zulässig (Dokument-IDs existieren nicht ->
        # Fehler pro Aktion, aber KEIN 400 durch den Deckel).
        actions = [
            {"action": "add_tag", "document": 999000 + i, "params": {"tag": "x"}}
            for i in range(AgentExecuteView.MAX_ACTIONS)
        ]
        resp = self.client.post("/api/agent/execute/", {"actions": actions}, format="json")
        self.assertEqual(resp.status_code, 200)


class AiThrottleWiringTests(APITestCase):
    def test_hybrid_und_plan_haben_ai_throttle(self):
        self.assertIn(AiRateThrottle, HybridSearchView.throttle_classes)
        self.assertIn(AiRateThrottle, AgentPlanView.throttle_classes)
