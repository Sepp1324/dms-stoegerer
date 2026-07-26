"""P1: Echter Nebenläufigkeitstest der systemweiten Lock-Reihenfolge.

``delete``, ``create_version_for_document`` (add_version) und ``seal_version``
sperren ALLE zuerst das Dokument und danach die Version (Document→Version). Zwei
Wettläufer auf demselben Dokument müssen daher von PostgreSQL sauber serialisiert
werden – OHNE Deadlock. Mit gegenläufiger Reihenfolge könnte hier ein Deadlock
(``OperationalError``) auftreten, und ``add_version`` könnte eine vom Löschen
nicht mehr erfasste Version einschieben.

Benötigt echte Zeilensperren -> nur unter PostgreSQL aussagekräftig; unter SQLite
(``FOR UPDATE`` ist dort ein No-op) wird der Test übersprungen.
"""
import os
import tempfile
import threading

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase

from . import pipeline
from .models import Document, DocumentVersion

User = get_user_model()


class DeleteAddVersionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Nebenläufigkeit nur unter PostgreSQL testbar")
        self.user = User.objects.create_user("conc", password="pw", role="user")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4 test")
        tmp.close()
        self.path = tmp.name
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

    def _seed_doc(self):
        doc = Document.objects.create(title="Race", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=self.path,
            sha256="a" * 64, mime_type="application/pdf", size=1, is_immutable=False,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_delete_und_add_version_deadlockfrei(self):
        doc = self._seed_doc()
        barrier = threading.Barrier(2, timeout=10)
        errors: dict[str, Exception] = {}

        def do_delete():
            try:
                barrier.wait()
                Document.objects.get(pk=doc.pk).delete()
            except Document.DoesNotExist:
                pass  # add_version hat evtl. gewonnen – das Dokument bleibt aber da;
                # DoesNotExist nur, falls delete zuerst lief (dann kein Konflikt).
            except Exception as exc:  # noqa: BLE001 – u. a. Deadlock hier einfangen
                errors["delete"] = exc
            finally:
                connection.close()

        def do_add():
            try:
                barrier.wait()
                target = Document.objects.get(pk=doc.pk)
                pipeline.create_version_for_document(
                    target, self.path, created_by=self.user
                )
            except Document.DoesNotExist:
                pass  # delete hat zuerst committet -> Dokument weg (zulässig).
            except Exception as exc:  # noqa: BLE001
                errors["add"] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=do_delete), threading.Thread(target=do_add)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)

        self.assertFalse(any(t.is_alive() for t in threads), "Thread hängt (Deadlock?)")
        self.assertEqual(errors, {}, f"Unerwarteter Fehler (evtl. Deadlock): {errors}")
