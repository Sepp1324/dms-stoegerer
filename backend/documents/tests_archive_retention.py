import hashlib
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import AuditLogEntry, Document, DocumentVersion
from .services import archive, version_snapshot

User = get_user_model()


class ArchiveDocMixin:
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()
        super().tearDown()

    def make_ready_document(
        self,
        owner=None,
        *,
        content=b"archiv-test",
        with_artifacts=False,
    ):
        doc = Document.objects.create(title="Archivdokument", owner=owner)
        path = Path(self.tmpdir.name) / f"doc-{doc.id}.pdf"
        path.write_bytes(content)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=str(path),
            archive_path=str(path) if with_artifacts else "",
            thumbnail_path=str(path) if with_artifacts else "",
            sha256=hashlib.sha256(content).hexdigest(),
            processing_state=DocumentVersion.ProcessingState.READY,
            is_immutable=False,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        version_snapshot.write_snapshot_on_seal(version, actor=owner)
        version.is_immutable = True
        version.save(update_fields=["is_immutable"])
        return doc, version, path


class ArchiveServiceTests(ArchiveDocMixin, TestCase):
    def test_verify_document_archive_ok_persists_status(self):
        doc, _version, _path = self.make_ready_document()

        report = archive.verify_document_archive(doc)

        self.assertEqual(report["status"], Document.ArchiveStatus.OK)
        doc.refresh_from_db()
        self.assertEqual(doc.archive_status, Document.ArchiveStatus.OK)
        self.assertTrue(doc.archive_checked_at)
        self.assertFalse(doc.archive_error)

    def test_verify_document_archive_detects_file_hash_mismatch(self):
        doc, _version, path = self.make_ready_document()
        path.write_bytes(b"nachtraeglich veraendert")

        report = archive.verify_document_archive(doc)

        self.assertEqual(report["status"], Document.ArchiveStatus.ERROR)
        self.assertFalse(report["integrity"]["chain_ok"])
        doc.refresh_from_db()
        self.assertEqual(doc.archive_status, Document.ArchiveStatus.ERROR)
        self.assertIn("Datei-Hash", doc.archive_error)

    def test_verify_document_archive_detects_archive_tamper(self):
        # Separates Archiv-PDF mit hinterlegtem archive_sha256.
        doc, version, _path = self.make_ready_document()
        apath = Path(self.tmpdir.name) / f"arch-{doc.id}.pdf"
        apath.write_bytes(b"%PDF archiv original")
        DocumentVersion.objects.filter(pk=version.pk).update(
            archive_path=str(apath),
            archive_sha256=hashlib.sha256(b"%PDF archiv original").hexdigest(),
        )

        # Unverändert -> OK.
        self.assertEqual(
            archive.verify_document_archive(doc)["status"], Document.ArchiveStatus.OK
        )

        # Archiv nachträglich manipuliert -> ERROR (Hash stimmt nicht).
        apath.write_bytes(b"%PDF manipuliert")
        report = archive.verify_document_archive(doc)
        self.assertEqual(report["status"], Document.ArchiveStatus.ERROR)
        self.assertTrue(any("Archiv-PDF verändert" in e for e in report["errors"]))


