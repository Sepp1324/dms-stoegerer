"""P2: Workflow-Aktionen sind validiert und pro Workflow atomar."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers as drf_serializers

from .models import (
    AuditLogEntry,
    Correspondent,
    Document,
    Tag,
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

    def test_save_ueberschreibt_parallele_notizaenderung_nicht(self):
        # P1: Der Workflow ändert nur den Titel -> eine parallele Notizänderung darf
        # nicht durch ein save() ALLER Felder verloren gehen.
        self._workflow_with_action("Neuer Titel")
        doc = Document.objects.create(title="Alt", note="Nutzer-A", owner=self.user)
        wf_doc = Document.objects.get(pk=doc.pk)  # lädt note=Nutzer-A in memory
        Document.objects.filter(pk=doc.pk).update(note="Nutzer-B")  # parallele Änderung

        run_workflows(wf_doc, trigger_type="document_added", source="upload", text="")

        doc.refresh_from_db()
        self.assertEqual(doc.title, "Neuer Titel")  # Titel gesetzt
        self.assertEqual(doc.note, "Nutzer-B")       # Notiz NICHT überschrieben

    def test_rollback_refresht_dokument_fuer_naechsten_workflow(self):
        # P1: Eine Aktion mutiert das document in-memory, eine spätere scheitert ->
        # Rollback. Danach muss die in-memory-Instanz frisch aus der DB geladen sein,
        # sonst sähe der nächste Workflow verworfene Werte.
        corr = Correspondent.objects.create(name="Finanzamt")
        wf = Workflow.objects.create(name="A", order=10, enabled=True)
        WorkflowTrigger.objects.create(workflow=wf, trigger_type="document_added")
        WorkflowAction.objects.create(
            workflow=wf, order=10, action_type="assign", assign_correspondent=corr
        )
        WorkflowAction.objects.create(
            workflow=wf, order=20, action_type="assign", assign_title="{unknown}"
        )
        doc = Document.objects.create(title="D", owner=self.user)

        run_workflows(doc, trigger_type="document_added", source="upload", text="")

        # in-memory (nach refresh) UND DB: Korrespondent zurückgerollt.
        self.assertIsNone(doc.correspondent_id)
        doc.refresh_from_db()
        self.assertIsNone(doc.correspondent_id)

    def test_audit_sammelt_tags_mehrerer_aktionen(self):
        # P2: applied.update() würde tags_added der ersten Aktion überschreiben.
        t1 = Tag.objects.create(name="T1")
        t2 = Tag.objects.create(name="T2")
        wf = Workflow.objects.create(name="Tags", order=10, enabled=True)
        WorkflowTrigger.objects.create(workflow=wf, trigger_type="document_added")
        a1 = WorkflowAction.objects.create(workflow=wf, order=10, action_type="assign")
        a1.assign_tags.set([t1])
        a2 = WorkflowAction.objects.create(workflow=wf, order=20, action_type="assign")
        a2.assign_tags.set([t2])
        doc = Document.objects.create(title="D", owner=self.user)

        run_workflows(doc, trigger_type="document_added", source="upload", text="")

        audit = AuditLogEntry.objects.get(action="workflow", object_id=str(doc.id))
        self.assertEqual(
            sorted(audit.detail["applied"]["tags_added"]), ["T1", "T2"]
        )
