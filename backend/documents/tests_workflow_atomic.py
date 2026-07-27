"""P2: Workflow-Aktionen sind validiert und pro Workflow atomar."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers as drf_serializers

from .models import (
    AuditLogEntry,
    Document,
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
)
from .serializers import WorkflowActionSerializer
from .workflows import run_workflows

User = get_user_model()


class AssignTitleValidationTests(TestCase):
    def _s(self):
        return WorkflowActionSerializer()

    def test_unbekannter_platzhalter_abgelehnt(self):
        with self.assertRaises(drf_serializers.ValidationError):
            self._s().validate_assign_title("{unknown}")

    def test_kaputte_klammer_abgelehnt(self):
        with self.assertRaises(drf_serializers.ValidationError):
            self._s().validate_assign_title("{correspondent")

    def test_erlaubte_platzhalter_ok(self):
        value = "{correspondent} {created} {doc_type}"
        self.assertEqual(self._s().validate_assign_title(value), value)


class WorkflowAtomicTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("wfa", password="pw", role="user")

    def _workflow_with_action(self, assign_title):
        wf = Workflow.objects.create(name="WF", order=10, enabled=True)
        WorkflowTrigger.objects.create(workflow=wf, trigger_type="document_added")
        # Direkt am Modell erstellt (umgeht die Serializer-Validierung), um den
        # Laufzeit-Fehlerpfad der Engine zu prüfen.
        WorkflowAction.objects.create(
            workflow=wf, order=10, action_type="assign", assign_title=assign_title
        )
        return wf

    def test_fehlerhafte_aktion_rollt_zurueck_und_auditiert_failed(self):
        self._workflow_with_action("{unknown}")
        doc = Document.objects.create(title="Original", owner=self.user)

        run_workflows(doc, trigger_type="document_added", source="upload", text="")

        doc.refresh_from_db()
        self.assertEqual(doc.title, "Original")  # Rollback: Titel unverändert
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="workflow_failed", object_id=str(doc.id)
            ).exists()
        )
        self.assertFalse(
            AuditLogEntry.objects.filter(
                action="workflow", object_id=str(doc.id)
            ).exists()
        )

    def test_gueltige_aktion_wird_angewandt_und_auditiert(self):
        self._workflow_with_action("Fix {created}")
        doc = Document.objects.create(title="Original", owner=self.user)

        result = run_workflows(doc, trigger_type="document_added", source="upload", text="")

        self.assertIn("WF", result["workflows"])
        doc.refresh_from_db()
        self.assertTrue(doc.title.startswith("Fix "))
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="workflow", object_id=str(doc.id)
            ).exists()
        )
