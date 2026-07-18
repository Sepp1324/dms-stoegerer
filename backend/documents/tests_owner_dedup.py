"""Tests: Hash-Dedup ist owner-scoped (P1, Multi-Tenant-Korrektheit).

Regression gegen tenant-übergreifenden Datenverlust: Vor dem Fix unterdrückte
der globale SHA-256-Dedup den Import eines Nutzers, sobald ein anderer Nutzer
zufällig denselben Inhalt besaß.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import pipeline
from .models import Document, DocumentVersion

SHA = "a" * 64


def _make_version(owner, sha=SHA):
    doc = Document.objects.create(title="Doc", owner=owner)
    return DocumentVersion.objects.create(
        document=doc, version_no=1, file_path="/tmp/x", sha256=sha
    )


class OwnerScopedDedupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user(username="alice", password="pw12345!")
        self.bob = User.objects.create_user(username="bob", password="pw12345!")

    def test_same_hash_other_owner_is_not_a_duplicate(self):
        _make_version(self.alice)
        # Bob hat denselben Inhalt noch nicht → kein Duplikat für Bob.
        self.assertIsNone(pipeline.find_duplicate_version(SHA, owner=self.bob))

    def test_same_hash_same_owner_is_a_duplicate(self):
        v = _make_version(self.alice)
        found = pipeline.find_duplicate_version(SHA, owner=self.alice)
        self.assertEqual(found, v)

    def test_none_owner_scopes_to_triage_pool(self):
        _make_version(self.alice)
        # owner=None trifft nur owner-lose (Triage-)Dokumente, nicht Alice.
        self.assertIsNone(pipeline.find_duplicate_version(SHA, owner=None))
        v = _make_version(None)
        self.assertEqual(pipeline.find_duplicate_version(SHA, owner=None), v)

    def test_without_owner_arg_stays_global(self):
        # Abwärtskompatibel: ohne owner-Scope wird global gefunden.
        _make_version(self.alice)
        self.assertIsNotNone(pipeline.find_duplicate_version(SHA))

    def test_empty_hash_returns_none(self):
        self.assertIsNone(pipeline.find_duplicate_version("", owner=self.alice))
