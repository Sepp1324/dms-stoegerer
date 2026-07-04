"""Regressionstests für die Owner-Isolation von Dokumenten (STOAA-7).

Belegt, dass ein Nutzer ausschließlich eigene Dokumente sieht und jeder
Cross-User-Zugriff (Liste, Detail, Download, Update, Delete, Audit) mit
404 abgewiesen wird – auf Objekt-Ebene, nicht nur in der UI.
"""
import os
import tempfile
from contextlib import contextmanager
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from . import pipeline
from .classification import apply_rules, rule_matches
from .models import (
    AuditLogEntry,
    ClassificationRule,
    Correspondent,
    CustomField,
    CustomFieldValue,
    Document,
    DocumentShareLink,
    DocumentType,
    DocumentVersion,
    MailAccount,
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


class MailClassificationRuleTests(TestCase):
    """E-Mail-spezifische Regeln (STOAA Stufe 4): subject_contains / from_contains.

    Belegt, dass Regeln zusätzlich zum OCR-Text auf Betreff und Absender der
    Quell-Mail matchen, dass reine Text-Regeln unverändert greifen und dass
    Mail-Bedingungen bei Nicht-Mail-Dokumenten (leere Felder) nicht feuern.
    """

    def _doc(self, *, title="", mail_subject="", mail_sender=""):
        return Document.objects.create(
            title=title,
            mail_subject=mail_subject,
            mail_sender=mail_sender,
        )

    def test_regel_matcht_per_betreff(self):
        ClassificationRule.objects.create(
            name="Betreff-Rechnung",
            match={"subject_contains": ["Rechnung", "Invoice"]},
            then={"document_type": "Rechnung", "tags": ["Finanzen"]},
        )
        doc = self._doc(title="anhang", mail_subject="Ihre RECHNUNG Nr. 4711")

        result = apply_rules(doc)

        self.assertEqual(result["rules"], ["Betreff-Rechnung"])
        doc.refresh_from_db()
        self.assertEqual(doc.document_type.name, "Rechnung")
        self.assertTrue(doc.tags.filter(name="Finanzen").exists())
        self.assertEqual(doc.classification["rules"], ["Betreff-Rechnung"])
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="classify", object_id=str(doc.id)
            ).exists()
        )

    def test_regel_matcht_per_absender(self):
        ClassificationRule.objects.create(
            name="Absender-Stadtwerke",
            match={"from_contains": ["@stadtwerke.de"]},
            then={"correspondent": "Stadtwerke"},
        )
        doc = self._doc(
            title="anhang", mail_sender="Stadtwerke Musterstadt <abrechnung@stadtwerke.de>"
        )

        result = apply_rules(doc)

        self.assertEqual(result["rules"], ["Absender-Stadtwerke"])
        doc.refresh_from_db()
        self.assertEqual(doc.correspondent.name, "Stadtwerke")

    def test_text_only_regel_unveraendert(self):
        ClassificationRule.objects.create(
            name="Text-Rechnung",
            match={"text_contains": ["rechnung"]},
            then={"document_type": "Rechnung"},
        )
        # Nicht-Mail-Dokument (leere Mail-Felder), Treffer nur über Titel/Text.
        doc = self._doc(title="Monatsrechnung Strom")

        result = apply_rules(doc)

        self.assertEqual(result["rules"], ["Text-Rechnung"])
        doc.refresh_from_db()
        self.assertEqual(doc.document_type.name, "Rechnung")

    def test_mail_bedingung_feuert_nicht_ohne_mail_metadaten(self):
        ClassificationRule.objects.create(
            name="Nur-Betreff",
            match={"subject_contains": ["Rechnung"]},
            then={"document_type": "Rechnung"},
        )
        # Kein Betreff gesetzt -> Bedingung greift nicht (keine Alles-Treffer).
        doc = self._doc(title="Enthält das Wort Rechnung im Titel")

        result = apply_rules(doc)

        self.assertEqual(result["rules"], [])
        doc.refresh_from_db()
        self.assertIsNone(doc.document_type)

    def test_kombinierte_bedingungen_und_verknuepft(self):
        # subject UND text müssen beide treffen (AND über Bedingungsarten).
        rule = ClassificationRule.objects.create(
            name="Betreff+Text",
            match={"subject_contains": ["Rechnung"], "text_contains": ["strom"]},
            then={"tags": ["Energie"]},
        )
        self.assertTrue(
            rule_matches(rule, "monatsstrom abrechnung", subject="Rechnung Juni")
        )
        # Betreff passt, Text nicht -> kein Treffer.
        self.assertFalse(rule_matches(rule, "irgendwas", subject="Rechnung Juni"))

    def test_leere_liste_ist_keine_bedingung(self):
        # Leere subject_contains-Liste darf nicht zum Alles-Treffer führen.
        rule = ClassificationRule.objects.create(
            name="Leer",
            match={"subject_contains": []},
            then={"tags": ["X"]},
        )
        self.assertFalse(rule_matches(rule, "text", subject="beliebig"))


class MailAccountAdminFormTests(TestCase):
    """STOAA-33 Punkt 1: Klartext-Passwort im Admin maskiert (write-only)."""

    def _acc(self, password="geheim"):
        return MailAccount.objects.create(
            name="Rechnungen", host="imap.example.org", username="u", password=password
        )

    def _data(self, **over):
        data = {
            "name": "Rechnungen",
            "host": "imap.example.org",
            "port": 993,
            "use_ssl": True,
            "username": "u",
            "folder": "INBOX",
            "password_env": "",
            "password": "",
        }
        data.update(over)
        return data

    def test_leeres_passwort_behaelt_bestehendes(self):
        from .admin import MailAccountAdminForm

        acc = self._acc(password="geheim")
        form = MailAccountAdminForm(data=self._data(password=""), instance=acc)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        # Passwort ist at-rest verschlüsselt (STOAA-212) – semantisch prüfen.
        self.assertNotIn("geheim", obj.password)
        self.assertEqual(obj.resolve_password(), "geheim")

    def test_neues_passwort_ersetzt(self):
        from .admin import MailAccountAdminForm

        acc = self._acc(password="alt")
        form = MailAccountAdminForm(data=self._data(password="neu"), instance=acc)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        # Passwort ist at-rest verschlüsselt (STOAA-212) – semantisch prüfen.
        self.assertNotIn("neu", obj.password)
        self.assertEqual(obj.resolve_password(), "neu")

    def test_gespeichertes_passwort_nicht_im_html(self):
        from .admin import MailAccountAdminForm

        acc = self._acc(password="geheim")
        form = MailAccountAdminForm(instance=acc)
        # render_value=False → der gespeicherte Wert darf nicht zurückgerendert werden.
        self.assertNotIn("geheim", str(form["password"]))


class MailFetchLockTests(TestCase):
    """STOAA-33 Punkt 3: Overlap-Lock überspringt einen laufenden Konto-Abruf."""

    def test_belegter_lock_ueberspringt_abruf(self):
        from unittest import mock

        from . import mail, tasks

        @contextmanager
        def _busy(_account_id):
            yield False

        with mock.patch.object(mail, "account_fetch_lock", _busy):
            result = tasks.fetch_mail_account(123)
        self.assertEqual(result["status"], "locked")

    def test_freier_lock_ruft_ab(self):
        from unittest import mock

        from . import mail, tasks

        @contextmanager
        def _free(_account_id):
            yield True

        acc = MailAccount.objects.create(
            name="R", host="h", username="u", enabled=True
        )
        with mock.patch.object(mail, "account_fetch_lock", _free), mock.patch.object(
            mail, "fetch_account", return_value={"status": "ok", "account_id": acc.id}
        ) as fetched:
            result = tasks.fetch_mail_account(acc.id)
        self.assertEqual(result["status"], "ok")
        fetched.assert_called_once()


