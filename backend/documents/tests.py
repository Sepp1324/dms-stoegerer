"""Regressionstests für die Owner-Isolation von Dokumenten (STOAA-7).

Belegt, dass ein Nutzer ausschließlich eigene Dokumente sieht und jeder
Cross-User-Zugriff (Liste, Detail, Download, Update, Delete, Audit) mit
404 abgewiesen wird – auf Objekt-Ebene, nicht nur in der UI.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import (
    AuditLogEntry,
    Correspondent,
    Document,
    DocumentVersion,
    Tag,
)

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


class AISuggestionsTests(APITestCase):
    """STOAA-45: Datum-Mapping, Dedup, Validierung, Regenerate/Dismiss.

    Deckt den API-Kontrakt ab, an dem Frontend + QA hängen: ``date`` →
    ``created_at``, case-insensitive Stammdaten-Wiederverwendung, Sanitierung,
    ``POST /suggest/`` und ``POST /dismiss_suggestions/`` – jeweils owner-gescoped,
    can_write-gegated und audit-geloggt.
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="owner", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="guest", password="pw", role="guest"
        )

    def _doc(self, **suggestions):
        doc = Document.objects.create(title="Original", owner=self.owner)
        if suggestions:
            doc.ai_suggestions = suggestions
            doc.save(update_fields=["ai_suggestions"])
        return doc

    # --- 1. Datum-Vorschlag → created_at ---------------------------------
    def test_datum_roundtrip_setzt_created_at(self):
        doc = self._doc(date="2023-05-17")
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["date"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.created_at)
        self.assertEqual(doc.created_at.date().isoformat(), "2023-05-17")
        # tz-aware (created_at ist DateTime, USE_TZ=True)
        self.assertIsNotNone(doc.created_at.tzinfo)
        # angewendeter Schlüssel wurde entfernt
        self.assertNotIn("date", doc.ai_suggestions)

    def test_ungueltiges_datum_wird_ignoriert(self):
        doc = self._doc(date="17.05.2023")  # nicht ISO
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["date"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        self.assertIsNone(doc.created_at)
        # nicht angewendet → Vorschlag bleibt stehen
        self.assertEqual(doc.ai_suggestions.get("date"), "17.05.2023")

    def test_leeres_datum_wird_ignoriert(self):
        doc = self._doc(date="")
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["date"]},
            format="json",
        )
        doc.refresh_from_db()
        self.assertIsNone(doc.created_at)

    # --- 3. Case-insensitive Dedup ---------------------------------------
    def test_correspondent_case_insensitive_wiederverwendet(self):
        existing = Correspondent.objects.create(name="Finanzamt")
        doc = self._doc(correspondent="finanzamt")
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["correspondent"]},
            format="json",
        )
        doc.refresh_from_db()
        self.assertEqual(doc.correspondent_id, existing.id)
        # kein Groß/Klein-Duplikat angelegt
        self.assertEqual(Correspondent.objects.filter(name__iexact="finanzamt").count(), 1)

    def test_document_type_neu_wenn_kein_bestand(self):
        doc = self._doc(document_type="Rechnung")
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["document_type"]},
            format="json",
        )
        doc.refresh_from_db()
        self.assertEqual(doc.document_type.name, "Rechnung")

    def test_tags_case_insensitive_dedup(self):
        existing = Tag.objects.create(name="Finanzen")
        doc = self._doc(tags=["finanzen", "Steuer"])
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["tags"]},
            format="json",
        )
        doc.refresh_from_db()
        names = sorted(t.name for t in doc.tags.all())
        # 'finanzen' auf Bestand 'Finanzen' gemappt, 'Steuer' neu
        self.assertEqual(names, ["Finanzen", "Steuer"])
        self.assertEqual(Tag.objects.filter(name__iexact="finanzen").count(), 1)
        self.assertIn(existing, doc.tags.all())

    # --- 4. Validierung / Sanitierung ------------------------------------
    def test_title_wird_gestrippt_und_gekappt(self):
        doc = self._doc(title="   " + "X" * 300 + "   ")
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["title"]},
            format="json",
        )
        doc.refresh_from_db()
        self.assertEqual(doc.title, "X" * 255)

    def test_nicht_string_vorschlag_wird_ignoriert(self):
        doc = self._doc(correspondent=123, title=["nope"])
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["correspondent", "title"]},
            format="json",
        )
        doc.refresh_from_db()
        self.assertIsNone(doc.correspondent_id)
        self.assertEqual(doc.title, "Original")
        self.assertEqual(Correspondent.objects.count(), 0)

    def test_tags_nicht_strings_werden_uebersprungen(self):
        doc = self._doc(tags=["Gut", 5, None, "  ", "Auch"])
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/documents/{doc.id}/apply_suggestions/",
            {"fields": ["tags"]},
            format="json",
        )
        doc.refresh_from_db()
        self.assertEqual(sorted(t.name for t in doc.tags.all()), ["Auch", "Gut"])

    # --- 5. Regenerate-Endpoint ------------------------------------------
    def test_suggest_provider_unavailable_liefert_200(self):
        doc = self._doc()
        self.client.force_authenticate(self.owner)
        with self.settings(AI_PROVIDER="disabled"):
            resp = self.client.post(f"/api/documents/{doc.id}/suggest/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["source"], "unavailable")
        doc.refresh_from_db()
        self.assertEqual(doc.ai_suggestions, {})
        self.assertIsNone(doc.ai_suggested_at)

    def test_suggest_schreibt_ai_suggestions(self):
        from unittest import mock

        doc = self._doc()
        fake = {
            "source": "ai",
            "provider": "anthropic",
            "suggestions": {"title": "Stromrechnung", "date": "2024-01-02"},
        }
        self.client.force_authenticate(self.owner)
        with mock.patch("ai.services.suggest_metadata", return_value=fake):
            resp = self.client.post(f"/api/documents/{doc.id}/suggest/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["source"], "ai")
        doc.refresh_from_db()
        self.assertEqual(doc.ai_suggestions["title"], "Stromrechnung")
        self.assertEqual(doc.ai_suggestions["date"], "2024-01-02")
        self.assertIsNotNone(doc.ai_suggested_at)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="suggest", object_id=str(doc.id)
            ).exists()
        )

    # --- 6. Dismiss-Endpoint ---------------------------------------------
    def test_dismiss_entfernt_felder(self):
        doc = self._doc(title="A", correspondent="B", date="2024-01-01")
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/documents/{doc.id}/dismiss_suggestions/",
            {"fields": ["title", "date"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        self.assertNotIn("title", doc.ai_suggestions)
        self.assertNotIn("date", doc.ai_suggestions)
        # nicht genannter Vorschlag bleibt stehen
        self.assertEqual(doc.ai_suggestions.get("correspondent"), "B")
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="dismiss_suggestions", object_id=str(doc.id)
            ).exists()
        )

    # --- Owner-Scoping & can_write ---------------------------------------
    def test_suggest_fremd_404(self):
        doc = self._doc()
        self.client.force_authenticate(self.other)
        resp = self.client.post(f"/api/documents/{doc.id}/suggest/", {}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_dismiss_fremd_404(self):
        doc = self._doc(title="A")
        self.client.force_authenticate(self.other)
        resp = self.client.post(
            f"/api/documents/{doc.id}/dismiss_suggestions/",
            {"fields": ["title"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_guest_kein_schreibrecht(self):
        doc = self._doc(title="A")
        # Gast ist nicht owner → 404 schützt bereits; eigener Gast-Doc → 403.
        guest_doc = Document.objects.create(title="G", owner=self.guest)
        guest_doc.ai_suggestions = {"title": "Neu"}
        guest_doc.save(update_fields=["ai_suggestions"])
        self.client.force_authenticate(self.guest)
        for path in ("apply_suggestions", "suggest", "dismiss_suggestions"):
            resp = self.client.post(
                f"/api/documents/{guest_doc.id}/{path}/",
                {"fields": ["title"]},
                format="json",
            )
            self.assertEqual(resp.status_code, 403, path)
