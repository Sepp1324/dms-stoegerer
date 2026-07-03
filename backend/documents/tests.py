"""Regressionstests für die Owner-Isolation von Dokumenten (STOAA-7).

Belegt, dass ein Nutzer ausschließlich eigene Dokumente sieht und jeder
Cross-User-Zugriff (Liste, Detail, Download, Update, Delete, Audit) mit
404 abgewiesen wird – auf Objekt-Ebene, nicht nur in der UI.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Document, DocumentVersion

User = get_user_model()


class OwnerIsolationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sebastian = User.objects.create_user(
            username="sebastian", password="pw", role="user"
        )
        cls.manfred = User.objects.create_user(
            username="manfred", password="pw", role="user"
        )
        cls.admin = User.objects.create_user(
            username="admin", password="pw", role="admin"
        )

        # Ein Dokument von sebastian (mit Version → Detail/Audit realistisch).
        cls.doc = Document.objects.create(title="Sebastians Steuerbescheid", owner=cls.sebastian)
        version = DocumentVersion.objects.create(
            document=cls.doc,
            version_no=1,
            file_path="/data/originals/sebastian.pdf",
            sha256="a" * 64,
        )
        cls.doc.current_version = version
        cls.doc.save(update_fields=["current_version"])

    # --- Liste / Suche ----------------------------------------------------
    def test_manfred_sieht_keine_fremden_dokumente_in_liste(self):
        self.client.force_authenticate(self.manfred)
        resp = self.client.get("/api/documents/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)
        self.assertEqual(resp.data["results"], [])

    def test_owner_sieht_eigene_dokumente(self):
        self.client.force_authenticate(self.sebastian)
        resp = self.client.get("/api/documents/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["id"], self.doc.id)

    def test_admin_sieht_alle_dokumente(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get("/api/documents/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)

    # --- Direktzugriff per ID (kein Datenabfluss) -------------------------
    def test_manfred_detail_fremd_404(self):
        self.client.force_authenticate(self.manfred)
        resp = self.client.get(f"/api/documents/{self.doc.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_manfred_download_preview_fremd_404(self):
        self.client.force_authenticate(self.manfred)
        resp = self.client.get(f"/api/documents/{self.doc.id}/preview/")
        self.assertEqual(resp.status_code, 404)

    def test_manfred_thumbnail_fremd_404(self):
        self.client.force_authenticate(self.manfred)
        resp = self.client.get(f"/api/documents/{self.doc.id}/thumbnail/")
        self.assertEqual(resp.status_code, 404)

    def test_manfred_audit_fremd_404(self):
        """Abgeleitete Ansicht: Audit-Trail ist ebenfalls owner-gescoped."""
        self.client.force_authenticate(self.manfred)
        resp = self.client.get(f"/api/documents/{self.doc.id}/audit/")
        self.assertEqual(resp.status_code, 404)

    def test_manfred_update_fremd_404(self):
        self.client.force_authenticate(self.manfred)
        resp = self.client.patch(
            f"/api/documents/{self.doc.id}/", {"title": "gekapert"}, format="json"
        )
        self.assertEqual(resp.status_code, 404)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.title, "Sebastians Steuerbescheid")

    def test_manfred_delete_fremd_404(self):
        self.client.force_authenticate(self.manfred)
        resp = self.client.delete(f"/api/documents/{self.doc.id}/")
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Document.objects.filter(id=self.doc.id).exists())

    # --- Owner-Feld nicht manipulierbar -----------------------------------
    def test_owner_wird_serverseitig_gesetzt_bei_create(self):
        """POST mit fremdem owner → Dokument gehört trotzdem dem Ersteller."""
        self.client.force_authenticate(self.manfred)
        resp = self.client.post(
            "/api/documents/",
            {"title": "Manfreds Notiz", "owner": self.sebastian.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        created = Document.objects.get(id=resp.data["id"])
        self.assertEqual(created.owner_id, self.manfred.id)

    def test_owner_nicht_per_patch_reassignbar(self):
        """Eigenes Dokument kann nicht per owner-Feld verschenkt/übernommen werden."""
        mine = Document.objects.create(title="Manfreds Beleg", owner=self.manfred)
        self.client.force_authenticate(self.manfred)
        resp = self.client.patch(
            f"/api/documents/{mine.id}/",
            {"owner": self.sebastian.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        mine.refresh_from_db()
        self.assertEqual(mine.owner_id, self.manfred.id)


class OrderingTests(APITestCase):
    """Sortier-Parameter der Dokumentliste (STOAA-36).

    Belegt: ohne ``ordering`` gilt der Standard (``-added_at``); mit
    explizitem ``ordering`` sortiert der whitelisted OrderingFilter um,
    nicht-whitelisted Felder werden ignoriert.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="sortuser", password="pw", role="user"
        )
        # added_at ist auto_now_add → nach dem Anlegen explizit setzen, damit
        # die Reihenfolge deterministisch prüfbar ist. Titel bewusst gegen die
        # Datums-Reihenfolge gewählt, um beide Sortierungen zu unterscheiden.
        cls.beta = Document.objects.create(title="Beta", owner=cls.user)
        cls.alpha = Document.objects.create(title="Alpha", owner=cls.user)
        cls.gamma = Document.objects.create(title="Gamma", owner=cls.user)
        Document.objects.filter(id=cls.beta.id).update(added_at="2026-01-01T00:00:00Z")
        Document.objects.filter(id=cls.alpha.id).update(added_at="2026-02-01T00:00:00Z")
        Document.objects.filter(id=cls.gamma.id).update(added_at="2026-03-01T00:00:00Z")

    def _titles(self, resp):
        return [row["title"] for row in resp.data["results"]]

    def test_default_ordering_neueste_zuerst(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/documents/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._titles(resp), ["Gamma", "Alpha", "Beta"])

    def test_ordering_added_at_aufsteigend(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/documents/?ordering=added_at")
        self.assertEqual(self._titles(resp), ["Beta", "Alpha", "Gamma"])

    def test_ordering_title_az(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/documents/?ordering=title")
        self.assertEqual(self._titles(resp), ["Alpha", "Beta", "Gamma"])

    def test_ordering_title_za(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/documents/?ordering=-title")
        self.assertEqual(self._titles(resp), ["Gamma", "Beta", "Alpha"])

    def test_ordering_nicht_whitelisted_wird_ignoriert(self):
        """Nicht freigegebenes Feld (owner) fällt auf Standard-Sortierung zurück."""
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/documents/?ordering=owner")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._titles(resp), ["Gamma", "Alpha", "Beta"])
