"""Unit-Tests für das lexikalische Dubletten-Signal (reine Funktionen).

Der token-basierte Fake-Embedder der Integrationstests koppelt Cosine und Lexik;
die eigentliche Trennung „echtes Duplikat vs. gleiche Vorlage" prüfen wir daher
direkt an den reinen Funktionen.
"""
from django.test import SimpleTestCase

from documents.services import duplicates


class LexicalSignalTests(SimpleTestCase):
    def test_lexical_similarity_jaccard(self):
        a = duplicates._normalized_tokens("Honorarnote Betrag 90 Euro Nummer 266")
        b = duplicates._normalized_tokens("Honorarnote Betrag 90 Euro Nummer 457")
        # 5 gemeinsame (honorarnote, betrag, 90, euro, nummer), 1 unterschiedlich je
        # Seite (266 / 457) → 5 / 7.
        self.assertAlmostEqual(duplicates._lexical_similarity(a, b), 5 / 7, places=3)

    def test_lexical_similarity_edge_cases(self):
        self.assertEqual(duplicates._lexical_similarity(set(), {"a"}), 0.0)
        toks = duplicates._normalized_tokens("gleicher Text")
        self.assertEqual(duplicates._lexical_similarity(toks, toks), 1.0)

    def test_classify_duplicate_needs_high_cosine_and_lexical(self):
        s = duplicates.STRONG_THRESHOLD
        # Hoher Cosine + nahezu identischer Text → echtes Duplikat.
        self.assertEqual(duplicates._classify(s + 0.005, 0.95), "duplicate")

    def test_classify_high_cosine_low_lexical_is_version(self):
        s = duplicates.STRONG_THRESHOLD
        # Semantisch fast gleich, Text weicht ab (wiederkehrende Rechnung) → Version.
        self.assertEqual(duplicates._classify(s + 0.005, 0.5), "version")

    def test_classify_mid_cosine_is_version(self):
        s = duplicates.STRONG_THRESHOLD
        # Unter der Strong-Schwelle bleibt es „mögliche Version", egal wie ähnlich der Text.
        self.assertEqual(duplicates._classify(s - 0.01, 0.99), "version")
