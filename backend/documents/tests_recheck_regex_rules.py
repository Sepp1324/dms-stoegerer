"""P2: Bestehende RE2-inkompatible Regel-/Trigger-Regexe werden erkannt und
(mit --disable) deaktiviert, statt still nur noch False zu liefern."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .models import (
    AuditLogEntry,
    ClassificationRule,
    Workflow,
    WorkflowTrigger,
)

# Lookahead: unter Python-``re`` gültig, unter RE2 nicht.
INVALID = r"(?=Rechnung)\d+"
VALID = r"SR-\d+"


class RecheckRegexRulesCommandTests(TestCase):
    def test_disable_deaktiviert_ungueltige_regel_und_auditiert(self):
        bad = ClassificationRule.objects.create(
            name="Ungueltig", enabled=True, match={"text_regex": INVALID}, then={}
        )
        good = ClassificationRule.objects.create(
            name="Gueltig", enabled=True, match={"text_regex": VALID}, then={}
        )

        out = StringIO()
        call_command("recheck_regex_rules", "--disable", stdout=out)

        bad.refresh_from_db()
        good.refresh_from_db()
        self.assertFalse(bad.enabled)          # ungueltig -> deaktiviert
        self.assertTrue(good.enabled)          # gueltig -> unveraendert
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="rule_disabled_invalid_regex",
                object_id=str(bad.id),
            ).exists()
        )

    def test_ohne_disable_nur_bericht_keine_aenderung(self):
        bad = ClassificationRule.objects.create(
            name="Ungueltig", enabled=True, match={"text_regex": INVALID}, then={}
        )
        out = StringIO()
        call_command("recheck_regex_rules", stdout=out)
        bad.refresh_from_db()
        self.assertTrue(bad.enabled)  # ohne --disable unveraendert
        self.assertIn("RE2-inkompatibles", out.getvalue())

    def test_disable_deaktiviert_workflow_mit_ungueltigem_trigger(self):
        wf = Workflow.objects.create(name="WF", order=1, enabled=True)
        WorkflowTrigger.objects.create(
            workflow=wf, trigger_type="document_added", filter_text_regex=INVALID
        )
        call_command("recheck_regex_rules", "--disable", stdout=StringIO())
        wf.refresh_from_db()
        self.assertFalse(wf.enabled)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="workflow_disabled_invalid_regex", object_id=str(wf.id)
            ).exists()
        )