class ArchiveApiTests(ArchiveDocMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="archive-user", password="pw", role="user"
        )
        self.guest = User.objects.create_user(
            username="archive-guest", password="pw", role="guest"
        )
        self.admin = User.objects.create_user(
            username="archive-admin", password="pw", role="admin"
        )
        self.doc, self.version, self.path = self.make_ready_document(
            owner=self.user,
            with_artifacts=True,
        )

    def test_document_archive_check_action_persists_and_audits(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(f"/api/documents/{self.doc.id}/archive-check/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Document.ArchiveStatus.OK)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.archive_status, Document.ArchiveStatus.OK)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="archive_check",
                object_type="Document",
                object_id=str(self.doc.id),
            ).exists()
        )

    def test_legal_hold_blocks_delete_before_retention_or_worm_checks(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            f"/api/documents/{self.doc.id}/legal-hold/",
            {"enabled": True, "reason": "Streitfall mit Versicherung"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["legal_hold"])

        delete_response = self.client.delete(f"/api/documents/{self.doc.id}/")

        self.assertEqual(delete_response.status_code, 403)
        self.assertIn("Legal Hold", str(delete_response.data["detail"]))
        self.assertTrue(Document.objects.filter(pk=self.doc.pk).exists())

    def test_guest_cannot_set_legal_hold(self):
        self.client.force_authenticate(self.guest)

        response = self.client.post(
            f"/api/documents/{self.doc.id}/legal-hold/",
            {"enabled": True, "reason": "nicht erlaubt"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_versions_retention_blockt_loeschen_ohne_500_und_falschen_audit(self):
        # P1: NUR die Version hat eine aktive Retention (Dokument selbst nicht,
        # keine WORM-Version, kein Legal Hold). Frueher schrieb die View bereits
        # "delete", dann warf das Model DjangoValidationError -> HTTP 500, das Doc
        # blieb, im Audit stand aber „geloescht". Jetzt: 403 + retention_block,
        # KEIN "delete"-Audit.
        from datetime import timedelta

        from django.utils import timezone

        doc = Document.objects.create(title="RetDoc", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/tmp/x.pdf",
            sha256="d" * 64, mime_type="application/pdf", size=1,
            is_immutable=False,
            retention_until=timezone.now().date() + timedelta(days=30),
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        self.client.force_authenticate(self.user)

        resp = self.client.delete(f"/api/documents/{doc.id}/")

        self.assertEqual(resp.status_code, 403)
        self.assertIn("Aufbewahrungsfrist", str(resp.data["detail"]))
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="retention_block", object_id=str(doc.id)
            ).exists()
        )
        self.assertFalse(
            AuditLogEntry.objects.filter(
                action="delete", object_id=str(doc.id)
            ).exists()
        )

    def test_delete_entfernt_artefaktdateien(self):
        # P2: Beim Loeschen werden Original-/Archiv-/Thumbnail-Dateien der
        # Versionen entfernt (sonst blieben geloeschte Inhalte auf dem PVC).
        from unittest import mock

        from . import storage, tasks

        doc = Document.objects.create(title="MitDateien", owner=self.user)
        orig = Path(self.tmpdir.name) / "orig.pdf"
        arch = Path(self.tmpdir.name) / "arch.pdf"
        thumb = Path(self.tmpdir.name) / "thumb.jpg"
        for p in (orig, arch, thumb):
            p.write_bytes(b"x")
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=str(orig),
            archive_path=str(arch), thumbnail_path=str(thumb),
            sha256="f" * 64, mime_type="application/pdf", size=1, is_immutable=False,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        self.client.force_authenticate(self.user)

        # Cleanup-Task synchron ausfuehren (statt an Celery zu delegieren).
        # DATA_DIR auf den Tmpdir patchen, damit die Testdateien den Root-Check
        # bestehen (der Cleanup entfernt nur Pfade UNTER DATA_DIR).
        with mock.patch.object(
            storage, "DATA_DIR", Path(self.tmpdir.name)
        ), mock.patch.object(
            tasks.cleanup_artifact_files,
            "delay",
            side_effect=lambda paths, job_id=None: tasks.cleanup_artifact_files(
                paths, job_id=job_id
            ),
        ), self.captureOnCommitCallbacks(execute=True):
            resp = self.client.delete(f"/api/documents/{doc.id}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(orig.exists())
        self.assertFalse(arch.exists())
        self.assertFalse(thumb.exists())

    def test_loeschbares_dokument_wird_geloescht_und_auditiert(self):
        # Happy Path: kein Block -> Doc geloescht, genau ein "delete"-Audit.
        doc = Document.objects.create(title="Weg", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/tmp/y.pdf",
            sha256="e" * 64, mime_type="application/pdf", size=1, is_immutable=False,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        self.client.force_authenticate(self.user)

        resp = self.client.delete(f"/api/documents/{doc.id}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        self.assertEqual(
            AuditLogEntry.objects.filter(
                action="delete", object_id=str(doc.id)
            ).count(),
            1,
        )

    def test_race_block_unter_sperre_gibt_403_statt_500(self):
        # P1: Wird der Block ERST unter der Zeilensperre sichtbar (paralleles
        # Sealing zwischen Vorabpruefung und Loeschen), wirft das Model eine
        # DjangoValidationError. Die View muss das in 403 uebersetzen (nicht 500),
        # den Block-Audit dauerhaft schreiben (kein Rollback-Verlust) und KEIN
        # "delete"-Audit hinterlassen.
        from unittest import mock

        from django.core.exceptions import ValidationError as DjangoValidationError

        doc = Document.objects.create(title="RaceDoc", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/tmp/z.pdf",
            sha256="c" * 64, mime_type="application/pdf", size=1, is_immutable=False,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        self.client.force_authenticate(self.user)

        block = ("immutable_block", "Dokument enthält unveränderliche (WORM-)Versionen.")
        # Vorabpruefung sieht (noch) keinen Block -> None; das gesperrte delete()
        # scheitert (Sealing hat gewonnen); die Nachpruefung im except sieht ihn.
        with mock.patch.object(
            Document, "delete_block", side_effect=[None, block]
        ), mock.patch.object(
            Document, "delete", side_effect=DjangoValidationError("gesperrt")
        ):
            resp = self.client.delete(f"/api/documents/{doc.id}/")

        self.assertEqual(resp.status_code, 403)
        self.assertIn("WORM", str(resp.data["detail"]))
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="immutable_block", object_id=str(doc.id)
            ).exists()
        )
        self.assertFalse(
            AuditLogEntry.objects.filter(
                action="delete", object_id=str(doc.id)
            ).exists()
        )

    def test_archive_health_is_admin_only(self):
        archive.verify_document_archive(self.doc)
        self.client.force_authenticate(self.user)
        denied = self.client.get("/api/system/archive-health/")
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/system/archive-health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["documents"], 1)
        self.assertEqual(response.data["summary"]["archive_ok"], 1)

    def test_evidence_status_respects_owner_scope(self):
        other_doc, _other_version, _other_path = self.make_ready_document(
            owner=self.admin,
            with_artifacts=True,
        )
        archive.verify_document_archive(self.doc)
        archive.verify_document_archive(other_doc)

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/documents/evidence-status/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["documents"], 1)
        self.assertEqual(response.data["summary"]["evidence_ok"], 1)

    def test_evidence_report_verifies_document_and_audits_access(self):
        archive.verify_document_archive(self.doc)
        self.client.force_authenticate(self.user)

        response = self.client.get(f"/api/documents/{self.doc.id}/evidence/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")
        self.assertTrue(response.data["integrity"]["chain_ok"])
        self.assertEqual(response.data["versions"][0]["version_no"], 1)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="evidence_report_view",
                object_type="Document",
                object_id=str(self.doc.id),
            ).exists()
        )

    def test_evidence_report_is_owner_scoped(self):
        other = User.objects.create_user(username="other-owner", password="pw", role="user")
        self.client.force_authenticate(other)

        response = self.client.get(f"/api/documents/{self.doc.id}/evidence/")

        self.assertEqual(response.status_code, 404)


