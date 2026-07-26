import tempfile
from pathlib import Path
from unittest import mock

import pikepdf
from django.contrib.auth import get_user_model
from pikepdf import Name
from rest_framework.test import APITestCase

from . import pipeline, storage
from .models import AuditLogEntry, Document, DocumentVersion, Tag

User = get_user_model()
ROTATE_NAME = Name("/Rotate")


class PdfWorkbenchTests(APITestCase):
    """PDF-Werkbank: Seitenoperationen erzeugen neue Versionen/Dokumente."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.originals = Path(self.tmp.name) / "originals"
        self.originals.mkdir(parents=True, exist_ok=True)
        self.storage_patch = mock.patch.object(storage, "ORIGINALS_DIR", self.originals)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)

        self.user = User.objects.create_user(
            username="pdf-workbench-user", password="pw", role="user"
        )
        self.other = User.objects.create_user(
            username="pdf-workbench-other", password="pw", role="user"
        )
        self.guest = User.objects.create_user(
            username="pdf-workbench-guest", password="pw", role="guest"
        )

    def _pdf(self, name: str, pages: int) -> Path:
        path = self.originals / name
        pdf = pikepdf.Pdf.new()
        for _idx in range(pages):
            pdf.add_blank_page(page_size=(72, 72))
        pdf.save(path)
        pdf.close()
        return path

    def _doc(self, title: str, owner, pages: int = 3) -> Document:
        path = self._pdf(f"{title}.pdf", pages)
        doc = Document.objects.create(title=title, owner=owner)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=str(path),
            sha256=pipeline.sha256_of(path),
            mime_type="application/pdf",
            size=path.stat().st_size,
            page_count=pages,
            processing_state=DocumentVersion.ProcessingState.READY,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_pages_manifest_reads_current_pdf(self):
        doc = self._doc("manifest", self.user, pages=3)
        self.client.force_authenticate(self.user)

        resp = self.client.get(f"/api/documents/{doc.id}/pdf-workbench/pages/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["page_count"], 3)
        self.assertEqual([page["page"] for page in resp.data["pages"]], [1, 2, 3])

    def test_page_thumbnail_endpoint_returns_jpeg(self):
        doc = self._doc("thumb", self.user, pages=2)
        self.client.force_authenticate(self.user)

        with mock.patch(
            "documents.services.pdf_workbench.render_page_thumbnail",
            return_value=b"jpeg-bytes",
        ):
            resp = self.client.get(
                f"/api/documents/{doc.id}/pdf-workbench/pages/1/thumbnail/"
                f"?version_id={doc.current_version.id}"
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/jpeg")
        self.assertEqual(resp.content, b"jpeg-bytes")

    def test_thumbnail_ohne_version_id_400(self):
        doc = self._doc("thumb-noversion", self.user, pages=1)
        self.client.force_authenticate(self.user)
        resp = self.client.get(
            f"/api/documents/{doc.id}/pdf-workbench/pages/1/thumbnail/"
        )
        self.assertEqual(resp.status_code, 400)

    def test_thumbnail_fremde_version_404(self):
        doc = self._doc("thumb-a", self.user, pages=1)
        other = self._doc("thumb-b", self.user, pages=1)
        self.client.force_authenticate(self.user)
        resp = self.client.get(
            f"/api/documents/{doc.id}/pdf-workbench/pages/1/thumbnail/"
            f"?version_id={other.current_version.id}"
        )
        self.assertEqual(resp.status_code, 404)

    def test_split_mit_veralteter_source_version_409(self):
        doc = self._doc("split-stale", self.user, pages=2)
        self.client.force_authenticate(self.user)
        with mock.patch("documents.views.process_document_version.delay"):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/split/",
                {"parts": [{"title": "T", "pages": [1]}], "source_version_id": 999999},
                format="json",
            )
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(Document.objects.filter(title="T").exists())

    def test_thumbnail_cache_vermeidet_erneutes_rendern(self):
        # P1: Ein bereits gerendertes (version, page, dpi)-JPEG kommt beim zweiten
        # Aufruf aus dem Disk-Cache – Poppler wird NICHT erneut ausgeführt.
        from PIL import Image

        from .services import pdf_workbench

        doc = self._doc("cache", self.user, pages=1)
        version = doc.current_version

        def fake_convert(*args, **kwargs):
            return [Image.new("RGB", (20, 20), "white")]

        with mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name)), mock.patch(
            "pdf2image.convert_from_path", side_effect=fake_convert
        ) as conv:
            first = pdf_workbench.render_page_thumbnail(version, 1)
            second = pdf_workbench.render_page_thumbnail(version, 1)

        self.assertTrue(first)
        self.assertEqual(first, second)
        conv.assert_called_once()  # zweiter Aufruf aus dem Cache, kein Re-Render

    def test_thumbnail_cache_read_aktualisiert_mtime_lru(self):
        # P2: Echte LRU – ein Cache-TREFFER (Lesezugriff) muss die mtime auffrischen,
        # damit der Größen-Prune (sortiert nach mtime) häufig gelesene, aber alte
        # Thumbnails nicht fälschlich als „ältestes" verwirft.
        import os
        import time

        from PIL import Image

        from .services import pdf_workbench

        doc = self._doc("lru", self.user, pages=1)
        version = doc.current_version

        with mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name)), mock.patch(
            "pdf2image.convert_from_path",
            return_value=[Image.new("RGB", (20, 20), "white")],
        ):
            pdf_workbench.render_page_thumbnail(version, 1)  # rendert + schreibt Cache
            cache_path = pdf_workbench._thumbnail_cache_path(version, 1, 110)
            self.assertTrue(cache_path.exists())
            # mtime künstlich alt setzen, dann Cache-Treffer auslösen.
            old = time.time() - 30 * 86400
            os.utime(cache_path, (old, old))
            pdf_workbench.render_page_thumbnail(version, 1)  # Cache-Treffer

        self.assertGreater(cache_path.stat().st_mtime, old + 86400)

    def test_thumbnail_singleflight_rendert_nur_einmal(self):
        # P2: Zwei gleichzeitige Erst-Misses desselben (version, page, dpi) dürfen
        # Poppler NUR EINMAL starten (Singleflight-Sperre pro Cache-Key), statt
        # mehrere Renderprozesse zu starten und dieselbe Datei parallel zu schreiben.
        import threading
        import time as _time

        from PIL import Image

        from django.db import connection
        from .services import pdf_workbench

        doc = self._doc("sf", self.user, pages=1)
        version = doc.current_version
        calls: list[int] = []

        def fake_convert(*args, **kwargs):
            calls.append(1)
            _time.sleep(0.3)  # Renderfenster offen halten, damit der 2. Thread wartet
            return [Image.new("RGB", (20, 20), "white")]

        barrier = threading.Barrier(2, timeout=10)
        results: dict[str, object] = {}

        def worker(name):
            try:
                barrier.wait()
                results[name] = pdf_workbench.render_page_thumbnail(version, 1)
            except Exception as exc:  # noqa: BLE001
                results[name] = exc
            finally:
                connection.close()

        with mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name)), mock.patch(
            "pdf2image.convert_from_path", side_effect=fake_convert
        ), mock.patch.object(pdf_workbench, "_page_count", return_value=1):
            threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(15)

        self.assertEqual(len(calls), 1, "Singleflight: Poppler darf nur einmal laufen")
        self.assertEqual(results["a"], results["b"])
        self.assertIsInstance(results["a"], (bytes, bytearray))

    def test_thumbnail_atomar_veroeffentlicht_ohne_temp_reste(self):
        # P2: Nach dem Rendern liegt genau das fertige JPEG im Cache und KEINE
        # temporäre (.tmp-*) Datei bleibt zurück (atomare Veröffentlichung).
        from PIL import Image

        from .services import pdf_workbench

        doc = self._doc("atomic", self.user, pages=1)
        version = doc.current_version

        with mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name)), mock.patch(
            "pdf2image.convert_from_path",
            return_value=[Image.new("RGB", (20, 20), "white")],
        ):
            data = pdf_workbench.render_page_thumbnail(version, 1)
            # Cache-Pfad UNTER dem DATA_DIR-Patch bestimmen (sonst zeigte er auf das
            # echte Datenverzeichnis, nicht auf den Tmpdir).
            cache_path = pdf_workbench._thumbnail_cache_path(version, 1, 110)
            self.assertTrue(cache_path.exists())
            self.assertEqual(cache_path.read_bytes(), data)
            leftovers = list(cache_path.parent.glob(".tmp-*"))
            self.assertEqual(leftovers, [], f"Temp-Reste geblieben: {leftovers}")

    def test_thumbnail_render_bekommt_timeout(self):
        from PIL import Image

        from .services import pdf_workbench

        doc = self._doc("timeout", self.user, pages=1)
        version = doc.current_version

        with mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name)), mock.patch(
            "pdf2image.convert_from_path",
            return_value=[Image.new("RGB", (10, 10), "white")],
        ) as conv:
            pdf_workbench.render_page_thumbnail(version, 1)

        # Poppler-Rendern läuft mit hartem Timeout (P1).
        self.assertIn("timeout", conv.call_args.kwargs)
        self.assertGreater(conv.call_args.kwargs["timeout"], 0)

    def test_operation_ohne_source_version_id_400(self):
        # P2: source_version_id ist fuer alle mutierenden Werkbank-Endpunkte
        # verpflichtend (direkte API-Aufrufe duerfen den Konfliktschutz nicht
        # umgehen). Fehlt oder nicht positiv -> 400, keine neue Version.
        doc = self._doc("noversion-op", self.user, pages=2)
        self.client.force_authenticate(self.user)
        for body in (
            {"pages": [{"page": 1}]},  # fehlt
            {"pages": [{"page": 1}], "source_version_id": 0},  # nicht positiv
            {"pages": [{"page": 1}], "source_version_id": "abc"},  # keine Zahl
        ):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/rewrite/", body, format="json"
            )
            self.assertEqual(resp.status_code, 400, body)
        self.assertEqual(doc.versions.count(), 1)

    def test_rewrite_creates_new_version_with_reordered_rotated_pages(self):
        doc = self._doc("rewrite", self.user, pages=3)
        self.client.force_authenticate(self.user)

        with mock.patch("documents.views.process_document_version.delay") as delay:
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
                {
                    "source_version_id": doc.current_version.id,
                    "pages": [
                        {"page": 3},
                        {"page": 1, "rotation": 90},
                    ],
                    "reason": "Test",
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 201)
        doc.refresh_from_db()
        self.assertEqual(doc.versions.count(), 2)
        self.assertEqual(doc.current_version.version_no, 2)
        delay.assert_called_once_with(doc.current_version.id)
        with pikepdf.open(doc.current_version.file_path) as pdf:
            self.assertEqual(len(pdf.pages), 2)
            self.assertEqual(int(pdf.pages[1].obj.get(ROTATE_NAME, 0) or 0), 90)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="pdf_workbench_rewrite",
                object_id=str(doc.id),
            ).exists()
        )

    def test_rewrite_broker_down_returns_503_not_500(self):
        # Broker-Ausfall beim Enqueue darf NICHT als 500 durchschlagen: die
        # Version ist bereits erzeugt, der Client soll ein sauberes 503 („später
        # erneut") sehen. Regression zum PDF-Werkbank-500 (Redis MISCONF).
        from kombu.exceptions import OperationalError

        doc = self._doc("broker", self.user, pages=2)
        self.client.force_authenticate(self.user)

        with mock.patch(
            "documents.views.process_document_version.delay",
            side_effect=OperationalError("broker down"),
        ):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
                {"pages": [{"page": 1}], "source_version_id": doc.current_version.id},
                format="json",
            )

        self.assertEqual(resp.status_code, 503)
        # Version wurde trotz Enqueue-Fehler erzeugt UND als FAILED markiert, damit
        # der Retry-Endpoint sie aufgreifen kann (sonst hinge sie in UPLOADED und
        # wäre nicht reparierbar).
        doc.refresh_from_db()
        self.assertEqual(doc.versions.count(), 2)
        self.assertEqual(
            doc.current_version.processing_state,
            DocumentVersion.ProcessingState.FAILED,
        )
        self.assertEqual(doc.current_version.processing_failed_step, "hashing")

    def test_rewrite_invalid_page_returns_400_without_version(self):
        doc = self._doc("invalid", self.user, pages=2)
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
            {"pages": [{"page": 9}], "source_version_id": doc.current_version.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(doc.versions.count(), 1)

    def test_rewrite_ueber_seitenlimit_wird_abgelehnt(self):
        # P1: Auch der Rewrite muss die Ressourcengrenzen prüfen (nicht nur
        # Merge/Split) – sonst OOM durch tausendfache Wiederholung derselben Seite.
        from django.test import override_settings

        doc = self._doc("rewrite-limit", self.user, pages=2)
        self.client.force_authenticate(self.user)
        with override_settings(PDF_WORKBENCH_MAX_PAGES=1):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
                {"pages": [{"page": 1}, {"page": 2}], "source_version_id": doc.current_version.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(doc.versions.count(), 1)  # keine neue Version

    def test_rewrite_audit_fehler_rollt_version_zurueck(self):
        # P2: Version + Audit sind EINE Operation. Scheitert der Audit nach dem
        # Versions-Insert, darf KEINE neue aktuelle Version zurueckbleiben.
        doc = self._doc("rewrite-atomic", self.user, pages=2)
        self.client.force_authenticate(self.user)

        real = AuditLogEntry.objects.create

        def flaky(**kwargs):
            if kwargs.get("action") == "pdf_workbench_rewrite":
                raise RuntimeError("audit boom")
            return real(**kwargs)

        with mock.patch.object(
            AuditLogEntry.objects, "create", side_effect=flaky
        ), mock.patch("documents.views.process_document_version.delay"):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
                {"pages": [{"page": 1}], "source_version_id": doc.current_version.id},
                format="json",
            )

        self.assertEqual(resp.status_code, 400)
        doc.refresh_from_db()
        self.assertEqual(doc.versions.count(), 1)  # Rollback: keine 2. Version
        self.assertEqual(doc.current_version.version_no, 1)

    def test_split_creates_new_documents_and_copies_tags(self):
        doc = self._doc("split-source", self.user, pages=4)
        tag = Tag.objects.create(name="Werkbank", color="#93c5fd")
        doc.tags.add(tag)
        self.client.force_authenticate(self.user)

        with mock.patch("documents.views.process_document_version.delay") as delay:
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/split/",
                {
                    "source_version_id": doc.current_version.id,
                    "parts": [
                        {"title": "Teil A", "pages": [1, 2]},
                        {"title": "Teil B", "pages": [3, 4]},
                    ]
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.data["documents"]), 2)
        created = Document.objects.filter(title__in=["Teil A", "Teil B"]).order_by("title")
        self.assertEqual(created.count(), 2)
        self.assertTrue(all(item.owner == self.user for item in created))
        self.assertTrue(all(tag in item.tags.all() for item in created))
        self.assertEqual(delay.call_count, 2)
        with pikepdf.open(created[0].current_version.file_path) as pdf:
            self.assertEqual(len(pdf.pages), 2)
        self.assertTrue(
            AuditLogEntry.objects.filter(action="pdf_workbench_split").exists()
        )

    def test_split_ungueltiger_teil_erzeugt_keine_teildokumente(self):
        # P1: Teil 1 gültig, Teil 2 ungültig -> 400 und KEIN Teil-Dokument bleibt
        # bestehen (frueher blieb Teil 1 -> Duplikate beim erneuten Versuch).
        doc = self._doc("split-atomic", self.user, pages=3)
        self.client.force_authenticate(self.user)

        with mock.patch("documents.views.process_document_version.delay"):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/split/",
                {
                    "source_version_id": doc.current_version.id,
                    "parts": [
                        {"title": "TeilOK", "pages": [1]},
                        {"title": "TeilBad", "pages": [99]},  # ausserhalb 1..3
                    ]
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Document.objects.filter(title="TeilOK").exists())
        self.assertFalse(Document.objects.filter(title="TeilBad").exists())

    def test_split_ueber_seitenlimit_wird_abgelehnt(self):
        from django.test import override_settings

        doc = self._doc("split-limit", self.user, pages=4)
        self.client.force_authenticate(self.user)
        with override_settings(PDF_WORKBENCH_MAX_PAGES=2):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/split/",
                {"parts": [{"title": "Gross", "pages": [1, 2, 3, 4]}], "source_version_id": doc.current_version.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Document.objects.filter(title="Gross").exists())

    def test_merge_ueber_dokumentlimit_wird_abgelehnt(self):
        from django.test import override_settings

        target = self._doc("m-target", self.user, pages=1)
        a = self._doc("m-a", self.user, pages=1)
        b = self._doc("m-b", self.user, pages=1)
        self.client.force_authenticate(self.user)
        with override_settings(PDF_WORKBENCH_MAX_DOCUMENTS=1):
            resp = self.client.post(
                f"/api/documents/{target.id}/pdf-workbench/merge/",
                {"document_ids": [a.id, b.id], "source_version_id": target.current_version.id},  # target + 2 = 3 > Limit 1
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        target.refresh_from_db()
        # Version unverändert (keine neue Merge-Version).
        self.assertEqual(target.current_version.version_no, 1)

    def test_split_broker_ausfall_reiht_alle_ein_und_liefert_strukturiertes_503(self):
        # P1: Scheitert der Broker beim Enqueue, werden trotzdem ALLE erzeugten
        # Versionen best-effort eingereiht (jede Fehl-Version -> FAILED, nicht
        # UPLOADED), und die Antwort ist ein strukturiertes 503 mit den erzeugten
        # Dokument-IDs (damit kein Re-Split -> Duplikate).
        from kombu.exceptions import OperationalError

        doc = self._doc("split-broker", self.user, pages=4)
        self.client.force_authenticate(self.user)

        with mock.patch(
            "documents.views.process_document_version.delay",
            side_effect=OperationalError("broker down"),
        ):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/split/",
                {
                    "source_version_id": doc.current_version.id,
                    "parts": [
                        {"title": "BrokerA", "pages": [1, 2]},
                        {"title": "BrokerB", "pages": [3, 4]},
                    ]
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 503)
        created = list(Document.objects.filter(title__in=["BrokerA", "BrokerB"]))
        self.assertEqual(len(created), 2)  # Teile bleiben bestehen
        self.assertEqual(
            sorted(resp.data["document_ids"]), sorted(d.id for d in created)
        )
        # Beide neuen Versionen sind FAILED (retry-fähig), keine haengt in UPLOADED.
        for document in created:
            document.refresh_from_db()
            self.assertEqual(
                document.current_version.processing_state,
                DocumentVersion.ProcessingState.FAILED,
            )

    def test_rewrite_mit_veralteter_source_version_409(self):
        # P2: Basiert die Aktion auf einer inzwischen veralteten Version, wird sie
        # mit 409 abgelehnt (statt die parallele neue Version zu überschreiben).
        doc = self._doc("stale", self.user, pages=2)
        self.client.force_authenticate(self.user)

        with mock.patch("documents.views.process_document_version.delay"):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
                {"pages": [{"page": 1}], "source_version_id": 999999},
                format="json",
            )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(doc.versions.count(), 1)  # keine neue Version

    def test_rewrite_mit_korrekter_source_version_gelingt(self):
        doc = self._doc("fresh", self.user, pages=2)
        self.client.force_authenticate(self.user)

        with mock.patch("documents.views.process_document_version.delay"):
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
                {
                    "pages": [{"page": 1}],
                    "source_version_id": doc.current_version.id,
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 201, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.versions.count(), 2)

    def test_merge_limit_ohne_off_by_one(self):
        # P3: Bei MAX_DOCUMENTS=2 sind hoechstens 1 ZUSAETZLICHES Dokument erlaubt
        # (Ziel + 1 = 2). 2 zusaetzliche IDs -> schon der View lehnt ab (nicht erst
        # der Service nach Aufbau).
        from django.test import override_settings

        target = self._doc("ob-target", self.user, pages=1)
        a = self._doc("ob-a", self.user, pages=1)
        b = self._doc("ob-b", self.user, pages=1)
        self.client.force_authenticate(self.user)

        with override_settings(PDF_WORKBENCH_MAX_DOCUMENTS=2):
            with mock.patch("documents.views.process_document_version.delay"):
                ok = self.client.post(
                    f"/api/documents/{target.id}/pdf-workbench/merge/",
                    {"document_ids": [a.id], "source_version_id": target.current_version.id},
                    format="json",
                )
            self.assertEqual(ok.status_code, 201, ok.data)

            blocked = self.client.post(
                f"/api/documents/{target.id}/pdf-workbench/merge/",
                {"document_ids": [a.id, b.id], "source_version_id": target.current_version.id},
                format="json",
            )
            self.assertEqual(blocked.status_code, 400)
            self.assertIn("Zu viele", blocked.data["detail"])

    def test_merge_creates_new_version_and_respects_owner_scope(self):
        target = self._doc("merge-target", self.user, pages=2)
        appendix = self._doc("merge-appendix", self.user, pages=1)
        foreign = self._doc("merge-foreign", self.other, pages=1)
        self.client.force_authenticate(self.user)

        blocked = self.client.post(
            f"/api/documents/{target.id}/pdf-workbench/merge/",
            {"document_ids": [foreign.id], "source_version_id": target.current_version.id},
            format="json",
        )
        self.assertEqual(blocked.status_code, 404)

        with mock.patch("documents.views.process_document_version.delay") as delay:
            resp = self.client.post(
                f"/api/documents/{target.id}/pdf-workbench/merge/",
                {"document_ids": [appendix.id], "source_version_id": target.current_version.id},
                format="json",
            )

        self.assertEqual(resp.status_code, 201)
        target.refresh_from_db()
        delay.assert_called_once_with(target.current_version.id)
        with pikepdf.open(target.current_version.file_path) as pdf:
            self.assertEqual(len(pdf.pages), 3)
        self.assertTrue(
            AuditLogEntry.objects.filter(action="pdf_workbench_merge").exists()
        )

    def test_merge_payload_ueber_limit_wird_frueh_abgelehnt(self):
        # P1: Ein zu langer document_ids-Payload wird SOFORT (vor SQL-IN/Sort)
        # mit 400 abgelehnt – auch wenn die IDs gar nicht existieren.
        from django.test import override_settings

        target = self._doc("merge-guard", self.user, pages=1)
        self.client.force_authenticate(self.user)
        with override_settings(PDF_WORKBENCH_MAX_DOCUMENTS=1):
            resp = self.client.post(
                f"/api/documents/{target.id}/pdf-workbench/merge/",
                {"document_ids": [999999, 888888], "source_version_id": target.current_version.id},  # 2 > Limit 1, existieren nicht
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Zu viele", resp.data["detail"])

    def test_guest_cannot_write_pdf_workbench_actions(self):
        doc = self._doc("guest", self.guest, pages=2)
        self.client.force_authenticate(self.guest)

        resp = self.client.post(
            f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
            {"pages": [{"page": 1}], "source_version_id": doc.current_version.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 403)
