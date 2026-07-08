from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import (
    AuditLogEntry,
    CaseFile,
    CaseFileCandidate,
    Correspondent,
    Document,
    DocumentPageText,
    DocumentVersion,
    ExtractionCandidate,
)

User = get_user_model()


class CaseFileAutopilotTests(APITestCase):
    """Akten-Autopilot: Vorschläge bleiben explizit, auditierbar und owner-gescoped."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="case-auto-u", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="case-auto-o", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="case-auto-g", password="pw", role="guest"
        )
        cls.correspondent = Correspondent.objects.create(name="Wüstenrot")

    def _doc(self, title, owner, text, *, correspondent=None):
        doc = Document.objects.create(
            title=title,
            owner=owner,
            correspondent=correspondent,
            review_status=Document.ReviewStatus.NEEDS_REVIEW,
        )
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/tmp/{title}.pdf",
            sha256=title.encode().hex().ljust(64, "0")[:64],
            ocr_text=text,
            processing_state=DocumentVersion.ProcessingState.READY,
        )
        DocumentPageText.objects.create(version=version, page_no=1, text=text)
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def _contract_candidate(self, doc, value="510/839294-2"):
        return ExtractionCandidate.objects.create(
            document=doc,
            field=ExtractionCandidate.Field.CONTRACT_NUMBER,
            value=value,
            normalized_value=value,
            confidence=88,
        )

    def test_generate_case_candidates_suggests_existing_case(self):
        case_file = CaseFile.objects.create(title="Wüstenrot Vertrag", owner=self.user)
        existing = self._doc(
            "Bestehendes Wüstenrot Dokument",
            self.user,
            "Vertragsnummer 510/839294-2 monatlicher Beitrag",
            correspondent=self.correspondent,
        )
        existing.case_file = case_file
        existing.save(update_fields=["case_file"])
        self._contract_candidate(existing)
        doc = self._doc(
            "Neuer Wüstenrot Beleg",
            self.user,
            "Mandatsreferenz 510/839294-2 Folgebeitrag",
            correspondent=self.correspondent,
        )
        self._contract_candidate(doc)
        self.client.force_authenticate(self.user)

        resp = self.client.post(f"/api/documents/{doc.id}/case-candidates/")

        self.assertEqual(resp.status_code, 200)
        existing_hits = [
            row for row in resp.data if row["kind"] == CaseFileCandidate.Kind.EXISTING_CASE
        ]
        self.assertEqual(existing_hits[0]["case_file"], case_file.id)
        self.assertGreaterEqual(existing_hits[0]["score"], 45)
        self.assertTrue(existing_hits[0]["signals"])
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="generate_case_file_candidates",
                object_id=str(doc.id),
            ).exists()
        )

    def test_owner_scope_does_not_suggest_foreign_case(self):
        foreign_case = CaseFile.objects.create(title="Fremde Akte", owner=self.other)
        foreign_doc = self._doc(
            "Fremdes Wüstenrot Dokument",
            self.other,
            "Vertragsnummer 510/839294-2",
            correspondent=self.correspondent,
        )
        foreign_doc.case_file = foreign_case
        foreign_doc.save(update_fields=["case_file"])
        self._contract_candidate(foreign_doc)
        doc = self._doc(
            "Eigener Wüstenrot Beleg",
            self.user,
            "Vertragsnummer 510/839294-2",
            correspondent=self.correspondent,
        )
        self._contract_candidate(doc)
        self.client.force_authenticate(self.user)

        resp = self.client.post(f"/api/documents/{doc.id}/case-candidates/")

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(foreign_case.id, {row["case_file"] for row in resp.data})

    def test_apply_existing_case_candidate_assigns_document_and_dismisses_rest(self):
        case_file = CaseFile.objects.create(title="Versicherung", owner=self.user)
        doc = self._doc("Neuer Beleg", self.user, "Vertragsnummer 510/839294-2")
        keep = CaseFileCandidate.objects.create(
            document=doc,
            case_file=case_file,
            kind=CaseFileCandidate.Kind.EXISTING_CASE,
            signature=f"existing:{case_file.id}",
            score=82,
            reason="Gleiche Vertragsnummer",
            signals=[{"type": "contract", "value": "510/839294-2"}],
        )
        other = CaseFileCandidate.objects.create(
            document=doc,
            kind=CaseFileCandidate.Kind.NEW_CASE,
            suggested_title="Neue Versicherung",
            signature="new:versicherung",
            score=55,
        )
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            f"/api/documents/{doc.id}/case-candidates/{keep.id}/apply/"
        )

        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        keep.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(doc.case_file, case_file)
        self.assertEqual(keep.status, CaseFileCandidate.Status.APPLIED)
        self.assertEqual(other.status, CaseFileCandidate.Status.DISMISSED)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="apply_case_file_candidate",
                object_id=str(doc.id),
            ).exists()
        )

    def test_apply_new_case_candidate_creates_case_file(self):
        doc = self._doc("Neuer Vorgang", self.user, "Neuer Vertrag")
        candidate = CaseFileCandidate.objects.create(
            document=doc,
            kind=CaseFileCandidate.Kind.NEW_CASE,
            suggested_title="Wüstenrot · Vertrag 510/839294-2",
            signature="new:wuestenrot-vertrag",
            score=68,
        )
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            f"/api/documents/{doc.id}/case-candidates/{candidate.id}/apply/"
        )

        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        candidate.refresh_from_db()
        self.assertIsNotNone(doc.case_file)
        self.assertEqual(doc.case_file.owner, self.user)
        self.assertEqual(doc.case_file.title, "Wüstenrot · Vertrag 510/839294-2")
        self.assertEqual(candidate.status, CaseFileCandidate.Status.APPLIED)

    def test_dismiss_case_candidate_marks_candidate(self):
        doc = self._doc("Neuer Vorgang", self.user, "Text")
        candidate = CaseFileCandidate.objects.create(
            document=doc,
            kind=CaseFileCandidate.Kind.NEW_CASE,
            suggested_title="Nicht diese Akte",
            signature="new:nicht-diese-akte",
            score=50,
        )
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            f"/api/documents/{doc.id}/case-candidates/{candidate.id}/dismiss/"
        )

        self.assertEqual(resp.status_code, 200)
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, CaseFileCandidate.Status.DISMISSED)
        self.assertIsNotNone(candidate.dismissed_at)

    def test_guest_cannot_generate_or_apply_case_candidates(self):
        doc = self._doc("Gast Dokument", self.guest, "Text")
        candidate = CaseFileCandidate.objects.create(
            document=doc,
            kind=CaseFileCandidate.Kind.NEW_CASE,
            suggested_title="Gast Akte",
            signature="new:gast-akte",
            score=50,
        )
        self.client.force_authenticate(self.guest)

        generate = self.client.post(f"/api/documents/{doc.id}/case-candidates/")
        apply = self.client.post(
            f"/api/documents/{doc.id}/case-candidates/{candidate.id}/apply/"
        )

        self.assertEqual(generate.status_code, 403)
        self.assertEqual(apply.status_code, 403)
