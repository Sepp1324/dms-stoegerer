"""P2: Rename/Löschen von Stammdaten aktualisiert den Suchindex der Dokumente."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Correspondent, Document, Tag

User = get_user_model()


class SearchIndexMasterDataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("idx", password="pw", role="user")
        self.doc = Document.objects.create(title="Beleg", owner=self.user)

    def test_tag_rename_reindiziert_dokumente(self):
        tag = Tag.objects.create(name="Alt")
        self.doc.tags.add(tag)
        with mock.patch(
            "documents.signals._refresh_search_vector"
        ) as refresh, self.captureOnCommitCallbacks(execute=True):
            tag.name = "Neu"
            tag.save()
        refresh.assert_any_call(self.doc.pk)

    def test_tag_delete_reindiziert_dokumente(self):
        tag = Tag.objects.create(name="Weg")
        self.doc.tags.add(tag)
        with mock.patch(
            "documents.signals._refresh_search_vector"
        ) as refresh, self.captureOnCommitCallbacks(execute=True):
            tag.delete()
        refresh.assert_any_call(self.doc.pk)

    def test_korrespondent_rename_reindiziert_dokumente(self):
        corr = Correspondent.objects.create(name="Finanzamt")
        self.doc.correspondent = corr
        self.doc.save(update_fields=["correspondent"])
        with mock.patch(
            "documents.signals._refresh_search_vector"
        ) as refresh, self.captureOnCommitCallbacks(execute=True):
            corr.name = "Finanzamt Wien"
            corr.save()
        refresh.assert_any_call(self.doc.pk)

    def test_korrespondent_delete_reindiziert_dokumente(self):
        corr = Correspondent.objects.create(name="Alt")
        self.doc.correspondent = corr
        self.doc.save(update_fields=["correspondent"])
        with mock.patch(
            "documents.signals._refresh_search_vector"
        ) as refresh, self.captureOnCommitCallbacks(execute=True):
            corr.delete()
        refresh.assert_any_call(self.doc.pk)