class ApprovalWorkflowTests(APITestCase):
    """STOAA-63: Freigabe-Workflow – submit/approve/reject, Übergänge, Audit.

    Deckt den Kontrakt für FE (STOAA-59) und QA (STOAA-60) ab: gültige und
    ungültige Statusübergänge (409, Status unverändert), Gast-403 auf allen
    drei Actions sowie je Übergang genau ein ``AuditLogEntry`` mit from/to.
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

    def _doc(self, status=Document.ApprovalStatus.ENTWURF, owner=None):
        return Document.objects.create(
            title="Freigabe-Doc", owner=owner or self.owner, status=status
        )

    def _audit(self, doc, action):
        return AuditLogEntry.objects.filter(
            object_type="Document", object_id=str(doc.id), action=action
        )

    # --- Default & Übergänge ---------------------------------------------
    def test_default_status_entwurf(self):
        doc = Document.objects.create(title="Neu", owner=self.owner)
        self.assertEqual(doc.status, Document.ApprovalStatus.ENTWURF)

    def test_submit_dann_approve(self):
        doc = self._doc()
        self.client.force_authenticate(self.owner)

        resp = self.client.post(f"/api/documents/{doc.id}/submit/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "zur_freigabe")
        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.ApprovalStatus.ZUR_FREIGABE)

        resp = self.client.post(f"/api/documents/{doc.id}/approve/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "freigegeben")
        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.ApprovalStatus.FREIGEGEBEN)

        # je Übergang genau ein Audit-Eintrag mit korrektem from/to.
        submit = self._audit(doc, "submit")
        self.assertEqual(submit.count(), 1)
        self.assertEqual(submit.first().detail["from"], "entwurf")
        self.assertEqual(submit.first().detail["to"], "zur_freigabe")
        approve = self._audit(doc, "approve")
        self.assertEqual(approve.count(), 1)
        self.assertEqual(approve.first().detail["from"], "zur_freigabe")
        self.assertEqual(approve.first().detail["to"], "freigegeben")

    def test_submit_dann_reject_mit_grund(self):
        doc = self._doc()
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/documents/{doc.id}/submit/", {}, format="json")

        resp = self.client.post(
            f"/api/documents/{doc.id}/reject/",
            {"reason": "Unterschrift fehlt"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "abgelehnt")
        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.ApprovalStatus.ABGELEHNT)

        reject = self._audit(doc, "reject")
        self.assertEqual(reject.count(), 1)
        self.assertEqual(reject.first().detail["from"], "zur_freigabe")
        self.assertEqual(reject.first().detail["to"], "abgelehnt")
        self.assertEqual(reject.first().detail["reason"], "Unterschrift fehlt")

    def test_abgelehnt_kann_erneut_eingereicht_werden(self):
        doc = self._doc(status=Document.ApprovalStatus.ABGELEHNT)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/documents/{doc.id}/submit/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.ApprovalStatus.ZUR_FREIGABE)

    def test_reject_ohne_grund_erlaubt(self):
        doc = self._doc(status=Document.ApprovalStatus.ZUR_FREIGABE)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/documents/{doc.id}/reject/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self._audit(doc, "reject").first().detail["reason"])

    # --- Ungültige Übergänge → 409, Status unverändert, kein Audit -------
    def test_approve_aus_entwurf_unzulaessig(self):
        doc = self._doc()  # entwurf
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/documents/{doc.id}/approve/", {}, format="json")
        self.assertEqual(resp.status_code, 409)
        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.ApprovalStatus.ENTWURF)
        self.assertEqual(self._audit(doc, "approve").count(), 0)

    def test_reject_aus_entwurf_unzulaessig(self):
        doc = self._doc()  # entwurf
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/documents/{doc.id}/reject/", {}, format="json")
        self.assertEqual(resp.status_code, 409)
        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.ApprovalStatus.ENTWURF)

    def test_submit_aus_freigegeben_unzulaessig(self):
        doc = self._doc(status=Document.ApprovalStatus.FREIGEGEBEN)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/documents/{doc.id}/submit/", {}, format="json")
        self.assertEqual(resp.status_code, 409)
        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.ApprovalStatus.FREIGEGEBEN)

    # --- Owner-Scoping & Gast-Rechte -------------------------------------
    def test_fremd_404(self):
        doc = self._doc(status=Document.ApprovalStatus.ZUR_FREIGABE)
        self.client.force_authenticate(self.other)
        for path in ("submit", "approve", "reject"):
            resp = self.client.post(
                f"/api/documents/{doc.id}/{path}/", {}, format="json"
            )
            self.assertEqual(resp.status_code, 404, path)

    def test_gast_403_auf_allen_actions(self):
        guest_doc = self._doc(
            status=Document.ApprovalStatus.ZUR_FREIGABE, owner=self.guest
        )
        self.client.force_authenticate(self.guest)
        for path in ("submit", "approve", "reject"):
            resp = self.client.post(
                f"/api/documents/{guest_doc.id}/{path}/", {}, format="json"
            )
            self.assertEqual(resp.status_code, 403, path)
        # Gast-403 darf keinen Statuswechsel bewirkt haben.
        guest_doc.refresh_from_db()
        self.assertEqual(guest_doc.status, Document.ApprovalStatus.ZUR_FREIGABE)

    def test_status_nicht_per_patch_aenderbar(self):
        doc = self._doc()  # entwurf
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            f"/api/documents/{doc.id}/",
            {"status": "freigegeben"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        # read_only → PATCH ignoriert den Statuswechsel.
        self.assertEqual(doc.status, Document.ApprovalStatus.ENTWURF)


class CustomFieldTests(APITestCase):
    """Zusatzfeld-Definitionen: CRUD, DELETE-Sperre, Typ-Einfrieren (STOAA-109)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="cf_user", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="cf_guest", password="pw", role="guest"
        )
        cls.betrag = CustomField.objects.create(
            name="Rechnungsbetrag", data_type=CustomField.DataType.CURRENCY
        )

    def test_list_und_get(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/custom-fields/")
        self.assertEqual(resp.status_code, 200)
        # Liste ist paginiert (globale DRF-Pagination wie tags/correspondents).
        data = resp.json()
        results = data["results"] if isinstance(data, dict) else data
        names = [f["name"] for f in results]
        self.assertIn("Rechnungsbetrag", names)
        # Kontrakt: genau id/name/data_type.
        self.assertEqual(set(results[0].keys()), {"id", "name", "data_type"})

    def test_create(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            "/api/custom-fields/",
            {"name": "Vertragsnummer", "data_type": "text"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(CustomField.objects.filter(name="Vertragsnummer").exists())

    def test_guest_darf_nicht_anlegen(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post(
            "/api/custom-fields/",
            {"name": "X", "data_type": "text"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_data_type_beim_update_read_only(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/api/custom-fields/{self.betrag.id}/",
            {"name": "Betrag", "data_type": "text"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.betrag.refresh_from_db()
        self.assertEqual(self.betrag.name, "Betrag")  # Name änderbar
        # Typwechsel ignoriert (read_only) – bleibt currency.
        self.assertEqual(self.betrag.data_type, CustomField.DataType.CURRENCY)

    def test_delete_ohne_werte_erlaubt(self):
        field = CustomField.objects.create(name="Temp", data_type="text")
        self.client.force_authenticate(self.user)
        resp = self.client.delete(f"/api/custom-fields/{field.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(CustomField.objects.filter(id=field.id).exists())

    def test_delete_mit_werten_geblockt(self):
        doc = Document.objects.create(title="Rg", owner=self.user)
        CustomFieldValue.objects.create(document=doc, field=self.betrag, value="42")
        self.client.force_authenticate(self.user)
        resp = self.client.delete(f"/api/custom-fields/{self.betrag.id}/")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("detail", resp.json())
        # Feld bleibt erhalten.
        self.assertTrue(CustomField.objects.filter(id=self.betrag.id).exists())


class CustomFieldValueOnDocumentTests(APITestCase):
    """Zusatzfeld-Werte im Document-GET/PATCH: Nested + Upsert (STOAA-109)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="cfv_user", password="pw", role="user"
        )
        cls.betrag = CustomField.objects.create(
            name="Rechnungsbetrag", data_type=CustomField.DataType.CURRENCY
        )
        cls.nummer = CustomField.objects.create(
            name="Vertragsnummer", data_type=CustomField.DataType.TEXT
        )
        cls.doc = Document.objects.create(title="Rechnung", owner=cls.user)

    def test_get_zeigt_nested_werte_mit_typinfo(self):
        CustomFieldValue.objects.create(
            document=self.doc, field=self.betrag, value="199.90"
        )
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.doc.id}/")
        self.assertEqual(resp.status_code, 200)
        cfv = resp.json()["custom_field_values"]
        self.assertEqual(len(cfv), 1)
        self.assertEqual(cfv[0]["field"], self.betrag.id)
        self.assertEqual(cfv[0]["value"], "199.90")
        # Read-only Zusatzangaben für FE-Formatierung ohne Zweit-Request.
        self.assertEqual(cfv[0]["field_name"], "Rechnungsbetrag")
        self.assertEqual(cfv[0]["data_type"], "currency")

    def test_patch_legt_wert_an(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"custom_field_values": [{"field": self.betrag.id, "value": "50"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            CustomFieldValue.objects.get(document=self.doc, field=self.betrag).value,
            "50",
        )

    def test_patch_upsert_aktualisiert_bestehenden_wert(self):
        CustomFieldValue.objects.create(
            document=self.doc, field=self.betrag, value="10"
        )
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"custom_field_values": [{"field": self.betrag.id, "value": "99"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        # Kein Duplikat (unique_together), Wert aktualisiert.
        self.assertEqual(
            CustomFieldValue.objects.filter(
                document=self.doc, field=self.betrag
            ).count(),
            1,
        )
        self.assertEqual(
            CustomFieldValue.objects.get(document=self.doc, field=self.betrag).value,
            "99",
        )

    def test_patch_laesst_nicht_genannte_werte_unberuehrt(self):
        CustomFieldValue.objects.create(
            document=self.doc, field=self.nummer, value="V-123"
        )
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"custom_field_values": [{"field": self.betrag.id, "value": "5"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        # Upsert (kein Replace): der Vertragsnummer-Wert bleibt bestehen.
        self.assertEqual(
            CustomFieldValue.objects.get(document=self.doc, field=self.nummer).value,
            "V-123",
        )

    def test_patch_ohne_key_laesst_werte_unveraendert(self):
        CustomFieldValue.objects.create(
            document=self.doc, field=self.betrag, value="7"
        )
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"title": "Neuer Titel"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            CustomFieldValue.objects.get(document=self.doc, field=self.betrag).value,
            "7",
        )


class CustomFieldFilterTests(APITestCase):
    """Bereichsfilter custom_field_<id>_gte/_lte auf Zusatzfeld-Werten (§7.3)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="cff_user", password="pw", role="user"
        )
        cls.betrag = CustomField.objects.create(
            name="Rechnungsbetrag", data_type=CustomField.DataType.CURRENCY
        )
        # Drei Dokumente mit numerischen Beträgen + eines mit nicht-numerischem Wert.
        cls.d10 = cls._doc_with_value("D10", "10")
        cls.d100 = cls._doc_with_value("D100", "100.50")
        cls.d500 = cls._doc_with_value("D500", "500")
        cls.d_text = cls._doc_with_value("Dtext", "keine Angabe")

    @classmethod
    def _doc_with_value(cls, title, value):
        doc = Document.objects.create(title=title, owner=cls.user)
        CustomFieldValue.objects.create(document=doc, field=cls.betrag, value=value)
        return doc

    def _titles(self, resp):
        data = resp.json()
        results = data["results"] if isinstance(data, dict) else data
        return {d["title"] for d in results}

    def test_gte_filter(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(
            f"/api/documents/?custom_field_{self.betrag.id}_gte=100"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._titles(resp), {"D100", "D500"})

    def test_lte_filter(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(
            f"/api/documents/?custom_field_{self.betrag.id}_lte=100"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._titles(resp), {"D10"})

    def test_gte_und_lte_kombiniert(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(
            f"/api/documents/?custom_field_{self.betrag.id}_gte=10"
            f"&custom_field_{self.betrag.id}_lte=100.50"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._titles(resp), {"D10", "D100"})

    def test_nicht_numerischer_wert_kein_500(self):
        # Der "keine Angabe"-Wert darf keinen Cast-Fehler auslösen.
        self.client.force_authenticate(self.user)
        resp = self.client.get(
            f"/api/documents/?custom_field_{self.betrag.id}_gte=0"
        )
        self.assertEqual(resp.status_code, 200)
        # Nur numerische Werte matchen; der Textwert fällt heraus.
        self.assertEqual(self._titles(resp), {"D10", "D100", "D500"})

    def test_ungueltige_grenze_kein_500(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(
            f"/api/documents/?custom_field_{self.betrag.id}_gte=abc"
        )
        # Ungültige Grenze wird ignoriert → alle eigenen Dokumente.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self._titles(resp), {"D10", "D100", "D500", "Dtext"}
        )


class ConsumeFolderScanTests(TestCase):
    """STOAA-174: NFS-tauglicher Consume-Ordner.

    Deckt den Reife-Check (CONSUME_MIN_AGE), den Normalpfad (ingested +
    ``_processed/``) sowie den Fehlerpfad (``_failed/`` + Scan läuft weiter) ab.
    ``process_document_version.delay`` wird gemockt, damit die Tests ohne
    Celery-Broker/OCR laufen.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.consume = root / "consume"
        self.originals = root / "originals"
        self.consume.mkdir(parents=True, exist_ok=True)
        self.originals.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, *, age_seconds):
        """Legt eine Datei im Consume-Ordner an und setzt ihr Alter (mtime)."""
        import os
        import time

        path = self.consume / name
        path.write_bytes(b"%PDF-1.4 dummy")
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))
        return path

    def _run_scan(self):
        from unittest import mock

        from . import storage, tasks

        with mock.patch.object(storage, "CONSUME_DIR", self.consume), mock.patch.object(
            storage, "ORIGINALS_DIR", self.originals
        ), mock.patch.object(tasks.process_document_version, "delay") as delay:
            result = tasks.scan_consume_folder()
        return result, delay

    def test_zu_junge_datei_wird_uebersprungen(self):
        """(a) Datei jünger als CONSUME_MIN_AGE -> übersprungen, nicht verschoben."""
        with self.settings(CONSUME_MIN_AGE=15):
            self._write("frisch.pdf", age_seconds=0)
            result, delay = self._run_scan()

        self.assertEqual(result["found"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 0)
        # Datei bleibt liegen (kein _processed/_failed), nichts angestoßen.
        self.assertTrue((self.consume / "frisch.pdf").exists())
        self.assertFalse((self.consume / "_processed" / "frisch.pdf").exists())
        delay.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    def test_reife_datei_wird_aufgenommen(self):
        """(b) Datei alt genug -> ingested + nach _processed/ verschoben."""
        with self.settings(CONSUME_MIN_AGE=15):
            self._write("reif.pdf", age_seconds=3600)
            result, delay = self._run_scan()

        self.assertEqual(result["found"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["failed"], 0)
        # Original aus dem Eingang entfernt und nach _processed/ verschoben.
        self.assertFalse((self.consume / "reif.pdf").exists())
        self.assertTrue((self.consume / "_processed" / "reif.pdf").exists())
        # Dokument angelegt, Pipeline (async) angestoßen.
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(Document.objects.get().title, "reif")
        delay.assert_called_once()

    def test_fehlerhafte_datei_landet_in_failed_und_scan_laeuft_weiter(self):
        """(c) Fehlerpfad -> _failed/ + die übrigen Dateien werden verarbeitet."""
        from unittest import mock

        from . import pipeline, storage, tasks

        self._write("bad.pdf", age_seconds=3600)
        self._write("good.pdf", age_seconds=3600)

        real = pipeline.create_document_from_file

        def flaky(path, *, title, **kwargs):
            if title == "bad":
                raise RuntimeError("boom")
            return real(path, title=title, **kwargs)

        with self.settings(CONSUME_MIN_AGE=15):
            with mock.patch.object(
                storage, "CONSUME_DIR", self.consume
            ), mock.patch.object(
                storage, "ORIGINALS_DIR", self.originals
            ), mock.patch.object(
                tasks.process_document_version, "delay"
            ) as delay, mock.patch.object(
                tasks.pipeline, "create_document_from_file", side_effect=flaky
            ):
                result = tasks.scan_consume_folder()

        # Scan wurde nicht abgebrochen: gute Datei ingested, schlechte gezählt.
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["skipped"], 0)
        # Fehlerhafte Datei nach _failed/, nicht verschluckt und nicht in _processed/.
        self.assertTrue((self.consume / "_failed" / "bad.pdf").exists())
        self.assertFalse((self.consume / "_processed" / "bad.pdf").exists())
        # Gute Datei regulär verarbeitet.
        self.assertTrue((self.consume / "_processed" / "good.pdf").exists())
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(Document.objects.get().title, "good")
        delay.assert_called_once()


class ConsumePerUserScanTests(TestCase):
    """STOAA-261: Pro-User-Attribution des Consume-Ingest.

    Bei ``CONSUME_PER_USER=True`` liegen Scans in pro-User-Unterordnern
    (``CONSUME_DIR/<username>/``); Dateien werden dem passenden Django-User als
    ``Document.owner`` zugeordnet. Deckt ab: (a) Zuordnung, (b) unbekannter
    Ordner wird übersprungen + geloggt, (c) Reife-Check, (d) Fehlerpfad →
    ``_failed/`` im User-Ordner, (e) Flag off → Flat-Regression (owner=None,
    Unterordner ignoriert). ``process_document_version.delay`` wird gemockt.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.consume = root / "consume"
        self.originals = root / "originals"
        self.consume.mkdir(parents=True, exist_ok=True)
        self.originals.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)

        self.sebastian = get_user_model().objects.create_user(
            username="sebastian", password="x"
        )

    def _write(self, subdir, name, *, age_seconds):
        """Legt eine Datei in ``consume/<subdir>/`` an und setzt ihr Alter (mtime)."""
        import os
        import time

        folder = self.consume / subdir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(b"%PDF-1.4 dummy")
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))
        return path

    def _run_scan(self):
        from unittest import mock

        from . import storage, tasks

        with mock.patch.object(storage, "CONSUME_DIR", self.consume), mock.patch.object(
            storage, "ORIGINALS_DIR", self.originals
        ), mock.patch.object(tasks.process_document_version, "delay") as delay:
            result = tasks.scan_consume_folder()
        return result, delay

    def test_datei_im_user_ordner_wird_dem_user_zugeordnet(self):
        """(a) /scans/sebastian/reif.pdf -> Document.owner == sebastian."""
        with self.settings(CONSUME_PER_USER=True, CONSUME_MIN_AGE=15):
            self._write("sebastian", "reif.pdf", age_seconds=3600)
            result, delay = self._run_scan()

        self.assertEqual(result["found"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(Document.objects.count(), 1)
        doc = Document.objects.get()
        self.assertEqual(doc.owner, self.sebastian)
        # _processed/ liegt IM User-Ordner, nicht auf Consume-Ebene.
        self.assertTrue((self.consume / "sebastian" / "_processed" / "reif.pdf").exists())
        self.assertFalse((self.consume / "_processed" / "reif.pdf").exists())
        delay.assert_called_once()

    def test_ordnername_case_insensitive(self):
        """Ordner ``Sebastian`` löst denselben User (username__iexact) auf."""
        with self.settings(CONSUME_PER_USER=True, CONSUME_MIN_AGE=15):
            self._write("Sebastian", "reif.pdf", age_seconds=3600)
            result, _ = self._run_scan()

        self.assertEqual(result["found"], 1)
        self.assertEqual(Document.objects.get().owner, self.sebastian)

    def test_unbekannter_ordner_wird_uebersprungen_und_geloggt(self):
        """(b) Ordner ohne passenden User -> nicht aufgenommen + WARN-Log."""
        self._write("unbekannt", "reif.pdf", age_seconds=3600)

        with self.settings(CONSUME_PER_USER=True, CONSUME_MIN_AGE=15):
            with self.assertLogs("documents.tasks", level="WARNING") as cm:
                result, delay = self._run_scan()

        self.assertEqual(result["found"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(Document.objects.count(), 0)
        # Datei bleibt liegen (keine stille owner=None-Aufnahme).
        self.assertTrue((self.consume / "unbekannt" / "reif.pdf").exists())
        self.assertFalse((self.consume / "unbekannt" / "_processed" / "reif.pdf").exists())
        delay.assert_not_called()
        self.assertTrue(any("unbekannt" in line for line in cm.output))

    def test_zu_junge_datei_im_user_ordner_wird_uebersprungen(self):
        """(c) Datei jünger als CONSUME_MIN_AGE -> übersprungen, nicht verschoben."""
        with self.settings(CONSUME_PER_USER=True, CONSUME_MIN_AGE=15):
            self._write("sebastian", "frisch.pdf", age_seconds=0)
            result, delay = self._run_scan()

        self.assertEqual(result["found"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertTrue((self.consume / "sebastian" / "frisch.pdf").exists())
        delay.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    def test_fehlerhafte_datei_landet_in_failed_im_user_ordner(self):
        """(d) Fehlerpfad -> _failed/ im User-Ordner, Scan läuft weiter."""
        from unittest import mock

        from . import pipeline, storage, tasks

        self._write("sebastian", "bad.pdf", age_seconds=3600)
        self._write("sebastian", "good.pdf", age_seconds=3600)

        real = pipeline.create_document_from_file

        def flaky(path, *, title, **kwargs):
            if title == "bad":
                raise RuntimeError("boom")
            return real(path, title=title, **kwargs)

        with self.settings(CONSUME_PER_USER=True, CONSUME_MIN_AGE=15):
            with mock.patch.object(
                storage, "CONSUME_DIR", self.consume
            ), mock.patch.object(
                storage, "ORIGINALS_DIR", self.originals
            ), mock.patch.object(
                tasks.process_document_version, "delay"
            ) as delay, mock.patch.object(
                tasks.pipeline, "create_document_from_file", side_effect=flaky
            ):
                result = tasks.scan_consume_folder()

        self.assertEqual(result["found"], 1)
        self.assertEqual(result["failed"], 1)
        # Fehlerhafte Datei nach _failed/ IM User-Ordner.
        self.assertTrue((self.consume / "sebastian" / "_failed" / "bad.pdf").exists())
        self.assertTrue((self.consume / "sebastian" / "_processed" / "good.pdf").exists())
        # Gute Datei ist sebastian zugeordnet.
        doc = Document.objects.get()
        self.assertEqual(doc.title, "good")
        self.assertEqual(doc.owner, self.sebastian)
        delay.assert_called_once()

    def test_flag_off_flat_regression_ignoriert_unterordner(self):
        """(e) Flag off -> Flat-Modus: Datei direkt im Consume-Ordner, owner=None;
        Unterordner werden ignoriert (kein Pro-User-Verhalten)."""
        import os
        import time

        # Datei direkt im Consume-Ordner (Flat-Eingang).
        flat = self.consume / "flat.pdf"
        flat.write_bytes(b"%PDF-1.4 dummy")
        mtime = time.time() - 3600
        os.utime(flat, (mtime, mtime))
        # Zusätzlich eine Datei in einem User-Unterordner, die im Flat-Modus
        # ignoriert werden muss.
        self._write("sebastian", "ignored.pdf", age_seconds=3600)

        with self.settings(CONSUME_PER_USER=False, CONSUME_MIN_AGE=15):
            result, delay = self._run_scan()

        self.assertEqual(result["found"], 1)
        self.assertEqual(Document.objects.count(), 1)
        doc = Document.objects.get()
        self.assertEqual(doc.title, "flat")
        self.assertIsNone(doc.owner)
        # Flat-Idempotenz auf Consume-Ebene; Unterordner unangetastet.
        self.assertTrue((self.consume / "_processed" / "flat.pdf").exists())
        self.assertTrue((self.consume / "sebastian" / "ignored.pdf").exists())
        delay.assert_called_once()


class DocumentShareLinkTests(APITestCase):
    """Verwaltungs-API der Freigabelinks (STOAA-190).

    Belegt: nur der Hash wird gespeichert (Klartext-Token einmalig),
    Pflicht-Ablauf serverseitig erzwungen, Owner-Scoping und Widerruf.
    """

    BASE = "/api/document-share-links/"

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="s_owner", password="pw", role="user")
        cls.other = User.objects.create_user(username="s_other", password="pw", role="user")
        cls.guest = User.objects.create_user(username="s_guest", password="pw", role="guest")
        cls.admin = User.objects.create_user(username="s_admin", password="pw", role="admin")
        cls.doc = Document.objects.create(title="Freizugebendes Dokument", owner=cls.owner)
        cls.other_doc = Document.objects.create(title="Fremd", owner=cls.other)

    def _future(self, days=7):
        return (timezone.now() + timedelta(days=days)).isoformat()

    # --- Create -----------------------------------------------------------
    def test_create_liefert_token_einmalig_und_speichert_nur_hash(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.BASE, {"document": self.doc.id, "expires_at": self._future()}
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        token = resp.data["token"]
        self.assertGreaterEqual(len(token), 32)
        self.assertNotIn("token_hash", resp.data)
        link = DocumentShareLink.objects.get(id=resp.data["id"])
        # Es wird ausschließlich der Hash gespeichert, nie der Klartext.
        self.assertEqual(link.token_hash, DocumentShareLink.hash_token(token))
        self.assertNotEqual(link.token_hash, token)
        self.assertTrue(link.is_valid)
        self.assertEqual(link.created_by, self.owner)

    def test_create_ohne_expires_at_ist_400_kein_stillschweigendes_nie(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.BASE, {"document": self.doc.id})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(DocumentShareLink.objects.count(), 0)

    def test_create_mit_vergangenem_expires_at_ist_400(self):
        self.client.force_authenticate(self.owner)
        past = (timezone.now() - timedelta(days=1)).isoformat()
        resp = self.client.post(
            self.BASE, {"document": self.doc.id, "expires_at": past}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(DocumentShareLink.objects.count(), 0)

    def test_gast_darf_nicht_erstellen(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post(
            self.BASE, {"document": self.doc.id, "expires_at": self._future()}
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_fuer_fremdes_dokument_ist_404(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.BASE, {"document": self.other_doc.id, "expires_at": self._future()}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(DocumentShareLink.objects.count(), 0)

    # --- List -------------------------------------------------------------
    def test_list_je_dokument_ohne_hash_und_owner_gescoped(self):
        link = DocumentShareLink.objects.create(
            document=self.doc,
            token_hash=DocumentShareLink.hash_token("t"),
            expires_at=timezone.now() + timedelta(days=3),
            created_by=self.owner,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.BASE + f"?document={self.doc.id}")
        self.assertEqual(resp.status_code, 200)
        results = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(results), 1)
        row = results[0]
        self.assertEqual(row["id"], link.id)
        self.assertNotIn("token_hash", row)
        self.assertNotIn("token", row)
        self.assertIn("is_valid", row)

    def test_fremder_sieht_link_nicht(self):
        DocumentShareLink.objects.create(
            document=self.doc,
            token_hash=DocumentShareLink.hash_token("x"),
            expires_at=timezone.now() + timedelta(days=3),
        )
        self.client.force_authenticate(self.other)
        resp = self.client.get(self.BASE)
        results = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(results), 0)

    # --- Revoke -----------------------------------------------------------
    def test_delete_widerruft_soft_und_is_valid_false(self):
        link = DocumentShareLink.objects.create(
            document=self.doc,
            token_hash=DocumentShareLink.hash_token("d"),
            expires_at=timezone.now() + timedelta(days=3),
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(self.BASE + f"{link.id}/")
        self.assertIn(resp.status_code, (200, 204))
        link.refresh_from_db()
        self.assertIsNotNone(link.revoked_at)
        self.assertFalse(link.is_valid)

    def test_patch_revoked_at_widerruft(self):
        link = DocumentShareLink.objects.create(
            document=self.doc,
            token_hash=DocumentShareLink.hash_token("p"),
            expires_at=timezone.now() + timedelta(days=3),
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            self.BASE + f"{link.id}/", {"revoked_at": timezone.now().isoformat()}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["is_valid"])
        link.refresh_from_db()
        self.assertIsNotNone(link.revoked_at)

    def test_fremder_kann_nicht_widerrufen(self):
        link = DocumentShareLink.objects.create(
            document=self.doc,
            token_hash=DocumentShareLink.hash_token("f"),
            expires_at=timezone.now() + timedelta(days=3),
        )
        self.client.force_authenticate(self.other)
        resp = self.client.delete(self.BASE + f"{link.id}/")
        self.assertEqual(resp.status_code, 404)
        link.refresh_from_db()
        self.assertIsNone(link.revoked_at)

    def test_is_valid_false_wenn_abgelaufen(self):
        link = DocumentShareLink.objects.create(
            document=self.doc,
            token_hash=DocumentShareLink.hash_token("e"),
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertFalse(link.is_valid)


class ShareAccessRouteTests(APITestCase):
    """Freigabe-Abrufrouten /api/share/<token>/preview|download (STOAA-191).

    Belegt: Login-Pflicht (IsAuthenticated), 410 Gone bei
    unbekannt/widerrufen/abgelaufen (keine Existenz-Enumeration), Durchbrechen
    der Owner-Isolation ausschließlich für das eine verknüpfte Dokument sowie
    Auditierung je Zugriff.
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="sh_owner", password="pw", role="user"
        )
        cls.viewer = User.objects.create_user(
            username="sh_viewer", password="pw", role="user"
        )
        cls.doc = Document.objects.create(title="Freigegeben", owner=cls.owner)

    def setUp(self):
        # Reale Datei auf Platte, damit FileResponse tatsächlich Bytes liefert.
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4 share test")
        tmp.close()
        self._tmp_path = tmp.name
        self.addCleanup(
            lambda: os.path.exists(self._tmp_path) and os.remove(self._tmp_path)
        )
        self.version = DocumentVersion.objects.create(
            document=self.doc,
            version_no=1,
            file_path=self._tmp_path,
            sha256="b" * 64,
        )
        self.doc.current_version = self.version
        self.doc.save(update_fields=["current_version"])

    def _link(self, *, token, expired=False, revoked=False):
        expires = timezone.now() + (
            timedelta(days=-1) if expired else timedelta(days=7)
        )
        return DocumentShareLink.objects.create(
            document=self.doc,
            token_hash=DocumentShareLink.hash_token(token),
            expires_at=expires,
            revoked_at=timezone.now() if revoked else None,
            created_by=self.owner,
        )

    # --- Login-Pflicht ----------------------------------------------------
    def test_download_verlangt_login(self):
        self._link(token="t1")
        resp = self.client.get("/api/share/t1/download")
        self.assertIn(resp.status_code, (401, 403))

    def test_preview_verlangt_login(self):
        self._link(token="t1b")
        resp = self.client.get("/api/share/t1b/preview")
        self.assertIn(resp.status_code, (401, 403))

    # --- Erfolgspfad (Owner-Isolation gezielt durchbrochen) ---------------
    def test_fremder_angemeldeter_darf_ueber_link_herunterladen(self):
        self._link(token="t2")
        self.client.force_authenticate(self.viewer)
        resp = self.client.get("/api/share/t2/download")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            b"".join(resp.streaming_content), b"%PDF-1.4 share test"
        )

    def test_preview_liefert_inline(self):
        self._link(token="t3")
        self.client.force_authenticate(self.viewer)
        resp = self.client.get("/api/share/t3/preview")
        self.assertEqual(resp.status_code, 200)

    # --- 410 Gone (keine Enumeration) -------------------------------------
    def test_unbekanntes_token_ist_410(self):
        self.client.force_authenticate(self.viewer)
        resp = self.client.get("/api/share/gibtsnicht/download")
        self.assertEqual(resp.status_code, 410)

    def test_widerrufener_link_ist_410(self):
        self._link(token="t4", revoked=True)
        self.client.force_authenticate(self.viewer)
        resp = self.client.get("/api/share/t4/download")
        self.assertEqual(resp.status_code, 410)

    def test_abgelaufener_link_ist_410(self):
        self._link(token="t5", expired=True)
        self.client.force_authenticate(self.viewer)
        resp = self.client.get("/api/share/t5/download")
        self.assertEqual(resp.status_code, 410)

    # --- Audit ------------------------------------------------------------
    def test_zugriff_wird_auditiert(self):
        self._link(token="t6")
        self.client.force_authenticate(self.viewer)
        before = AuditLogEntry.objects.filter(action="share_download").count()
        self.client.get("/api/share/t6/download")
        entries = AuditLogEntry.objects.filter(action="share_download")
        self.assertEqual(entries.count(), before + 1)
        entry = entries.latest("timestamp")
        self.assertEqual(entry.object_id, str(self.doc.id))
        self.assertEqual(entry.actor, self.viewer)

    def test_kein_audit_bei_ungueltigem_token(self):
        self.client.force_authenticate(self.viewer)
        before = AuditLogEntry.objects.count()
        self.client.get("/api/share/gibtsnicht/preview")
        self.assertEqual(AuditLogEntry.objects.count(), before)

    # --- Isolation bleibt sonst intakt ------------------------------------
    def test_link_durchbricht_isolation_nur_fuer_das_eine_dokument(self):
        other = Document.objects.create(title="Anderes", owner=self.owner)
        self._link(token="t7")
        self.client.force_authenticate(self.viewer)
        # Der Link liefert NUR self.doc; ein Direktzugriff auf `other` bleibt 404.
        resp_other = self.client.get(f"/api/documents/{other.id}/")
        self.assertEqual(resp_other.status_code, 404)


class MailCryptoTests(TestCase):
    """Verschlüsselung der DB-Geheimnisse (STOAA-212)."""

    def test_roundtrip(self):
        from .crypto import decrypt_secret, encrypt_secret, is_encrypted

        token = encrypt_secret("geheim123")
        self.assertNotEqual(token, "geheim123")  # nicht im Klartext
        self.assertTrue(is_encrypted(token))
        self.assertEqual(decrypt_secret(token), "geheim123")

    def test_empty(self):
        from .crypto import decrypt_secret, encrypt_secret, is_encrypted

        self.assertEqual(encrypt_secret(""), "")
        self.assertEqual(decrypt_secret(""), "")
        self.assertFalse(is_encrypted(""))

    def test_legacy_plaintext_passthrough(self):
        # Alt-Datenbestand (Klartext vor STOAA-212) bleibt lesbar.
        from .crypto import decrypt_secret, is_encrypted

        self.assertFalse(is_encrypted("altes-klartext-pw"))
        self.assertEqual(decrypt_secret("altes-klartext-pw"), "altes-klartext-pw")

    def test_model_encrypts_on_save(self):
        acc = MailAccount.objects.create(
            name="Rechnungen", host="imap.example.org", username="u", password="s3cret"
        )
        acc.refresh_from_db()
        self.assertNotIn("s3cret", acc.password)  # DB-Feld ist Chiffretext
        self.assertEqual(acc.resolve_password(), "s3cret")  # entschlüsselt korrekt

    def test_save_is_idempotent(self):
        acc = MailAccount.objects.create(
            name="A", host="h", username="u", password="pw"
        )
        first = MailAccount.objects.get(pk=acc.pk).password
        acc.name = "B"
        acc.save()  # zweites Save darf nicht doppelt verschlüsseln
        acc.refresh_from_db()
        self.assertEqual(acc.password, first)
        self.assertEqual(acc.resolve_password(), "pw")


class MailAccountApiTests(APITestCase):
    """CRUD + test-connection der Mailkonto-Verwaltung (STOAA-212)."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="mailadmin", password="pw", role="admin"
        )
        cls.user = User.objects.create_user(
            username="normal", password="pw", role="user"
        )

    # --- Rechte -----------------------------------------------------------
    def test_non_admin_forbidden(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/mail-accounts/").status_code, 403)

    def test_anonymous_unauthorized(self):
        self.assertIn(self.client.get("/api/mail-accounts/").status_code, (401, 403))

    def test_admin_can_list(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/mail-accounts/").status_code, 200)

    # --- CRUD -------------------------------------------------------------
    def test_create_hides_password_and_encrypts(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/mail-accounts/",
            {
                "name": "Rechnungen",
                "host": "imap.example.org",
                "username": "rechnung@example.org",
                "password": "supergeheim",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        # Passwort niemals in der Response
        self.assertNotIn("password", resp.data)
        self.assertNotIn("supergeheim", str(resp.data))
        self.assertTrue(resp.data["has_password"])
        acc = MailAccount.objects.get(pk=resp.data["id"])
        self.assertNotIn("supergeheim", acc.password)  # verschlüsselt in DB
        self.assertEqual(acc.resolve_password(), "supergeheim")
        # Audit-Eintrag
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="mailaccount_create", object_id=str(acc.id)
            ).exists()
        )

    def test_patch_empty_password_keeps_existing(self):
        self.client.force_authenticate(self.admin)
        acc = MailAccount.objects.create(
            name="A", host="h", username="u", password="orig-pw"
        )
        resp = self.client.patch(
            f"/api/mail-accounts/{acc.id}/",
            {"name": "A-neu", "password": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        acc.refresh_from_db()
        self.assertEqual(acc.name, "A-neu")
        self.assertEqual(acc.resolve_password(), "orig-pw")  # unverändert

    def test_patch_new_password_replaces(self):
        self.client.force_authenticate(self.admin)
        acc = MailAccount.objects.create(
            name="A", host="h", username="u", password="orig-pw"
        )
        resp = self.client.patch(
            f"/api/mail-accounts/{acc.id}/",
            {"password": "neues-pw"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        acc.refresh_from_db()
        self.assertEqual(acc.resolve_password(), "neues-pw")

    def test_delete_audited(self):
        self.client.force_authenticate(self.admin)
        acc = MailAccount.objects.create(name="A", host="h", username="u")
        resp = self.client.delete(f"/api/mail-accounts/{acc.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(MailAccount.objects.filter(pk=acc.id).exists())
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="mailaccount_delete", object_id=str(acc.id)
            ).exists()
        )

    # --- Verbindungstest --------------------------------------------------
    def test_test_connection_missing_fields(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/mail-accounts/test-connection/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_test_connection_success(self):
        from unittest import mock

        self.client.force_authenticate(self.admin)
        with mock.patch("documents.mail.connect") as m:
            m.return_value = mock.Mock()
            resp = self.client.post(
                "/api/mail-accounts/test-connection/",
                {"host": "imap.example.org", "username": "u", "password": "p"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["ok"])

    def test_test_connection_failure(self):
        from unittest import mock

        self.client.force_authenticate(self.admin)
        with mock.patch("documents.mail.connect", side_effect=OSError("connect refused")):
            resp = self.client.post(
                "/api/mail-accounts/test-connection/",
                {"host": "imap.example.org", "username": "u", "password": "p"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["ok"])
        self.assertIn("connect refused", resp.data["message"])

    def test_test_connection_by_id_uses_stored_password(self):
        from unittest import mock

        self.client.force_authenticate(self.admin)
        acc = MailAccount.objects.create(
            name="A", host="h", username="u", password="stored-pw"
        )
        captured = {}

        def fake_connect(account):
            captured["pw"] = account.resolve_password()
            return mock.Mock()

        with mock.patch("documents.mail.connect", side_effect=fake_connect):
            resp = self.client.post(
                "/api/mail-accounts/test-connection/", {"id": acc.id}, format="json"
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["ok"])
        self.assertEqual(captured["pw"], "stored-pw")

    def test_test_connection_by_id_persists_success_status(self):
        """Erfolgreicher Test eines gespeicherten Kontos aktualisiert
        ``last_checked_at`` und löscht ``last_error`` (STOAA-172-Spec)."""
        from unittest import mock

        self.client.force_authenticate(self.admin)
        acc = MailAccount.objects.create(
            name="A", host="h", username="u", last_error="alter Fehler"
        )
        with mock.patch("documents.mail.connect", return_value=mock.Mock()):
            resp = self.client.post(
                "/api/mail-accounts/test-connection/", {"id": acc.id}, format="json"
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["ok"])
        acc.refresh_from_db()
        self.assertIsNotNone(acc.last_checked_at)
        self.assertEqual(acc.last_error, "")
        # In der API-Response sichtbar (read-only Statusfelder)
        detail = self.client.get(f"/api/mail-accounts/{acc.id}/")
        self.assertIsNotNone(detail.data["last_checked_at"])
        self.assertEqual(detail.data["last_error"], "")
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="mailaccount_test_connection", object_id=str(acc.id)
            ).exists()
        )

    def test_test_connection_by_id_persists_error(self):
        """Fehlgeschlagener Test schreibt ``last_error`` und setzt ``last_checked_at``."""
        from unittest import mock

        self.client.force_authenticate(self.admin)
        acc = MailAccount.objects.create(name="A", host="h", username="u")
        with mock.patch(
            "documents.mail.connect", side_effect=OSError("connect refused")
        ):
            resp = self.client.post(
                "/api/mail-accounts/test-connection/", {"id": acc.id}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["ok"])
        acc.refresh_from_db()
        self.assertIsNotNone(acc.last_checked_at)
        self.assertIn("connect refused", acc.last_error)

    def test_test_connection_detail_route_persists(self):
        """Spec-Route ``/{pk}/test-connection/`` testet + persistiert das Konto."""
        from unittest import mock

        self.client.force_authenticate(self.admin)
        acc = MailAccount.objects.create(name="A", host="h", username="u")
        with mock.patch("documents.mail.connect", return_value=mock.Mock()):
            resp = self.client.post(
                f"/api/mail-accounts/{acc.id}/test-connection/", {}, format="json"
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["ok"])
        acc.refresh_from_db()
        self.assertIsNotNone(acc.last_checked_at)
        self.assertEqual(acc.last_error, "")

    def test_test_connection_transient_does_not_persist(self):
        """Test mit rohen Zugangsdaten (Anlege-Formular) bleibt zustandslos:
        legt kein Konto an und berührt keinen Datensatz."""
        from unittest import mock

        self.client.force_authenticate(self.admin)
        with mock.patch("documents.mail.connect", return_value=mock.Mock()):
            resp = self.client.post(
                "/api/mail-accounts/test-connection/",
                {"host": "imap.example.org", "username": "u", "password": "p"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["ok"])
        self.assertEqual(MailAccount.objects.count(), 0)

    def test_non_admin_cannot_test_connection(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            "/api/mail-accounts/test-connection/",
            {"host": "h", "username": "u"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)


class BulkClassifyEndpointTests(APITestCase):
    """Bulk-Klassifizierung POST /api/documents/bulk-classify/ (STOAA-208).

    Belegt: synchrone Verarbeitung kleiner Batches mit updated/unchanged/errors,
    Celery-Dispatch großer Batches, can_write-Gate, Owner-Isolation (fremde IDs
    als Teilfehler, kein Leak) und Eingabevalidierung.
    """

    URL = "/api/documents/bulk-classify/"

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="u208", password="pw", role="user")
        cls.other = User.objects.create_user(username="o208", password="pw", role="user")
        cls.guest = User.objects.create_user(username="g208", password="pw", role="guest")
        # Regel greift auf Dokumente, deren Titel „rechnung" enthält.
        ClassificationRule.objects.create(
            name="Text-Rechnung",
            match={"text_contains": ["rechnung"]},
            then={"document_type": "Rechnung"},
        )

    def _doc(self, title, owner=None):
        return Document.objects.create(title=title, owner=owner or self.user)

    def test_sync_klein_batch_updated_unchanged_zaehlung(self):
        treffer = self._doc("Monatsrechnung Strom")  # Regel greift → updated
        kein_treffer = self._doc("Urlaubsfoto")       # keine Regel → unchanged
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            self.URL, {"ids": [treffer.id, kein_treffer.id]}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["updated"], 1)
        self.assertEqual(resp.data["unchanged"], 1)
        self.assertEqual(resp.data["errors"], [])
        treffer.refresh_from_db()
        self.assertEqual(treffer.document_type.name, "Rechnung")
        # Audit-Eintrag der Massenaktion.
        self.assertTrue(
            AuditLogEntry.objects.filter(action="bulk_classify").exists()
        )

    def test_grosser_batch_spawnt_celery_task(self):
        from unittest import mock

        docs = [self._doc(f"Rechnung {i}") for i in range(11)]  # > Limit (10)
        self.client.force_authenticate(self.user)

        class _Result:
            id = "task-abc-123"

        with mock.patch(
            "documents.views.bulk_classify_documents.delay", return_value=_Result()
        ) as delay:
            resp = self.client.post(
                self.URL, {"ids": [d.id for d in docs]}, format="json"
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"task_id": "task-abc-123", "status": "processing"})
        delay.assert_called_once()
        args, kwargs = delay.call_args
        self.assertEqual(sorted(args[0]), sorted(d.id for d in docs))
        self.assertEqual(kwargs["actor_id"], self.user.id)

    def test_rechte_check_403_fuer_gast(self):
        doc = self._doc("Rechnung", owner=self.guest)
        self.client.force_authenticate(self.guest)
        resp = self.client.post(self.URL, {"ids": [doc.id]}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_fremde_ids_als_errors_kein_leak(self):
        mine = self._doc("Meine Rechnung")
        fremd = self._doc("Fremde Rechnung", owner=self.other)
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            self.URL, {"ids": [mine.id, fremd.id]}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        # Nur das eigene Dokument wurde verarbeitet.
        self.assertEqual(resp.data["updated"] + resp.data["unchanged"], 1)
        fehler_ids = [e["id"] for e in resp.data["errors"]]
        self.assertIn(fremd.id, fehler_ids)
        fremd.refresh_from_db()
        self.assertIsNone(fremd.document_type)  # unangetastet

    def test_leere_ids_400(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.URL, {"ids": []}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_ungueltige_id_400(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.URL, {"ids": ["abc"]}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_celery_task_klassifiziert_und_auditiert(self):
        # Task-Funktion direkt aufrufen (kein Broker nötig).
        from .tasks import bulk_classify_documents

        treffer = self._doc("Stromrechnung Januar")
        neutral = self._doc("Notiz")

        result = bulk_classify_documents(
            [treffer.id, neutral.id], actor_id=self.user.id
        )

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(result["errors"], [])
        treffer.refresh_from_db()
        self.assertEqual(treffer.document_type.name, "Rechnung")
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="bulk_classify", actor=self.user
            ).exists()
        )


class DocumentProcessingStateMachineTests(TestCase):
    """Regressionstests für die fachliche Dokumentverarbeitungs-State-Machine."""

    def _version(self, file_path: str = "/tmp/dms-test.pdf"):
        user = User.objects.create_user(username="state-user", password="pw", role="user")
        document = Document.objects.create(title="State Machine Test", owner=user)
        version = DocumentVersion.objects.create(
            document=document,
            version_no=1,
            file_path=file_path,
            created_by=user,
        )
        document.current_version = version
        document.save(update_fields=["current_version"])
        return user, document, version

    def test_neue_version_startet_als_uploaded(self):
        _, _, version = self._version()

        self.assertEqual(
            version.processing_state,
            DocumentVersion.ProcessingState.UPLOADED,
        )

    def test_transitionen_muessen_strikt_vorwaerts_laufen(self):
        from django.core.exceptions import ValidationError

        user, _, version = self._version()

        with self.assertRaises(ValidationError):
            version.transition_to(
                DocumentVersion.ProcessingState.OCR_RUNNING,
                actor=user,
            )

        version.transition_to(DocumentVersion.ProcessingState.HASHED, actor=user)
        version.transition_to(DocumentVersion.ProcessingState.OCR_RUNNING, actor=user)

        with self.assertRaises(ValidationError):
            version.transition_to(DocumentVersion.ProcessingState.READY, actor=user)

    def test_process_document_version_durchlaeuft_alle_states(self):
        from pathlib import Path
        from unittest import mock

        from documents.services.ocr.types import OCRResult, OCRStatusEnum
        from .tasks import process_document_version

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cornelia.pdf"
            source.write_bytes(b"%PDF-1.4\n% test\n")
            user, document, version = self._version(str(source))

            ClassificationRule.objects.create(
                name="Cornelia",
                match={"text_contains": ["Cornelia"]},
                then={"correspondent": "Cornelia", "document_type": "Privat"},
            )

            def fake_thumbnail(version, *, max_width=700):
                thumbnail = str(Path(tmp) / "thumb.jpg")
                DocumentVersion.objects.filter(pk=version.pk).update(
                    thumbnail_path=thumbnail
                )
                version.thumbnail_path = thumbnail
                return thumbnail

            with mock.patch(
                "documents.pipeline.run_ocr",
                return_value=OCRResult(
                    text="Dokument von Cornelia",
                    pages=1,
                    status=OCRStatusEnum.SUCCESS,
                    duration_ms=12,
                    engine="test-ocr",
                ),
            ), mock.patch(
                "documents.pipeline.generate_thumbnail",
                side_effect=fake_thumbnail,
            ), mock.patch(
                "ai.tasks.suggest_document_metadata.delay"
            ) as suggest_delay:
                result = process_document_version(version.id)

        version.refresh_from_db()
        document.refresh_from_db()

        self.assertEqual(result["processing_state"], DocumentVersion.ProcessingState.READY)
        self.assertEqual(version.processing_state, DocumentVersion.ProcessingState.READY)
        self.assertTrue(version.is_immutable)
        self.assertEqual(version.ocr_text, "Dokument von Cornelia")
        self.assertEqual(version.ocr_status, OCRStatusEnum.SUCCESS.value)
        self.assertEqual(document.correspondent.name, "Cornelia")
        self.assertEqual(document.document_type.name, "Privat")
        suggest_delay.assert_called_once_with(document.id)

        state_changes = list(
            AuditLogEntry.objects.filter(
                action="processing_state",
                object_type="DocumentVersion",
                object_id=str(version.id),
            )
            .order_by("id")
            .values_list("detail", flat=True)
        )
        self.assertEqual(
            [entry["to"] for entry in state_changes],
            [
                DocumentVersion.ProcessingState.HASHED,
                DocumentVersion.ProcessingState.OCR_RUNNING,
                DocumentVersion.ProcessingState.OCR_DONE,
                DocumentVersion.ProcessingState.CLASSIFICATION_RUNNING,
                DocumentVersion.ProcessingState.CLASSIFIED,
                DocumentVersion.ProcessingState.THUMBNAIL_DONE,
                DocumentVersion.ProcessingState.SEALED,
                DocumentVersion.ProcessingState.READY,
            ],
        )

    def test_ocr_fehlerfall_setzt_status_failed_ohne_crash(self):
        """run_ocr liefert FAILED → ocr_status=failed + ocr_error gesetzt, kein Crash;
        Hash-Kette, Audit ``ocr`` und WORM-Siegel bleiben trotzdem erhalten."""
        from pathlib import Path
        from unittest import mock

        from documents.services.ocr.types import OCRResult, OCRStatusEnum
        from .tasks import process_document_version

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "kaputt.pdf"
            source.write_bytes(b"%PDF-1.4\n% broken\n")
            _, document, version = self._version(str(source))

            with mock.patch(
                "documents.pipeline.run_ocr",
                return_value=OCRResult(
                    text="",
                    pages=0,
                    status=OCRStatusEnum.FAILED,
                    error="ocrmypdf exit 2",
                    engine="ocrmypdf",
                ),
            ), mock.patch(
                "documents.pipeline.generate_thumbnail",
                return_value=None,
            ), mock.patch(
                "ai.tasks.suggest_document_metadata.delay"
            ):
                result = process_document_version(version.id)

        version.refresh_from_db()

        self.assertEqual(result["status"], "done")
        self.assertEqual(version.ocr_status, OCRStatusEnum.FAILED.value)
        self.assertEqual(version.ocr_error, "ocrmypdf exit 2")
        # Trotz OCR-Fehler: Hash-Kette gesetzt, versiegelt (WORM), Endzustand READY.
        self.assertTrue(version.sha256)
        self.assertTrue(version.is_immutable)
        self.assertIsNotNone(version.ocr_started_at)
        self.assertIsNotNone(version.ocr_finished_at)
        self.assertEqual(
            version.processing_state, DocumentVersion.ProcessingState.READY
        )
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="ocr",
                object_type="DocumentVersion",
                object_id=str(version.id),
            ).exists()
        )

    def test_ocr_status_ist_in_der_api_response_sichtbar(self):
        """Blocker 2: ocr_status ist über DocumentVersion- und Document-Serializer
        (read-only) in der API-Response sichtbar – Sinn der State-Machine."""
        from .serializers import DocumentSerializer, DocumentVersionSerializer

        _, document, version = self._version()
        DocumentVersion.objects.filter(pk=version.pk).update(
            ocr_status="failed", ocr_error="boom", ocr_engine="ocrmypdf"
        )
        version.refresh_from_db()

        vdata = DocumentVersionSerializer(version).data
        self.assertEqual(vdata["ocr_status"], "failed")
        self.assertEqual(vdata["ocr_error"], "boom")
        self.assertIn("ocr_started_at", vdata)
        self.assertIn("ocr_finished_at", vdata)

        document.refresh_from_db()
        ddata = DocumentSerializer(document).data
        self.assertEqual(ddata["ocr_status"], "failed")


class DocumentProcessingFailureRetryTests(TestCase):
    """Fehler-/Retry-Layer der Verarbeitungs-Pipeline (STOAA-228)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.user = User.objects.create_user(
            username="retry-user", password="pw", role="user"
        )

    def _version(self):
        from pathlib import Path

        source = Path(self._tmp.name) / f"doc-{DocumentVersion.objects.count()}.pdf"
        source.write_bytes(b"%PDF-1.4\n% test\n")
        document = Document.objects.create(title="Retry Test", owner=self.user)
        version = DocumentVersion.objects.create(
            document=document,
            version_no=1,
            file_path=str(source),
            created_by=self.user,
        )
        document.current_version = version
        document.save(update_fields=["current_version"])
        return document, version

    def _ok_ocr(self):
        from documents.services.ocr.types import OCRResult, OCRStatusEnum

        return OCRResult(
            text="Guter OCR-Text",
            pages=1,
            status=OCRStatusEnum.SUCCESS,
            duration_ms=5,
            engine="test-ocr",
        )

    def _fake_thumbnail(self, version, *, max_width=700):
        from pathlib import Path

        thumb = str(Path(self._tmp.name) / f"thumb-{version.id}.jpg")
        DocumentVersion.objects.filter(pk=version.pk).update(thumbnail_path=thumb)
        version.thumbnail_path = thumb
        return thumb

    def _run_to_ready(self, version):
        from unittest import mock

        with mock.patch(
            "documents.pipeline.run_ocr", return_value=self._ok_ocr()
        ), mock.patch(
            "documents.pipeline.generate_thumbnail", side_effect=self._fake_thumbnail
        ):
            return pipeline.process_version(version)

    def test_sealed_und_ready_nicht_auf_failed(self):
        from django.core.exceptions import ValidationError

        _, version = self._version()
        self._run_to_ready(version)
        version.refresh_from_db()
        self.assertEqual(
            version.processing_state, DocumentVersion.ProcessingState.READY
        )
        self.assertTrue(version.is_immutable)

        with self.assertRaises(ValidationError):
            version.mark_processing_failed(step="ocr", error="darf nicht")

    def test_begin_retry_nur_aus_failed(self):
        from django.core.exceptions import ValidationError

        _, version = self._version()
        # Frische Version ist UPLOADED, nicht FAILED → begin_retry wirft.
        with self.assertRaises(ValidationError):
            version.begin_retry()

    def test_ocr_fehler_fuehrt_zu_failed(self):
        from unittest import mock

        _, version = self._version()
        with mock.patch(
            "documents.pipeline.run_ocr", side_effect=RuntimeError("OCR kaputt")
        ):
            result = pipeline.process_version(version)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["step"], "ocr")
        self.assertEqual(
            result["processing_state"], DocumentVersion.ProcessingState.FAILED
        )

        version.refresh_from_db()
        self.assertEqual(
            version.processing_state, DocumentVersion.ProcessingState.FAILED
        )
        self.assertEqual(version.processing_failed_step, "ocr")
        self.assertIn("OCR kaputt", version.processing_error)
        self.assertIsNotNone(version.processing_failed_at)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="processing_failed", object_id=str(version.id)
            ).exists()
        )

    def test_retry_startet_neu_bis_ready(self):
        from unittest import mock

        _, version = self._version()
        with mock.patch(
            "documents.pipeline.run_ocr", side_effect=RuntimeError("boom")
        ):
            pipeline.process_version(version)
        version.refresh_from_db()
        self.assertEqual(
            version.processing_state, DocumentVersion.ProcessingState.FAILED
        )

        with mock.patch(
            "documents.pipeline.run_ocr", return_value=self._ok_ocr()
        ), mock.patch(
            "documents.pipeline.generate_thumbnail", side_effect=self._fake_thumbnail
        ):
            result = pipeline.retry_version(version, actor=self.user)

        self.assertEqual(result["status"], "done")
        version.refresh_from_db()
        self.assertEqual(
            version.processing_state, DocumentVersion.ProcessingState.READY
        )
        self.assertEqual(version.processing_attempts, 1)

        # FAILED -> RETRY_PENDING -> (HASHED) -> OCR_RUNNING deckt der Audit ab.
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="processing_retry", object_id=str(version.id)
            ).exists()
        )
        resume = AuditLogEntry.objects.filter(
            action="processing_resume", object_id=str(version.id)
        ).first()
        self.assertIsNotNone(resume)
        self.assertEqual(
            resume.detail["to"], DocumentVersion.ProcessingState.HASHED
        )
        self.assertEqual(resume.detail["step"], "ocr")

    def test_audit_enthaelt_fehler_und_retry(self):
        from unittest import mock

        _, version = self._version()
        with mock.patch(
            "documents.pipeline.run_ocr", side_effect=RuntimeError("boom")
        ):
            pipeline.process_version(version)
        with mock.patch(
            "documents.pipeline.run_ocr", return_value=self._ok_ocr()
        ), mock.patch(
            "documents.pipeline.generate_thumbnail", side_effect=self._fake_thumbnail
        ):
            pipeline.retry_version(version, actor=self.user)

        actions = set(
            AuditLogEntry.objects.filter(object_id=str(version.id)).values_list(
                "action", flat=True
            )
        )
        self.assertIn("processing_failed", actions)
        self.assertIn("processing_retry", actions)

    def test_command_ueberspringt_ready(self):
        from django.core.management import call_command

        _, version = self._version()
        self._run_to_ready(version)
        version.refresh_from_db()
        self.assertEqual(
            version.processing_state, DocumentVersion.ProcessingState.READY
        )

        before = AuditLogEntry.objects.filter(
            action="processing_state", object_id=str(version.id)
        ).count()
        call_command("retry_processing", "--version-id", str(version.id))
        after = AuditLogEntry.objects.filter(
            action="processing_state", object_id=str(version.id)
        ).count()
        self.assertEqual(before, after)

    def test_command_idempotent(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        _, version = self._version()
        with mock.patch(
            "documents.pipeline.run_ocr", side_effect=RuntimeError("boom")
        ):
            pipeline.process_version(version)

        out = StringIO()
        with mock.patch(
            "documents.pipeline.run_ocr", return_value=self._ok_ocr()
        ), mock.patch(
            "documents.pipeline.generate_thumbnail", side_effect=self._fake_thumbnail
        ):
            call_command("retry_processing", "--failed", stdout=out)
        self.assertIn("1 neu verarbeitet", out.getvalue())

        out2 = StringIO()
        call_command("retry_processing", "--failed", stdout=out2)
        self.assertIn("0 neu verarbeitet", out2.getvalue())


class ProcessingStatusAPITests(APITestCase):
    """Processing-Status-API (STOAA-248): Rollup-Feld, ?processing_state-Filter,
    dokument-scoped Retry-Endpoint."""

    @classmethod
    def setUpTestData(cls):
        PS = DocumentVersion.ProcessingState
        cls.owner = User.objects.create_user(
            username="ps_owner", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="ps_other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="ps_guest", password="pw", role="guest"
        )

        # Ein Dokument des Owners je relevantem State (current_version gesetzt).
        cls.docs = {}
        for key, state in [
            ("ready", PS.READY),
            ("failed", PS.FAILED),
            ("retry_pending", PS.RETRY_PENDING),
            ("ocr_running", PS.OCR_RUNNING),  # zählt zu Bucket "processing"
            ("classified", PS.CLASSIFIED),  # zählt zu Bucket "processing"
        ]:
            doc = Document.objects.create(title=f"Doc {key}", owner=cls.owner)
            version = DocumentVersion.objects.create(
                document=doc,
                version_no=1,
                file_path=f"/data/originals/{key}.pdf",
                sha256=key.ljust(64, "0")[:64],
                processing_state=state,
                processing_failed_step="ocr" if state == PS.FAILED else "",
            )
            doc.current_version = version
            doc.save(update_fields=["current_version"])
            cls.docs[key] = doc

    # --- Rollup-Feld ------------------------------------------------------
    def test_rollup_feld_in_liste(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/")
        self.assertEqual(resp.status_code, 200)
        by_title = {d["title"]: d for d in resp.data["results"]}
        self.assertEqual(by_title["Doc failed"]["processing_state"], "failed")
        self.assertEqual(by_title["Doc ready"]["processing_state"], "ready")

    # --- Filter-Buckets ---------------------------------------------------
    def _titles_for(self, value):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(f"/api/documents/?processing_state={value}")
        self.assertEqual(resp.status_code, 200)
        return {d["title"] for d in resp.data["results"]}

    def test_filter_failed_bucket(self):
        self.assertEqual(self._titles_for("failed"), {"Doc failed"})

    def test_filter_retry_pending_bucket(self):
        self.assertEqual(self._titles_for("retry_pending"), {"Doc retry_pending"})

    def test_filter_ready_bucket(self):
        self.assertEqual(self._titles_for("ready"), {"Doc ready"})

    def test_filter_processing_bucket_umfasst_alle_inflight_states(self):
        self.assertEqual(
            self._titles_for("processing"),
            {"Doc ocr_running", "Doc classified"},
        )

    def test_filter_exakter_state_als_fallback(self):
        self.assertEqual(self._titles_for("ocr_running"), {"Doc ocr_running"})

    def test_filter_unbekannter_wert_wird_ignoriert(self):
        # Kein 500, kein Filter → alle eigenen Dokumente.
        titles = self._titles_for("voellig_unbekannt")
        self.assertEqual(len(titles), len(self.docs))

    # --- Retry-Endpoint ---------------------------------------------------
    def _retry_url(self, doc):
        return f"/api/documents/{doc.id}/retry_processing/"

    def test_retry_failed_liefert_202_und_delayt(self):
        from unittest import mock

        self.client.force_authenticate(self.owner)
        with mock.patch(
            "documents.views.retry_document_version.delay"
        ) as delayed:
            resp = self.client.post(self._retry_url(self.docs["failed"]))
        self.assertEqual(resp.status_code, 202)
        version = self.docs["failed"].current_version
        delayed.assert_called_once_with(version.id, actor_id=self.owner.id)
        # Antwort serialisiert die (noch FAILED) aktuelle Version.
        self.assertEqual(resp.data["id"], version.id)
        self.assertEqual(resp.data["processing_state"], "failed")

    def test_retry_nicht_failed_liefert_400(self):
        from unittest import mock

        self.client.force_authenticate(self.owner)
        with mock.patch(
            "documents.views.retry_document_version.delay"
        ) as delayed:
            resp = self.client.post(self._retry_url(self.docs["ready"]))
        self.assertEqual(resp.status_code, 400)
        delayed.assert_not_called()

    def test_retry_gast_liefert_403(self):
        from unittest import mock

        # Gast besitzt selbst ein FAILED-Dokument → 403 kommt vom can_write-Guard,
        # nicht von der Owner-Isolation.
        guest_doc = Document.objects.create(title="Gast Doc", owner=self.guest)
        gv = DocumentVersion.objects.create(
            document=guest_doc,
            version_no=1,
            file_path="/data/originals/guest.pdf",
            sha256="g" * 64,
            processing_state=DocumentVersion.ProcessingState.FAILED,
            processing_failed_step="ocr",
        )
        guest_doc.current_version = gv
        guest_doc.save(update_fields=["current_version"])

        self.client.force_authenticate(self.guest)
        with mock.patch(
            "documents.views.retry_document_version.delay"
        ) as delayed:
            resp = self.client.post(self._retry_url(guest_doc))
        self.assertEqual(resp.status_code, 403)
        delayed.assert_not_called()

    def test_retry_fremdes_dokument_liefert_404(self):
        from unittest import mock

        self.client.force_authenticate(self.other)
        with mock.patch(
            "documents.views.retry_document_version.delay"
        ) as delayed:
            resp = self.client.post(self._retry_url(self.docs["failed"]))
        self.assertEqual(resp.status_code, 404)
        delayed.assert_not_called()


class DocumentSearchTests(APITestCase):
    """Gewichtete PostgreSQL-Volltextsuche im DocumentViewSet (STOAA-256).

    Deckt AK1–AK6 ab: OCR-Treffer, Titel/Korrespondent, Ranking (Titel vor OCR),
    Owner-Isolation inkl. Admin, Kurz-/Leer-Query-Fallback sowie
    Dokumenttyp/Tags/Mail-Betreff/-Absender je als alleiniges Trefferfeld.
    Benötigt PostgreSQL (SearchVector ``config='german'``) – läuft in CI.
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="such-owner", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="such-other", password="pw", role="user"
        )
        cls.admin = User.objects.create_user(
            username="such-admin", password="pw", role="admin"
        )

    @staticmethod
    def _doc(
        owner,
        title="",
        ocr_text="",
        correspondent=None,
        document_type=None,
        tags=None,
        mail_subject="",
        mail_sender="",
    ):
        """Legt ein Dokument mit Version (für OCR-Text) und Relationen an."""
        doc = Document.objects.create(
            owner=owner,
            title=title,
            correspondent=(
                Correspondent.objects.get_or_create(name=correspondent)[0]
                if correspondent
                else None
            ),
            document_type=(
                DocumentType.objects.get_or_create(name=document_type)[0]
                if document_type
                else None
            ),
            mail_subject=mail_subject,
            mail_sender=mail_sender,
        )
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/data/originals/{doc.id}.pdf",
            sha256=f"{doc.id:064d}",
            ocr_text=ocr_text,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        for name in tags or []:
            doc.tags.add(Tag.objects.get_or_create(name=name)[0])
        return doc

    def _ids(self, resp):
        return [r["id"] for r in resp.data["results"]]

    # --- AK1: Treffer allein über den OCR-Text --------------------------
    def test_ak1_ocr_only(self):
        doc = self._doc(
            self.owner,
            title="Belegscan",
            ocr_text="Sehr geehrte Frau Cornelia Muster, ...",
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=Cornelia")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._ids(resp), [doc.id])

    # --- AK2: Treffer über Titel bzw. Korrespondentenname ---------------
    def test_ak2_title_match(self):
        doc = self._doc(self.owner, title="Grundsteuerbescheid 2024")
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=Grundsteuerbescheid")
        self.assertEqual(self._ids(resp), [doc.id])

    def test_ak2_correspondent_match(self):
        doc = self._doc(self.owner, title="Rechnung", correspondent="Wienstrom")
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=Wienstrom")
        self.assertEqual(self._ids(resp), [doc.id])

    # --- AK3: Ranking – Titeltreffer vor reinem OCR-Treffer -------------
    def test_ak3_title_ranks_before_ocr(self):
        # ocr_doc zuerst angelegt → hätte bei Gleichstand via -added_at den
        # Vortritt; korrektes Ranking muss title_doc dennoch vorne einordnen.
        ocr_doc = self._doc(
            self.owner, title="Anhang", ocr_text="Zwischenbericht Quartalsbericht Q3"
        )
        title_doc = self._doc(self.owner, title="Quartalsbericht Q3 2024")
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=Quartalsbericht")
        ids = self._ids(resp)
        self.assertEqual(set(ids), {title_doc.id, ocr_doc.id})
        self.assertLess(
            ids.index(title_doc.id),
            ids.index(ocr_doc.id),
            "Titeltreffer (Gewicht A) muss vor OCR-Treffer (Gewicht D) ranken.",
        )

    # --- AK4: Owner-Isolation inkl. Admin-Ausnahme ----------------------
    def test_ak4_owner_isolation(self):
        doc = self._doc(self.owner, title="Privatvertrag Sonderbegriff")
        # Fremder Nutzer sieht den Treffer nicht.
        self.client.force_authenticate(self.other)
        resp = self.client.get("/api/documents/?q=Sonderbegriff")
        self.assertEqual(self._ids(resp), [])
        # Admin sieht alles.
        self.client.force_authenticate(self.admin)
        resp = self.client.get("/api/documents/?q=Sonderbegriff")
        self.assertEqual(self._ids(resp), [doc.id])

    # --- AK5: Fallback für leere / kurze Query --------------------------
    def test_ak5_empty_query_returns_full_list(self):
        d1 = self._doc(self.owner, title="Alpha")
        d2 = self._doc(self.owner, title="Beta")
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(self._ids(resp)), {d1.id, d2.id})

    def test_ak5_short_query_icontains(self):
        hit = self._doc(self.owner, title="XZ-Sonderfall")
        self._doc(self.owner, title="Unbeteiligt")
        self.client.force_authenticate(self.owner)
        # 2-Zeichen-Query → icontains-Fallback (FTS-Lexeme greifen hier nicht).
        resp = self.client.get("/api/documents/?q=XZ")
        self.assertEqual(self._ids(resp), [hit.id])

    # --- AK6: weitere Felder je als alleiniges Trefferfeld --------------
    def test_ak6_document_type_match(self):
        doc = self._doc(self.owner, title="Scan", document_type="Versicherungspolizze")
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=Versicherungspolizze")
        self.assertEqual(self._ids(resp), [doc.id])

    def test_ak6_tag_match(self):
        doc = self._doc(self.owner, title="Scan", tags=["Nebenkostenabrechnung"])
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=Nebenkostenabrechnung")
        self.assertEqual(self._ids(resp), [doc.id])

    def test_ak6_mail_subject_match(self):
        doc = self._doc(self.owner, title="Scan", mail_subject="Zählerstand Erdgas")
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=Zählerstand")
        self.assertEqual(self._ids(resp), [doc.id])

    def test_ak6_mail_sender_match(self):
        # mail.py speichert den From-Header realistisch als "Anzeigename <adresse>"
        # (mail.py:201). PostgreSQL-FTS tokenisiert eine reine E-Mail-Adresse als
        # EIN atomares Token, d. h. Teilstrings der Domain sind nicht als eigene
        # Lexeme suchbar. Realistische, FTS-taugliche Suche geht über den
        # Anzeigenamen des Absenders (siehe Known-Limitation-Kommentar in views.py).
        doc = self._doc(
            self.owner,
            title="Scan",
            mail_sender="Energieanbieter Buchhaltung <buchhaltung@energieanbieter.example>",
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?q=Energieanbieter")
        self.assertEqual(self._ids(resp), [doc.id])


# ---------------------------------------------------------------------------
# Workflow-Engine-Tests (STOAA-263)
# ---------------------------------------------------------------------------
class WorkflowEngineTests(TestCase):
    """Unit-Tests für run_workflows, Trigger-Matching und Aktionsanwendung."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="wf-user", password="pw", role="user")
        cls.corr = Correspondent.objects.create(name="Stadtwerke")
        cls.dt = DocumentType.objects.create(name="Rechnung")
        cls.sp = StoragePath.objects.create(name="Archiv", path_template="archiv/{titel}")
        cls.tag_finanzen = Tag.objects.create(name="Finanzen")
        cls.tag_privat = Tag.objects.create(name="Privat")

    def _make_doc(self, title="Testdokument", ingest_source="upload"):
        doc = Document.objects.create(title=title, owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/tmp/x.pdf",
            created_by=self.user, ingest_source=ingest_source,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc, version

    def _make_workflow(self, *, name="WF", order=10, enabled=True,
                       trigger_type="document_added", sources="",
                       filter_text_contains="", filter_text_regex="",
                       filter_correspondent=None, filter_document_type=None):
        from .models import Workflow, WorkflowAction, WorkflowTrigger
        wf = Workflow.objects.create(name=name, order=order, enabled=enabled)
        trig = WorkflowTrigger.objects.create(
            workflow=wf, trigger_type=trigger_type, sources=sources,
            filter_text_contains=filter_text_contains,
            filter_text_regex=filter_text_regex,
            filter_correspondent=filter_correspondent,
            filter_document_type=filter_document_type,
        )
        return wf, trig

    # ------------------------------------------------------------------
    # Trigger-Matching: source-Filter
    # ------------------------------------------------------------------
    def test_source_filter_trifft_passende_quelle(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction, WorkflowTrigger
        wf, _ = self._make_workflow(sources="consume")
        WorkflowAction.objects.create(
            workflow=wf, order=10, action_type="assign",
            assign_document_type=self.dt,
        )
        doc, _ = self._make_doc()
        result = run_workflows(doc, trigger_type="document_added", source="consume", text="")
        self.assertIn("WF", result["workflows"])

    def test_source_filter_ignoriert_falsche_quelle(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction, WorkflowTrigger
        wf, _ = self._make_workflow(name="WF2", sources="mail")
        WorkflowAction.objects.create(
            workflow=wf, order=10, action_type="assign",
            assign_document_type=self.dt,
        )
        doc, _ = self._make_doc()
        result = run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertNotIn("WF2", result["workflows"])

    def test_leere_source_trifft_alle(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_ALL", sources="")
        WorkflowAction.objects.create(
            workflow=wf, order=10, action_type="assign",
            assign_correspondent=self.corr,
        )
        doc, _ = self._make_doc()
        result = run_workflows(doc, trigger_type="document_added", source="api", text="")
        self.assertIn("WF_ALL", result["workflows"])

    # ------------------------------------------------------------------
    # Trigger-Matching: text_contains / text_regex
    # ------------------------------------------------------------------
    def test_text_contains_trifft(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_TEXT", filter_text_contains="rechnung")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        result = run_workflows(doc, trigger_type="document_added", source="upload",
                               text="Das ist eine Rechnung von Stadtwerke")
        self.assertIn("WF_TEXT", result["workflows"])

    def test_text_contains_verfehlt(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_MISS", filter_text_contains="xyz123")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        result = run_workflows(doc, trigger_type="document_added", source="upload", text="normal text")
        self.assertNotIn("WF_MISS", result["workflows"])

    def test_text_regex_trifft(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_REGEX", filter_text_regex=r"SR-\d+")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        result = run_workflows(doc, trigger_type="document_added", source="upload", text="Beleg SR-4711")
        self.assertIn("WF_REGEX", result["workflows"])

    # ------------------------------------------------------------------
    # Trigger-Matching: Korrespondent
    # ------------------------------------------------------------------
    def test_filter_correspondent_trifft(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_CORR", filter_correspondent=self.corr)
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        doc.correspondent = self.corr
        doc.save(update_fields=["correspondent"])
        result = run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertIn("WF_CORR", result["workflows"])

    def test_filter_correspondent_verfehlt(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_CORR2", filter_correspondent=self.corr)
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        result = run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertNotIn("WF_CORR2", result["workflows"])

    # ------------------------------------------------------------------
    # Trigger-Matching: Tags
    # ------------------------------------------------------------------
    def test_filter_has_tags_trifft(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction, WorkflowTrigger
        wf, trig = self._make_workflow(name="WF_TAGS")
        trig.filter_has_tags.add(self.tag_finanzen)
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_correspondent=self.corr)
        doc, _ = self._make_doc()
        doc.tags.add(self.tag_finanzen)
        result = run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertIn("WF_TAGS", result["workflows"])

    def test_filter_has_not_tags_sperrt(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction, WorkflowTrigger
        wf, trig = self._make_workflow(name="WF_NOTTAGS")
        trig.filter_has_not_tags.add(self.tag_privat)
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_correspondent=self.corr)
        doc, _ = self._make_doc()
        doc.tags.add(self.tag_privat)
        result = run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertNotIn("WF_NOTTAGS", result["workflows"])

    # ------------------------------------------------------------------
    # Aktions-Anwendung: assign
    # ------------------------------------------------------------------
    def test_action_assign_document_type(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_DT")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        doc.refresh_from_db()
        self.assertEqual(doc.document_type, self.dt)

    def test_action_assign_correspondent(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_CORR_ASSIGN")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_correspondent=self.corr)
        doc, _ = self._make_doc()
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        doc.refresh_from_db()
        self.assertEqual(doc.correspondent, self.corr)

    def test_action_assign_storage_path(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_SP")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_storage_path=self.sp)
        doc, _ = self._make_doc()
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        doc.refresh_from_db()
        self.assertEqual(doc.storage_path, self.sp)

    def test_action_assign_tags(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_TAG_ADD")
        action = WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign")
        action.assign_tags.add(self.tag_finanzen)
        doc, _ = self._make_doc()
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertIn(self.tag_finanzen, doc.tags.all())

    def test_action_assign_owner(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_OWNER")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_owner=self.user)
        doc, _ = self._make_doc()
        doc.owner = None
        doc.save(update_fields=["owner"])
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        doc.refresh_from_db()
        self.assertEqual(doc.owner, self.user)

    def test_action_assign_title_template(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_TITLE")
        doc, _ = self._make_doc()
        doc.correspondent = self.corr
        doc.save(update_fields=["correspondent"])
        WorkflowAction.objects.create(
            workflow=wf, order=10, action_type="assign",
            assign_title="{correspondent} – Beleg",
        )
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        doc.refresh_from_db()
        self.assertEqual(doc.title, "Stadtwerke – Beleg")

    def test_action_assign_custom_field(self):
        from .workflows import run_workflows
        from .models import CustomFieldValue, Workflow, WorkflowAction
        field = CustomField.objects.create(name="Betrag", data_type="text")
        wf, _ = self._make_workflow(name="WF_CF")
        WorkflowAction.objects.create(
            workflow=wf, order=10, action_type="assign",
            assign_custom_fields={str(field.pk): "99.00"},
        )
        doc, _ = self._make_doc()
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        val = CustomFieldValue.objects.get(document=doc, field=field)
        self.assertEqual(val.value, "99.00")

    # ------------------------------------------------------------------
    # Aktions-Anwendung: remove
    # ------------------------------------------------------------------
    def test_action_remove_tags(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_REMOVE")
        action = WorkflowAction.objects.create(workflow=wf, order=10, action_type="remove")
        action.remove_tags.add(self.tag_privat)
        doc, _ = self._make_doc()
        doc.tags.add(self.tag_privat)
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertNotIn(self.tag_privat, doc.tags.all())

    # ------------------------------------------------------------------
    # order-Reihenfolge + disabled
    # ------------------------------------------------------------------
    def test_order_reihenfolge_bestimmt_ausfuehrungsfolge(self):
        """Zweiter Workflow setzt Feld, weil erster es bereits belegt hat → nur erster wirkt."""
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        corr2 = Correspondent.objects.create(name="Zweiter")
        wf_first, _ = self._make_workflow(name="FIRST", order=1)
        WorkflowAction.objects.create(workflow=wf_first, order=10, action_type="assign",
                                       assign_correspondent=self.corr)
        wf_second, _ = self._make_workflow(name="SECOND", order=2)
        WorkflowAction.objects.create(workflow=wf_second, order=10, action_type="assign",
                                       assign_correspondent=corr2)
        doc, _ = self._make_doc()
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        doc.refresh_from_db()
        # assign-Logik: Einzelwert nur wenn noch leer → erster Workflow setzt, zweiter überspringt
        self.assertEqual(doc.correspondent, self.corr)

    def test_disabled_workflow_wird_uebersprungen(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_DISABLED", enabled=False)
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        result = run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertNotIn("WF_DISABLED", result["workflows"])
        doc.refresh_from_db()
        self.assertIsNone(doc.document_type)

    # ------------------------------------------------------------------
    # Trigger-Typ: document_added vs document_updated
    # ------------------------------------------------------------------
    def test_document_updated_trigger_nur_bei_updated(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_UPDATED", trigger_type="document_updated")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        result_added = run_workflows(doc, trigger_type="document_added", source="upload", text="")
        self.assertNotIn("WF_UPDATED", result_added["workflows"])
        result_updated = run_workflows(doc, trigger_type="document_updated", source="api", text="")
        self.assertIn("WF_UPDATED", result_updated["workflows"])

    # ------------------------------------------------------------------
    # Audit-Log
    # ------------------------------------------------------------------
    def test_workflow_erstellt_audit_eintrag(self):
        from .workflows import run_workflows
        from .models import Workflow, WorkflowAction
        wf, _ = self._make_workflow(name="WF_AUDIT")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign",
                                       assign_document_type=self.dt)
        doc, _ = self._make_doc()
        run_workflows(doc, trigger_type="document_added", source="upload", text="")
        entry = AuditLogEntry.objects.filter(
            action="workflow", object_type="Document", object_id=str(doc.id)
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.detail["workflow"], "WF_AUDIT")

    # ------------------------------------------------------------------
    # ClassificationRule bleibt unverändert
    # ------------------------------------------------------------------
    def test_classification_rule_weiterhin_funktionsfaehig(self):
        """apply_rules darf durch Workflow-Engine nicht gebrochen werden."""
        ClassificationRule.objects.create(
            name="Rechnungsregel",
            priority=10,
            match={"text_contains": "Rechnung"},
            then={"document_type": "Rechnung"},
        )
        doc, _ = self._make_doc()
        result = apply_rules(doc)
        # Ohne OCR-Text schlägt die Regel nicht an – das ist korrekt
        self.assertIsInstance(result, dict)
        self.assertIn("rules", result)


# ---------------------------------------------------------------------------
# Workflow-REST-API-Tests (STOAA-263 PR2)
# ---------------------------------------------------------------------------
class WorkflowAPITests(APITestCase):
    """CRUD über /api/workflows/ inkl. verschachteltem Trigger + Aktionen."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="wf_api_user", password="pw", role="user")
        cls.guest = User.objects.create_user(username="wf_api_guest", password="pw", role="guest")
        cls.corr = Correspondent.objects.create(name="Stadtwerke")
        cls.dt = DocumentType.objects.create(name="Rechnung")
        cls.tag = Tag.objects.create(name="Finanzen")

    def test_list_erfordert_login(self):
        resp = self.client.get("/api/workflows/")
        self.assertIn(resp.status_code, (401, 403))

    def test_create_workflow_mit_trigger_und_aktionen(self):
        self.client.force_authenticate(self.user)
        payload = {
            "name": "Rechnungs-Workflow",
            "order": 5,
            "enabled": True,
            "trigger": {
                "trigger_type": "document_added",
                "sources": "upload,mail",
                "filter_correspondent": self.corr.id,
                "filter_has_tags": [self.tag.id],
                "filter_text_contains": "rechnung",
            },
            "actions": [
                {
                    "order": 10,
                    "action_type": "assign",
                    "assign_document_type": self.dt.id,
                    "assign_tags": [self.tag.id],
                    "assign_title": "{correspondent} – Rechnung",
                },
                {
                    "order": 20,
                    "action_type": "remove",
                    "remove_tags": [self.tag.id],
                },
            ],
        }
        resp = self.client.post("/api/workflows/", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        from .models import Workflow, WorkflowAction, WorkflowTrigger
        wf = Workflow.objects.get(name="Rechnungs-Workflow")
        self.assertEqual(wf.order, 5)
        self.assertEqual(wf.trigger.trigger_type, "document_added")
        self.assertEqual(wf.trigger.sources, "upload,mail")
        self.assertEqual(wf.trigger.filter_correspondent, self.corr)
        self.assertIn(self.tag, wf.trigger.filter_has_tags.all())
        self.assertEqual(wf.actions.count(), 2)
        first = wf.actions.order_by("order").first()
        self.assertEqual(first.assign_document_type, self.dt)
        self.assertIn(self.tag, first.assign_tags.all())

    def test_retrieve_liefert_verschachtelte_struktur(self):
        self.client.force_authenticate(self.user)
        from .models import Workflow, WorkflowAction, WorkflowTrigger
        wf = Workflow.objects.create(name="WF-Get", order=1)
        WorkflowTrigger.objects.create(workflow=wf, trigger_type="document_added", sources="upload")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign", assign_correspondent=self.corr)
        resp = self.client.get(f"/api/workflows/{wf.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "WF-Get")
        self.assertEqual(data["trigger"]["sources"], "upload")
        self.assertEqual(len(data["actions"]), 1)
        self.assertEqual(data["actions"][0]["assign_correspondent"], self.corr.id)

    def test_update_ersetzt_aktionen(self):
        self.client.force_authenticate(self.user)
        from .models import Workflow, WorkflowAction, WorkflowTrigger
        wf = Workflow.objects.create(name="WF-Update", order=1)
        WorkflowTrigger.objects.create(workflow=wf, trigger_type="document_added")
        WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign", assign_correspondent=self.corr)
        payload = {
            "name": "WF-Update",
            "order": 2,
            "enabled": False,
            "trigger": {"trigger_type": "document_updated", "sources": "api"},
            "actions": [
                {"order": 5, "action_type": "assign", "assign_document_type": self.dt.id},
            ],
        }
        resp = self.client.put(f"/api/workflows/{wf.id}/", payload, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        wf.refresh_from_db()
        self.assertFalse(wf.enabled)
        self.assertEqual(wf.order, 2)
        self.assertEqual(wf.trigger.trigger_type, "document_updated")
        self.assertEqual(wf.actions.count(), 1)
        self.assertEqual(wf.actions.first().assign_document_type, self.dt)

    def test_delete_workflow(self):
        self.client.force_authenticate(self.user)
        from .models import Workflow, WorkflowTrigger
        wf = Workflow.objects.create(name="WF-Del", order=1)
        WorkflowTrigger.objects.create(workflow=wf, trigger_type="document_added")
        resp = self.client.delete(f"/api/workflows/{wf.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Workflow.objects.filter(id=wf.id).exists())

    def test_gast_darf_nicht_schreiben(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post("/api/workflows/", {"name": "X", "order": 1}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_create_ohne_trigger_und_aktionen_moeglich(self):
        """Minimaler Workflow (nur name/order) ist zulässig – Trigger folgt per Update."""
        self.client.force_authenticate(self.user)
        resp = self.client.post("/api/workflows/", {"name": "Leer", "order": 99}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        from .models import Workflow
        self.assertTrue(Workflow.objects.filter(name="Leer").exists())


class VersionCompareServiceTests(TestCase):
    """Unit-Tests für den isolierten Vergleichs-Service (STOAA-289).

    Der Service nimmt zwei ``DocumentVersion`` + das ``Document`` – kein Request,
    keine Permission-Ebene. Getestet werden OCR-Text-Diff, Datei-Vergleich und
    die PDF-Stufe (nur Architektur, keine Bildverarbeitung).
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="vc_owner", password="pw", role="user")
        cls.doc = Document.objects.create(title="Vergleichsdokument", owner=cls.user)

    def _version(self, version_no, **kwargs):
        defaults = dict(
            document=self.doc,
            version_no=version_no,
            file_path=f"/data/originals/v{version_no}.pdf",
            sha256=str(version_no) * 64,
            mime_type="application/pdf",
            size=1000 + version_no,
            page_count=1,
            ocr_text="",
        )
        defaults.update(kwargs)
        return DocumentVersion.objects.create(**defaults)

    def _compare(self, old, new):
        from .services import version_compare

        return version_compare.compare_versions(self.doc, old, new)

    # --- OCR-Text -------------------------------------------------------
    def test_gleicher_text_kein_change(self):
        old = self._version(1, ocr_text="Hallo Welt", sha256="a" * 64)
        new = self._version(2, ocr_text="Hallo Welt", sha256="a" * 64)
        result = self._compare(old, new)
        self.assertFalse(result.summary.text_changed)

    def test_geaenderter_text_change_mit_diff_zeilen(self):
        old = self._version(1, ocr_text="alt")
        new = self._version(2, ocr_text="neu")
        result = self._compare(old, new)
        self.assertTrue(result.summary.text_changed)
        self.assertIn("-alt", result.text_diff)
        self.assertIn("+neu", result.text_diff)
        self.assertIn("<table", result.text_diff_html)

    def test_leerer_text_kein_crash(self):
        old = self._version(1, ocr_text="")
        new = self._version(2, ocr_text="jetzt Inhalt")
        result = self._compare(old, new)
        self.assertTrue(result.summary.text_changed)
        # Beidseitig leer → kein Change, kein Crash.
        empty_old = self._version(3, ocr_text="")
        empty_new = self._version(4, ocr_text="")
        result2 = self._compare(empty_old, empty_new)
        self.assertFalse(result2.summary.text_changed)
        self.assertEqual(result2.text_diff, "")

    # --- Datei ----------------------------------------------------------
    def test_gleicher_sha_kein_binary_change(self):
        old = self._version(1, sha256="c" * 64)
        new = self._version(2, sha256="c" * 64)
        result = self._compare(old, new)
        self.assertFalse(result.summary.binary_changed)
        self.assertFalse(result.files.changed)

    def test_anderer_sha_binary_change(self):
        old = self._version(1, sha256="a" * 64)
        new = self._version(2, sha256="b" * 64)
        result = self._compare(old, new)
        self.assertTrue(result.summary.binary_changed)
        self.assertTrue(result.files.changed)
        self.assertEqual(result.files.old_sha256, "a" * 64)
        self.assertEqual(result.files.new_sha256, "b" * 64)

    def test_groesse_und_mime_gespiegelt(self):
        old = self._version(1, size=1234, mime_type="application/pdf")
        new = self._version(2, size=2345, mime_type="image/png")
        result = self._compare(old, new)
        self.assertEqual(result.files.old_size, 1234)
        self.assertEqual(result.files.new_size, 2345)
        self.assertEqual(result.files.old_mime_type, "application/pdf")
        self.assertEqual(result.files.new_mime_type, "image/png")

    # --- PDF-Stufe ------------------------------------------------------
    def test_beide_pdf_gleiche_seitenzahl(self):
        old = self._version(1, mime_type="application/pdf", page_count=3)
        new = self._version(2, mime_type="application/pdf", page_count=3)
        result = self._compare(old, new)
        self.assertTrue(result.files.both_pdf)
        self.assertFalse(result.summary.pages_changed)

    def test_beide_pdf_andere_seitenzahl(self):
        old = self._version(1, mime_type="application/pdf", page_count=3)
        new = self._version(2, mime_type="application/pdf", page_count=4)
        result = self._compare(old, new)
        self.assertTrue(result.files.both_pdf)
        self.assertTrue(result.summary.pages_changed)

    def test_nicht_pdf_kein_pages_changed(self):
        old = self._version(1, mime_type="image/png", page_count=1)
        new = self._version(2, mime_type="image/jpeg", page_count=9)
        result = self._compare(old, new)
        self.assertFalse(result.files.both_pdf)
        self.assertFalse(result.summary.pages_changed)

    # --- Summary Stufe-1-Fixwerte ---------------------------------------
    def test_metadata_flags_fix_false(self):
        old = self._version(1)
        new = self._version(2, sha256="z" * 64)
        result = self._compare(old, new).to_dict()
        self.assertFalse(result["summary"]["metadata_changed"])
        self.assertFalse(result["summary"]["tags_changed"])
        self.assertFalse(result["summary"]["custom_fields_changed"])
        self.assertFalse(result["metadata_versioning_supported"])
        self.assertEqual(result["metadata"], {})
        self.assertEqual(result["tags"], {"added": [], "removed": []})
        self.assertEqual(result["custom_fields"], {})


class VersionCompareApiTests(APITestCase):
    """API-Tests für ``GET .../versions/{from}/compare/{to}/`` (STOAA-289)."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="vc_api_owner", password="pw", role="user")
        cls.other = User.objects.create_user(username="vc_api_other", password="pw", role="user")
        cls.doc = Document.objects.create(title="API-Vergleich", owner=cls.owner)
        cls.v2 = DocumentVersion.objects.create(
            document=cls.doc, version_no=2, file_path="/d/v2.pdf",
            sha256="a" * 64, mime_type="application/pdf", size=1000,
            page_count=3, ocr_text="alt",
        )
        cls.v5 = DocumentVersion.objects.create(
            document=cls.doc, version_no=5, file_path="/d/v5.pdf",
            sha256="b" * 64, mime_type="application/pdf", size=2000,
            page_count=4, ocr_text="neu",
        )
        cls.doc.current_version = cls.v5
        cls.doc.save(update_fields=["current_version"])

    def _url(self, doc_id, frm, to):
        return f"/api/documents/{doc_id}/versions/{frm}/compare/{to}/"

    def test_erfolgreicher_vergleich_shape(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self._url(self.doc.id, 2, 5))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["document"], self.doc.id)
        self.assertEqual(data["from_version"], 2)
        self.assertEqual(data["to_version"], 5)
        self.assertTrue(data["summary"]["text_changed"])
        self.assertTrue(data["summary"]["binary_changed"])
        self.assertTrue(data["summary"]["pages_changed"])
        self.assertEqual(data["files"]["changed"], data["summary"]["binary_changed"])
        self.assertTrue(data["files"]["both_pdf"])
        self.assertEqual(data["files"]["old_page_count"], 3)
        self.assertEqual(data["files"]["new_page_count"], 4)
        self.assertFalse(data["metadata_versioning_supported"])
        # Stufe-1-Sektionen vorhanden aber leer.
        self.assertEqual(data["metadata"], {})
        self.assertEqual(data["tags"], {"added": [], "removed": []})
        self.assertEqual(data["custom_fields"], {})

    def test_beliebige_reihenfolge(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self._url(self.doc.id, 5, 2))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["from_version"], 5)
        self.assertEqual(data["to_version"], 2)

    def test_fehlende_version_404(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self._url(self.doc.id, 2, 99))
        self.assertEqual(resp.status_code, 404)

    def test_ungueltige_versionsnummer_404(self):
        # Nicht-numerisch matcht die url_path-Regex nicht → kein Route-Match → 404.
        self.client.force_authenticate(self.owner)
        resp = self.client.get(f"/api/documents/{self.doc.id}/versions/abc/compare/2/")
        self.assertEqual(resp.status_code, 404)

    def test_fremdes_dokument_404(self):
        self.client.force_authenticate(self.other)
        resp = self.client.get(self._url(self.doc.id, 2, 5))
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_abgewiesen(self):
        resp = self.client.get(self._url(self.doc.id, 2, 5))
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# Versionsvergleich Stufe 2 (STOAA-312): Metadaten-Snapshot beim Sealing,
# WORM/Siegel, Backfill und Snapshot-Diff im Compare-Endpoint.
# ===========================================================================
from django.core.management import call_command  # noqa: E402

from .services import version_compare, version_snapshot  # noqa: E402


class VersionSnapshotSealingTests(TestCase):
    """Snapshot beim Sealing, Write-once/WORM und Siegel-Bindung (STOAA-312)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="snap_owner", password="pw", role="user")
        cls.dtype = DocumentType.objects.create(name="Rechnung")
        cls.corr = Correspondent.objects.create(name="Stadtwerke")
        cls.spath = StoragePath.objects.create(name="Finanzen", path_template="{title}")
        cls.tag_a = Tag.objects.create(name="Finanzen")
        cls.tag_b = Tag.objects.create(name="Wichtig")
        cls.cfield = CustomField.objects.create(name="Betrag", data_type="currency")

    def _doc_with_metadata(self, title="Rechnung 2026"):
        doc = Document.objects.create(
            title=title,
            owner=self.user,
            document_type=self.dtype,
            correspondent=self.corr,
            storage_path=self.spath,
            status="entwurf",
        )
        doc.tags.set([self.tag_b, self.tag_a])
        CustomFieldValue.objects.create(document=doc, field=self.cfield, value="100,00")
        return doc

    def _version(self, doc, *, version_no=1, sha256="a" * 64, prev_hash=""):
        return DocumentVersion.objects.create(
            document=doc,
            version_no=version_no,
            file_path=f"/data/v{version_no}.pdf",
            sha256=sha256,
            prev_hash=prev_hash,
            mime_type="application/pdf",
            size=1000,
            ocr_text="text",
        )

    def test_snapshot_inhalt_deterministisch(self):
        doc = self._doc_with_metadata()
        version = self._version(doc)
        wrote = version_snapshot.write_snapshot_on_seal(version)
        self.assertTrue(wrote)
        version.refresh_from_db()
        snap = version.metadata_snapshot
        self.assertEqual(snap["snapshot_schema_version"], version_snapshot.SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(snap["metadata"]["title"], "Rechnung 2026")
        self.assertEqual(snap["metadata"]["document_type"], "Rechnung")
        self.assertEqual(snap["metadata"]["correspondent"], "Stadtwerke")
        self.assertEqual(snap["metadata"]["storage_path"], "Finanzen")
        self.assertEqual(snap["metadata"]["owner"], "snap_owner")
        self.assertEqual(snap["metadata"]["status"], "entwurf")
        # Tags nach id aufsteigend sortiert (id+name Objekte).
        self.assertEqual(
            snap["tags"],
            [{"id": self.tag_a.id, "name": "Finanzen"}, {"id": self.tag_b.id, "name": "Wichtig"}],
        )
        self.assertEqual(snap["custom_fields"], {"Betrag": "100,00"})
        self.assertTrue(version.snapshot_taken_at is not None)
        self.assertTrue(version.seal_hash)

    def test_sealing_hook_schreibt_snapshot(self):
        doc = self._doc_with_metadata()
        version = self._version(doc)
        # _seal_version ist der echte Sealing-Hook (setzt WORM + schreibt Snapshot).
        pipeline._seal_version(version)
        version.refresh_from_db()
        self.assertIsNotNone(version.metadata_snapshot)
        self.assertTrue(version.is_immutable)
        self.assertTrue(version.seal_hash)

    def test_snapshot_write_once_idempotent(self):
        doc = self._doc_with_metadata()
        version = self._version(doc)
        self.assertTrue(version_snapshot.write_snapshot_on_seal(version))
        first = version.metadata_snapshot
        # Metadatum am Dokument nachträglich ändern und erneut versuchen.
        doc.title = "Manipuliert"
        doc.save(update_fields=["title"])
        self.assertFalse(version_snapshot.write_snapshot_on_seal(version))
        version.refresh_from_db()
        self.assertEqual(version.metadata_snapshot["metadata"]["title"], "Rechnung 2026")
        self.assertEqual(version.metadata_snapshot, first)

    def test_seal_hash_umfasst_snapshot_manipulation_bricht_siegel(self):
        doc = self._doc_with_metadata()
        version = self._version(doc)
        version_snapshot.write_snapshot_on_seal(version)
        version.refresh_from_db()
        # Unverändert → Siegel gültig.
        self.assertTrue(version_snapshot.verify_seal(version))
        # Eingefrorenes Metadatum manipulieren → Siegel bricht.
        version.metadata_snapshot["metadata"]["title"] = "gefälscht"
        self.assertFalse(version_snapshot.verify_seal(version))

    def test_verify_seal_ohne_snapshot_ist_true(self):
        doc = self._doc_with_metadata()
        version = self._version(doc)  # kein Snapshot (Stufe-1-Bestand)
        self.assertTrue(version_snapshot.verify_seal(version))

    def test_versiegelte_version_ist_worm(self):
        from django.core.exceptions import ValidationError

        doc = self._doc_with_metadata()
        version = self._version(doc)
        pipeline._seal_version(version)
        version.refresh_from_db()
        version.metadata_snapshot = {"metadata": {"title": "hack"}}
        with self.assertRaises(ValidationError):
            version.save()


class BackfillVersionSnapshotsTests(TestCase):
    """`manage.py backfill_version_snapshots` – idempotent, nur aktuelle Version."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="bf_owner", password="pw", role="user")

    def _doc_with_two_versions(self, title):
        doc = Document.objects.create(title=title, owner=self.user)
        v1 = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/d/v1.pdf", sha256="a" * 64,
            mime_type="application/pdf", size=10, ocr_text="alt",
        )
        v2 = DocumentVersion.objects.create(
            document=doc, version_no=2, file_path="/d/v2.pdf", sha256="b" * 64,
            prev_hash="a" * 64, mime_type="application/pdf", size=20, ocr_text="neu",
        )
        doc.current_version = v2
        doc.save(update_fields=["current_version"])
        return doc, v1, v2

    def test_backfill_nur_aktuelle_version(self):
        doc, v1, v2 = self._doc_with_two_versions("Doc A")
        call_command("backfill_version_snapshots")
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertIsNone(v1.metadata_snapshot)  # ältere Version bleibt unberührt
        self.assertIsNotNone(v2.metadata_snapshot)
        self.assertTrue(v2.snapshot_taken_at is not None)
        self.assertTrue(v2.seal_hash)

    def test_backfill_idempotent(self):
        doc, v1, v2 = self._doc_with_two_versions("Doc B")
        call_command("backfill_version_snapshots")
        v2.refresh_from_db()
        first_snapshot = v2.metadata_snapshot
        first_taken_at = v2.snapshot_taken_at
        # Zweiter Lauf: kein Doppelschreiben, Snapshot unverändert.
        call_command("backfill_version_snapshots")
        v2.refresh_from_db()
        self.assertEqual(v2.metadata_snapshot, first_snapshot)
        self.assertEqual(v2.snapshot_taken_at, first_taken_at)


class VersionCompareSnapshotDiffTests(TestCase):
    """Compare-Endpoint Stufe 2: Snapshot-Diff + supported-Flag + text_diff_html."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="vc2_owner", password="pw", role="user")
        cls.doc = Document.objects.create(title="Diff-Dokument", owner=cls.user)

    def _version(self, version_no, *, snapshot=None, ocr_text="text", sha256=None):
        version = DocumentVersion.objects.create(
            document=self.doc,
            version_no=version_no,
            file_path=f"/d/v{version_no}.pdf",
            sha256=sha256 or (str(version_no) * 64),
            mime_type="application/pdf",
            size=1000,
            ocr_text=ocr_text,
        )
        if snapshot is not None:
            DocumentVersion.objects.filter(pk=version.pk).update(
                metadata_snapshot=snapshot,
                snapshot_schema_version=version_snapshot.SNAPSHOT_SCHEMA_VERSION,
            )
            version.refresh_from_db()
        return version

    @staticmethod
    def _snap(*, title, tags, custom_fields, status="entwurf"):
        return {
            "snapshot_schema_version": version_snapshot.SNAPSHOT_SCHEMA_VERSION,
            "snapshot_taken_at": None,
            "metadata": {
                "title": title,
                "document_type": None,
                "correspondent": None,
                "storage_path": None,
                "owner": "vc2_owner",
                "status": status,
                "retention_until": None,
            },
            "tags": tags,
            "custom_fields": custom_fields,
        }

    def _compare(self, old, new):
        return version_compare.compare_versions(self.doc, old, new).to_dict()

    def test_diff_added_removed_changed(self):
        old = self._version(
            1,
            snapshot=self._snap(
                title="Alt", status="entwurf",
                tags=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
                custom_fields={"Betrag": "100", "Alt": "x"},
            ),
        )
        new = self._version(
            2,
            snapshot=self._snap(
                title="Neu", status="freigegeben",
                tags=[{"id": 2, "name": "B"}, {"id": 3, "name": "C"}],
                custom_fields={"Betrag": "200", "Neu": "y"},
            ),
        )
        result = self._compare(old, new)
        self.assertTrue(result["metadata_versioning_supported"])
        # Metadaten changed (title + status).
        meta = result["metadata"]
        self.assertEqual(meta["changed"]["title"], {"old": "Alt", "new": "Neu"})
        self.assertEqual(meta["changed"]["status"], {"old": "entwurf", "new": "freigegeben"})
        self.assertEqual(meta["added"], {})
        self.assertEqual(meta["removed"], {})
        # Tags: id 3 hinzu, id 1 weg.
        self.assertEqual(result["tags"]["added"], [{"id": 3, "name": "C"}])
        self.assertEqual(result["tags"]["removed"], [{"id": 1, "name": "A"}])
        # Custom-Fields: Betrag changed, Neu added, Alt removed.
        cf = result["custom_fields"]
        self.assertEqual(cf["changed"]["Betrag"], {"old": "100", "new": "200"})
        self.assertEqual(cf["added"], {"Neu": "y"})
        self.assertEqual(cf["removed"], {"Alt": "x"})
        # Summary-Flags.
        self.assertTrue(result["summary"]["metadata_changed"])
        self.assertTrue(result["summary"]["tags_changed"])
        self.assertTrue(result["summary"]["custom_fields_changed"])

    def test_gleiche_snapshots_keine_changes(self):
        snap = self._snap(title="Gleich", tags=[{"id": 1, "name": "A"}], custom_fields={"F": "1"})
        old = self._version(1, snapshot=snap, ocr_text="gleich")
        new = self._version(2, snapshot=dict(snap), ocr_text="gleich")
        result = self._compare(old, new)
        self.assertTrue(result["metadata_versioning_supported"])
        self.assertFalse(result["summary"]["metadata_changed"])
        self.assertFalse(result["summary"]["tags_changed"])
        self.assertFalse(result["summary"]["custom_fields_changed"])
        self.assertEqual(result["tags"], {"added": [], "removed": []})

    def test_supported_nur_wenn_beide_snapshots(self):
        with_snap = self._version(
            1, snapshot=self._snap(title="X", tags=[], custom_fields={}),
        )
        without_snap = self._version(2)  # kein Snapshot
        result = self._compare(with_snap, without_snap)
        # Nur EINE Seite hat einen Snapshot → nicht unterstützt, Leersektionen (Stufe-1-UX).
        self.assertFalse(result["metadata_versioning_supported"])
        self.assertEqual(result["metadata"], {})
        self.assertEqual(result["tags"], {"added": [], "removed": []})
        self.assertEqual(result["custom_fields"], {})
        self.assertFalse(result["summary"]["metadata_changed"])

    def test_text_diff_html_leer_bei_gleichheit(self):
        old = self._version(1, ocr_text="identisch")
        new = self._version(2, ocr_text="identisch", sha256="1" * 64)
        result = self._compare(old, new)
        self.assertFalse(result["summary"]["text_changed"])
        self.assertEqual(result["text_diff_html"], "")

    def test_text_diff_html_gefuellt_bei_unterschied(self):
        old = self._version(1, ocr_text="alt")
        new = self._version(2, ocr_text="neu")
        result = self._compare(old, new)
        self.assertTrue(result["summary"]["text_changed"])
        self.assertIn("<table", result["text_diff_html"])
