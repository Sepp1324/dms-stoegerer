"""P1: Nutzerdefinierte Regexe laufen ReDoS-sicher über RE2.

Deckt ab:
  * regex_safe.search: korrektes Matching, case-insensitive, Textdeckelung,
    fail-closed bei ungültigem Muster, und – der Kern – katastrophales
    Backtracking terminiert sofort (RE2 ist linear).
  * rule_matches nutzt denselben Pfad (und damit auch Workflow-Trigger).
  * Save-Time-Validierung lehnt RE2-inkompatible Muster ab.
"""
from django.test import TestCase

from documents import regex_safe
from documents.classification import rule_matches
from documents.serializers import (
    ClassificationRuleSerializer,
    WorkflowTriggerSerializer,
)


class _Rule:
    def __init__(self, match):
        self.match = match


class RegexSafeTests(TestCase):
    def test_search_matcht_gueltiges_muster(self):
        self.assertTrue(regex_safe.search(r"SR-\d+", "Rechnung SR-1234 vom Montag"))
        self.assertFalse(regex_safe.search(r"SR-\d+", "keine Nummer hier"))

    def test_case_insensitive(self):
        self.assertTrue(regex_safe.search(r"rechnung", "Große RECHNUNG"))

    def test_katastrophales_backtracking_terminiert_sofort(self):
        # Mit dem backtracking-basierten ``re`` würde (a+)+$ auf dieser langen
        # Nicht-Treffer-Eingabe praktisch endlos laufen (ReDoS). RE2 ist linear
        # -> terminiert sofort und liefert False. Bliebe es beim alten re.search,
        # liefe dieser Test ins CI-Timeout.
        payload = "a" * 5000 + "!"
        self.assertFalse(regex_safe.search(r"(a+)+$", payload))

    def test_ungueltiges_muster_faellt_geschlossen_auf_false(self):
        # Backreference wird von RE2 nicht unterstützt -> kein Crash, kein Treffer.
        self.assertFalse(regex_safe.search(r"(a)\1", "aa"))

    def test_compile_user_regex_lehnt_backreference_ab(self):
        with self.assertRaises(regex_safe.InvalidRegex):
            regex_safe.compile_user_regex(r"(a)\1")

    def test_text_wird_gedeckelt(self):
        # Treffer erst jenseits der Deckelung -> wird bewusst nicht gefunden.
        text = "x" * (regex_safe.MAX_TEXT_CHARS + 10) + "NADEL"
        self.assertFalse(regex_safe.search("NADEL", text))
        # Treffer innerhalb der Deckelung wird gefunden.
        self.assertTrue(regex_safe.search("NADEL", "NADEL" + "x" * 100))


class RuleMatchesRegexTests(TestCase):
    def test_regex_bedingung_ueber_re2(self):
        rule = _Rule({"text_regex": r"vertrag\s+\d+"})
        self.assertTrue(rule_matches(rule, "mein vertrag 42 laeuft"))
        self.assertFalse(rule_matches(rule, "kein treffer"))

    def test_regex_backtracking_bombe_terminiert(self):
        rule = _Rule({"text_regex": r"(a+)+$"})
        self.assertFalse(rule_matches(rule, "a" * 5000 + "!"))


class RuleSerializerRegexValidationTests(TestCase):
    def test_lehnt_re2_inkompatibles_muster_ab(self):
        serializer = ClassificationRuleSerializer(
            data={"name": "R", "match": {"text_regex": r"(a)\1"}, "then": {}}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("match", serializer.errors)

    def test_akzeptiert_gueltiges_muster(self):
        serializer = ClassificationRuleSerializer(
            data={"name": "R", "match": {"text_regex": r"SR-\d+"}, "then": {}}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)


class WorkflowTriggerRegexValidationTests(TestCase):
    def test_lehnt_re2_inkompatibles_muster_ab(self):
        serializer = WorkflowTriggerSerializer(
            data={"trigger_type": "document_added", "filter_text_regex": r"(a)\1"}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("filter_text_regex", serializer.errors)

    def test_akzeptiert_gueltiges_muster(self):
        serializer = WorkflowTriggerSerializer(
            data={"trigger_type": "document_added", "filter_text_regex": r"SR-\d+"}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
