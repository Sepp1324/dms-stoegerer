"""P2: Die Regel-API erzwingt ein striktes Schema für match/then, und
rule_matches ist robust gegen ein nicht-dict ``match`` (kein AttributeError)."""
from django.test import TestCase

from documents.classification import rule_matches
from documents.serializers import ClassificationRuleSerializer


class _Rule:
    def __init__(self, match):
        self.match = match


class RuleMatchRobustnessTests(TestCase):
    def test_nicht_dict_match_bricht_klassifizierung_nicht_ab(self):
        # Liste statt Dict -> darf KEIN AttributeError werfen, sondern kein Treffer.
        self.assertFalse(rule_matches(_Rule(["steuer"]), "steuer text"))
        self.assertFalse(rule_matches(_Rule("steuer"), "steuer text"))
        self.assertFalse(rule_matches(_Rule(None), "steuer text"))


class RuleMatchSchemaTests(TestCase):
    def _valid(self, **over):
        data = {"name": "R", "match": {"text_contains": ["x"]}, "then": {}}
        data.update(over)
        return ClassificationRuleSerializer(data=data)

    def test_match_muss_objekt_sein(self):
        self.assertFalse(self._valid(match=["x"]).is_valid())

    def test_unbekanntes_match_feld_wird_abgelehnt(self):
        s = self._valid(match={"text_contains": ["x"], "boom": 1})
        self.assertFalse(s.is_valid())
        self.assertIn("match", s.errors)

    def test_term_liste_mit_nicht_string_wird_abgelehnt(self):
        s = self._valid(match={"text_contains": ["ok", 5]})
        self.assertFalse(s.is_valid())

    def test_text_regex_muss_string_sein(self):
        self.assertFalse(self._valid(match={"text_regex": ["x"]}).is_valid())

    def test_gueltiges_match_wird_akzeptiert(self):
        s = self._valid(
            match={"text_contains": "steuer", "text_regex": r"SR-\d+"}
        )
        self.assertTrue(s.is_valid(), s.errors)


class RuleThenSchemaTests(TestCase):
    def _valid(self, then):
        return ClassificationRuleSerializer(
            data={"name": "R", "match": {"text_contains": ["x"]}, "then": then}
        )

    def test_then_muss_objekt_sein(self):
        self.assertFalse(self._valid(["document_type"]).is_valid())

    def test_unbekanntes_then_feld_wird_abgelehnt(self):
        s = self._valid({"boom": "x"})
        self.assertFalse(s.is_valid())
        self.assertIn("then", s.errors)

    def test_tags_string_wird_abgelehnt(self):
        # Kein zeichenweises "steuer" mehr – die API weist das jetzt ab.
        s = self._valid({"tags": "steuer"})
        self.assertFalse(s.is_valid())

    def test_zu_viele_tags_werden_abgelehnt(self):
        s = self._valid({"tags": [f"t{i}" for i in range(51)]})
        self.assertFalse(s.is_valid())

    def test_document_type_muss_string_sein(self):
        self.assertFalse(self._valid({"document_type": ["Rechnung"]}).is_valid())

    def test_gueltiges_then_wird_akzeptiert(self):
        s = self._valid({"document_type": "Rechnung", "tags": ["Finanzen"]})
        self.assertTrue(s.is_valid(), s.errors)
