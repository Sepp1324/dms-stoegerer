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
    DocumentType,
    DocumentVersion,
    ImmutableVersionError,
    RetentionError,
    RetentionPolicy,
    StoragePath,
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


class StoragePathFilterTests(APITestCase):
    """Listenfilter nach Speicherpfad und Mehrfach-Tag (STOAA-49)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="pathfilter", password="pw", role="user"
        )
        cls.sp_a = StoragePath.objects.create(name="Archiv A")
        cls.sp_b = StoragePath.objects.create(name="Archiv B")
        cls.tag_x = Tag.objects.create(name="Steuer")
        cls.tag_y = Tag.objects.create(name="Versicherung")

        cls.doc_a = Document.objects.create(
            title="Doc A", owner=cls.user, storage_path=cls.sp_a
        )
        cls.doc_a.tags.add(cls.tag_x)
        cls.doc_b = Document.objects.create(
            title="Doc B", owner=cls.user, storage_path=cls.sp_b
        )
        cls.doc_b.tags.add(cls.tag_y)
        # Ohne Speicherpfad → darf bei storage_path-Filter nie auftauchen.
        cls.doc_none = Document.objects.create(title="Doc ohne Pfad", owner=cls.user)

    def _ids(self, resp):
        data = resp.json()
        results = data["results"] if isinstance(data, dict) else data
        return {d["id"] for d in results}

    def test_storage_path_filter(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/?storage_path={self.sp_a.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._ids(resp), {self.doc_a.id})

    def test_ohne_filter_alle_sichtbar(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/documents/")
        self.assertEqual(
            self._ids(resp), {self.doc_a.id, self.doc_b.id, self.doc_none.id}
        )

    def test_multi_tag_filter_oder(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(
            f"/api/documents/?tag={self.tag_x.id}&tag={self.tag_y.id}"
        )
        self.assertEqual(self._ids(resp), {self.doc_a.id, self.doc_b.id})

    def test_single_tag_filter_abwaertskompatibel(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/?tag={self.tag_x.id}")
        self.assertEqual(self._ids(resp), {self.doc_a.id})


# ---------------------------------------------------------------------------
# WORM/Immutable + Aufbewahrungsfristen (STOAA-54, Stufe 4)
# ---------------------------------------------------------------------------
import os
import tempfile
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from . import pipeline, storage


class WormImmutableTests(TestCase):
    """WORM-Schutz: versiegelte (is_immutable) Versionen sind unveränderlich."""

    def setUp(self):
        self.doc = Document.objects.create(title="Rechnung 2026")
        self.version = DocumentVersion.objects.create(
            document=self.doc,
            version_no=1,
            file_path="/data/originals/rechnung.pdf",
            sha256="b" * 64,
            is_immutable=True,
        )

    def test_immutable_version_kann_nicht_ueberschrieben_werden(self):
        self.version.ocr_text = "manipuliert"
        with self.assertRaises(ImmutableVersionError):
            self.version.save(update_fields=["ocr_text"])
        # Audit-Eintrag action="immutable_block" muss entstanden sein.
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="immutable_block", object_id=str(self.version.id)
            ).exists()
        )
        # Persistierter Wert bleibt unverändert.
        self.version.refresh_from_db()
        self.assertNotEqual(self.version.ocr_text, "manipuliert")

    def test_immutable_version_kann_nicht_geloescht_werden(self):
        with self.assertRaises(ImmutableVersionError):
            self.version.delete()
        self.assertTrue(
            DocumentVersion.objects.filter(pk=self.version.pk).exists()
        )
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="immutable_block", object_id=str(self.version.id)
            ).exists()
        )

    def test_uebergang_false_true_ist_erlaubt(self):
        """Das Versiegeln (False → True) selbst darf nicht blockiert werden."""
        doc = Document.objects.create(title="Bescheid")
        v = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/x.pdf", sha256="c" * 64
        )
        v.is_immutable = True
        v.save(update_fields=["is_immutable"])  # darf NICHT werfen
        v.refresh_from_db()
        self.assertTrue(v.is_immutable)


class ChmodReadonlyTests(TestCase):
    """chmod 0444 auf der Archiv-Datei beim Versiegeln."""

    def test_make_readonly_setzt_0444(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            self.assertTrue(storage.make_readonly(path))
            mode = os.stat(path).st_mode & 0o777
            self.assertEqual(mode, 0o444)
        finally:
            os.chmod(path, 0o644)
            os.remove(path)

    def test_seal_version_setzt_immutable_und_chmod(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            doc = Document.objects.create(title="Vertrag")
            v = DocumentVersion.objects.create(
                document=doc,
                version_no=1,
                file_path="/orig.pdf",
                archive_path=path,
                sha256="d" * 64,
            )
            pipeline.seal_version(v)
            v.refresh_from_db()
            self.assertTrue(v.is_immutable)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o444)
            self.assertTrue(
                AuditLogEntry.objects.filter(
                    action="immutable_set", object_id=str(v.id)
                ).exists()
            )
        finally:
            os.chmod(path, 0o644)
            os.remove(path)


class RetentionTests(APITestCase):
    """Aufbewahrungsfristen: Löschsperre bis retention_until."""

    def setUp(self):
        self.dtype = DocumentType.objects.create(name="Rechnung")
        RetentionPolicy.objects.create(
            document_type=self.dtype, retention_months=120
        )

    def test_compute_retention_until_aus_policy(self):
        doc = Document.objects.create(
            title="R1",
            document_type=self.dtype,
            created_at=timezone.now(),
        )
        until = doc.compute_retention_until()
        self.assertIsNotNone(until)
        self.assertGreater(until, timezone.now())

    def test_ohne_policy_keine_frist(self):
        doc = Document.objects.create(title="Notiz")
        self.assertIsNone(doc.compute_retention_until())

    def test_loeschen_vor_fristende_gesperrt(self):
        doc = Document.objects.create(title="R2", document_type=self.dtype)
        doc.retention_until = timezone.now() + timedelta(days=365)
        doc.save(update_fields=["retention_until"])
        with self.assertRaises(RetentionError):
            doc.delete()
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="retention_block", object_id=str(doc.pk)
            ).exists()
        )

    def test_loeschen_nach_fristende_erlaubt(self):
        doc = Document.objects.create(title="R3", document_type=self.dtype)
        doc.retention_until = timezone.now() - timedelta(days=1)
        doc.save(update_fields=["retention_until"])
        doc.delete()  # Frist abgelaufen → erlaubt
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())

    def test_api_delete_liefert_423_bei_aktiver_frist(self):
        writer = User.objects.create_user(
            username="worm-writer", password="pw", role="user"
        )
        doc = Document.objects.create(
            title="R4", owner=writer, document_type=self.dtype
        )
        doc.retention_until = timezone.now() + timedelta(days=30)
        doc.save(update_fields=["retention_until"])
        self.client.force_authenticate(writer)
        resp = self.client.delete(f"/api/documents/{doc.id}/")
        self.assertEqual(resp.status_code, 423)
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