class EvidenceArchiveHashGateTests(ArchiveDocMixin, TestCase):
    """P1/P2: Der volle Archiv-Hash wird NUR in der Detailansicht berechnet; ein
    Lesefehler kippt das Center nicht mit 500."""

    def _doc_with_archive(self, content=b"%PDF archiv"):
        doc, version, _ = self.make_ready_document()
        apath = Path(self.tmpdir.name) / f"arch-{doc.id}.pdf"
        apath.write_bytes(content)
        DocumentVersion.objects.filter(pk=version.pk).update(
            archive_path=str(apath),
            archive_sha256=hashlib.sha256(content).hexdigest(),
        )
        return Document.objects.get(pk=doc.id), apath

    def test_uebersicht_hasht_archiv_nicht(self):
        from unittest import mock

        from .services import evidence

        doc, _ = self._doc_with_archive()
        with mock.patch("documents.pipeline.sha256_of") as h:
            evidence.evidence_status([doc])
        h.assert_not_called()   # Übersicht liest keine Archivdatei (kein Timeout-Risiko)

    def test_detail_erkennt_manipuliertes_archiv(self):
        from .services import evidence

        doc, apath = self._doc_with_archive()
        apath.write_bytes(b"%PDF manipuliert")   # nach dem Setzen des Hashes
        report = evidence.document_report(doc)
        arch = next(c for c in report["checks"] if c["code"] == "archive_file")
        self.assertEqual(arch["status"], "error")

    def test_unlesbares_archiv_meldet_fehler_ohne_crash(self):
        from unittest import mock

        from documents import pipeline

        doc, apath = self._doc_with_archive()
        real = pipeline.sha256_of

        def _se(p):
            if str(p) == str(apath):
                raise OSError("NFS weg")
            return real(p)

        with mock.patch("documents.pipeline.sha256_of", side_effect=_se):
            report = archive.verify_document_archive(doc)
        self.assertEqual(report["status"], Document.ArchiveStatus.ERROR)
        self.assertTrue(any("nicht lesbar" in e for e in report["errors"]))

    def test_detail_teilt_integrity_und_hasht_archiv_nur_einmal(self):
        # P2: document_report berechnet die Original-Hash-Kette EINMAL (geteilt an
        # _document_summary UND verify_document_archive) und hasht das aktuelle
        # Archiv-PDF nur EINMAL (Evidence reuse ueber archive_files).
        from unittest import mock

        from documents import pipeline

        from .services import evidence

        doc, apath = self._doc_with_archive()
        real_integrity = pipeline.verify_document_integrity
        real_sha = pipeline.sha256_of

        with mock.patch(
            "documents.pipeline.verify_document_integrity",
            side_effect=real_integrity,
        ) as vi, mock.patch(
            "documents.pipeline.sha256_of", side_effect=real_sha
        ) as sha:
            report = evidence.document_report(doc)

        # Hash-Kette genau EINMAL (nicht in summary UND archive erneut).
        self.assertEqual(vi.call_count, 1)
        # Aktuelles Archiv-PDF genau EINMAL gehasht.
        archive_hashes = [
            c for c in sha.call_args_list if str(c.args[0]) == str(apath)
        ]
        self.assertEqual(len(archive_hashes), 1)
        # Ergebnis bleibt korrekt (unmanipuliertes Archiv -> archive_file ok).
        arch = next(c for c in report["checks"] if c["code"] == "archive_file")
        self.assertEqual(arch["status"], "ok")
        self.assertTrue(report["archive_report"]["archive_files"])


