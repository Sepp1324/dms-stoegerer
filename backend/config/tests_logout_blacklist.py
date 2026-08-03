"""P2: Serverseitiger Logout blacklistet den Refresh-Token, sodass ein kopierter
Token nach dem Logout nicht mehr refreshbar ist."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

User = get_user_model()


class LogoutBlacklistTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("lo", password="pw", role="user")

    def _obtain_refresh(self) -> str:
        resp = self.client.post(
            "/api/auth/token/", {"username": "lo", "password": "pw"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        return resp.data["refresh"]

    def test_refresh_nach_logout_wird_abgelehnt(self):
        refresh = self._obtain_refresh()
        # Vor dem Logout ist der Refresh gültig.
        before = self.client.post(
            "/api/auth/token/refresh/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(before.status_code, 200)

        # Logout blacklistet den Refresh-Token.
        logout = self.client.post(
            "/api/auth/logout/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(logout.status_code, 205)

        # Derselbe (z. B. kopierte) Refresh-Token ist danach unbrauchbar.
        after = self.client.post(
            "/api/auth/token/refresh/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(after.status_code, 401)

    def test_logout_mit_ungueltigem_token_ist_idempotent(self):
        resp = self.client.post(
            "/api/auth/logout/", {"refresh": "kaputt.token.wert"}, format="json"
        )
        self.assertEqual(resp.status_code, 205)

    def test_logout_ohne_token_ist_idempotent(self):
        resp = self.client.post("/api/auth/logout/", {}, format="json")
        self.assertEqual(resp.status_code, 205)

    def test_logout_mit_listen_payload_kein_500(self):
        # Gültiges JSON `[]` -> request.data ist eine Liste; .get() darauf würde
        # sonst mit AttributeError 500 werfen.
        resp = self.client.post("/api/auth/logout/", [], format="json")
        self.assertEqual(resp.status_code, 205)

    def test_logout_mit_null_payload_kein_500(self):
        resp = self.client.generic(
            "POST", "/api/auth/logout/", "null", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 205)

    def test_logout_mit_nicht_string_refresh_kein_500(self):
        resp = self.client.post("/api/auth/logout/", {"refresh": 123}, format="json")
        self.assertEqual(resp.status_code, 205)


class FlushExpiredTokensTaskTests(APITestCase):
    def test_task_ruft_management_command(self):
        from unittest import mock

        from documents.tasks import flush_expired_jwt_tokens

        with mock.patch("django.core.management.call_command") as cc:
            result = flush_expired_jwt_tokens()
        cc.assert_called_once_with("flushexpiredtokens")
        self.assertEqual(result, {"flushed": True})
