"""Test der atomaren Übernahme eines Extraktionskandidaten in ein Zusatzfeld.

Wert schreiben + Kandidat auf ``applied`` setzen + Audit laufen in EINER
Transaktion – kein falscher ``dismissed``-Status, keine Teilerfolge mehr.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from documents.models import (
    CustomField,
    CustomFieldValue,
    Document,
    DocumentVersion,
    ExtractionCandidate,
)

User = get_user_model()


class ApplyToFieldTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="pw", role="user")
        self.guest = User.objects.create_user("guest", password="pw", role="guest")
        self.doc = Document.objects.create(title="doc", owner=self.user)
        DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/x.pdf", sha256="a" * 64
        )
        self.field = CustomField.objects.create(name="IBAN", data_type="text")
        self.cand = ExtractionCandidate.objects.create(
            document=self.doc,
            field=ExtractionCandidate.Field.IBAN,
            value="AT61 1904 3002 3457 3201",
            normalized_value="AT611904300234573201",
        )

    def _url(self, cid):
        return f"/api/documents/{self.doc.id}/extraction-candidates/{cid}/apply-to-field/"

    def test_uebernahme_schreibt_wert_und_setzt_applied(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url(self.cand.id), {"field": self.field.id}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "applied")
        self.cand.refresh_from_db()
        self.assertEqual(self.cand.status, ExtractionCandidate.Status.APPLIED)
        self.assertIsNotNone(self.cand.applied_at)
        val = CustomFieldValue.objects.get(document=self.doc, field=self.field)
        self.assertEqual(val.value, "AT611904300234573201")  # normalisiert

    def test_unbekanntes_feld_400_und_kein_statuswechsel(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url(self.cand.id), {"field": 99999}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.cand.refresh_from_db()
        self.assertEqual(self.cand.status, ExtractionCandidate.Status.PENDING)
        self.assertFalse(CustomFieldValue.objects.filter(document=self.doc).exists())

    def test_iban_in_zahlenfeld_400_kein_write(self):
        # Typprüfung: eine IBAN darf NICHT in ein Zahlenfeld.
        num = CustomField.objects.create(name="Betrag", data_type="number")
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url(self.cand.id), {"field": num.id}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.cand.refresh_from_db()
        self.assertEqual(self.cand.status, ExtractionCandidate.Status.PENDING)
        self.assertFalse(CustomFieldValue.objects.filter(field=num).exists())

    def test_betrag_in_zahlenfeld_ok(self):
        num = CustomField.objects.create(name="Summe", data_type="number")
        cand = ExtractionCandidate.objects.create(
            document=self.doc,
            field=ExtractionCandidate.Field.AMOUNT,
            value="42,00 EUR",
            normalized_value="42.00",
        )
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url(cand.id), {"field": num.id}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            CustomFieldValue.objects.get(document=self.doc, field=num).value, "42.00"
        )

    def test_fehlende_feld_id_400(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.post(self._url(self.cand.id), {}, format="json").status_code,
            400,
        )

    def test_nicht_offener_kandidat_409(self):
        self.cand.status = ExtractionCandidate.Status.DISMISSED
        self.cand.save(update_fields=["status"])
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url(self.cand.id), {"field": self.field.id}, format="json")
        self.assertEqual(resp.status_code, 409)

    def test_gast_403(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post(self._url(self.cand.id), {"field": self.field.id}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_nan_und_infinity_in_zahlenfeld_400(self):
        num = CustomField.objects.create(name="Zahl", data_type="number")
        self.client.force_authenticate(self.user)
        for bad in ("NaN", "Infinity", "-Infinity"):
            cand = ExtractionCandidate.objects.create(
                document=self.doc,
                field=ExtractionCandidate.Field.AMOUNT,
                value=bad,
                normalized_value=bad,
            )
            resp = self.client.post(self._url(cand.id), {"field": num.id}, format="json")
            self.assertEqual(resp.status_code, 400, bad)
        self.assertFalse(CustomFieldValue.objects.filter(field=num).exists())

    def test_patch_zahlenfeld_lehnt_text_ab(self):
        # Die zentrale Typprüfung greift auch beim regulären PATCH.
        num = CustomField.objects.create(name="Zahl", data_type="number")
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"custom_field_values": [{"field": num.id, "value": "keine zahl"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(CustomFieldValue.objects.filter(field=num).exists())