class IntegrityUnreadableOriginalTests(ArchiveDocMixin, TestCase):
    """P2: Ist die Originaldatei vorhanden, aber nicht lesbar (Rechte/NFS/I/O),
    meldet verify_document_integrity file_ok=False + Fehlerdetail statt 500. Der
    Evidence-original_file-Check übernimmt diesen Fehler (statt nur exists())."""

    def _raise_on(self, path):
        from documents import pipeline

        real = pipeline.sha256_of

        def _se(p):
            if str(p) == str(path):
                raise OSError("Permission denied")
            return real(p)

        return _se

    def test_integrity_meldet_file_ok_false_ohne_crash(self):
        from unittest import mock

        from documents import pipeline

        doc, version, path = self.make_ready_document()
        with mock.patch(
            "documents.pipeline.sha256_of", side_effect=self._raise_on(path)
        ):
            report = pipeline.verify_document_integrity(doc)

        self.assertFalse(report["chain_ok"])
        entry = next(v for v in report["versions"] if v["version_no"] == 1)
        self.assertTrue(entry["file_present"])
        self.assertFalse(entry["file_ok"])
        self.assertEqual(entry["computed_sha256"], "")
        self.assertIn("nicht lesbar", entry["error"])

    def test_evidence_original_file_check_spiegelt_lesefehler(self):
        # P2: Kern-Fix – der original_file-Check darf nicht gruen sein, waehrend
        # die Integritaetspruefung den Lesefehler erkennt.
        from unittest import mock

        from documents import pipeline

        from .services import evidence

        doc, version, path = self.make_ready_document()
        with mock.patch(
            "documents.pipeline.sha256_of", side_effect=self._raise_on(path)
        ):
            report = evidence.document_report(doc)

        original = next(c for c in report["checks"] if c["code"] == "original_file")
        self.assertEqual(original["status"], "error")
        self.assertIn("nicht lesbar", original["detail"])
        self.assertTrue(
            any(r["code"] == "original_unreadable" for r in report["risks"])
        )
        self.assertEqual(report["status"], "error")

    def test_lesbares_original_bleibt_ok(self):
        from documents import pipeline

        from .services import evidence

        doc, version, path = self.make_ready_document()
        report = evidence.document_report(doc)
        original = next(c for c in report["checks"] if c["code"] == "original_file")
        self.assertEqual(original["status"], "ok")
