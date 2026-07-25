"""Deaktiviert bestehende Regel-/Trigger-Regexe, die RE2 nicht unterstützt (P2).

Mit dem Wechsel auf RE2 (ReDoS-Schutz) werden NEUE Muster beim Speichern geprüft.
Bereits gespeicherte Muster mit Lookarounds/Backreferences (unter ``re`` gültig,
unter RE2 nicht) blieben aber aktiviert und liefern seither still nur noch
``False`` – die Regel „wirkt" also scheinbar, matcht aber nie mehr.

Diese Datenmigration deaktiviert solche Regeln/Workflows EINMAL zum Umstellungs-
zeitpunkt (in der UI als deaktiviert sichtbar) und protokolliert das im Audit-Log.
Ein späteres erneutes Prüfen ist über das Management-Command ``recheck_regex_rules``
möglich. Nicht umkehrbar (das stille Fehlverhalten soll nicht zurückkehren).
"""
from django.db import migrations


def _re2_invalid(pattern) -> bool:
    if not pattern:
        return False
    from documents import regex_safe

    try:
        regex_safe.compile_user_regex(str(pattern))
        return False
    except regex_safe.InvalidRegex:
        return True


def disable_invalid_regexes(apps, schema_editor):
    ClassificationRule = apps.get_model("documents", "ClassificationRule")
    WorkflowTrigger = apps.get_model("documents", "WorkflowTrigger")
    AuditLogEntry = apps.get_model("documents", "AuditLogEntry")

    for rule in ClassificationRule.objects.filter(enabled=True):
        match = rule.match if isinstance(rule.match, dict) else {}
        pattern = match.get("text_regex")
        if _re2_invalid(pattern):
            rule.enabled = False
            rule.save(update_fields=["enabled"])
            AuditLogEntry.objects.create(
                action="rule_disabled_invalid_regex",
                object_type="ClassificationRule",
                object_id=str(rule.id),
                detail={"reason": "RE2-inkompatibles text_regex", "pattern": pattern},
            )

    for trigger in WorkflowTrigger.objects.exclude(filter_text_regex="").select_related(
        "workflow"
    ):
        if _re2_invalid(trigger.filter_text_regex):
            workflow = trigger.workflow
            if workflow is not None and workflow.enabled:
                workflow.enabled = False
                workflow.save(update_fields=["enabled"])
                AuditLogEntry.objects.create(
                    action="workflow_disabled_invalid_regex",
                    object_type="Workflow",
                    object_id=str(workflow.id),
                    detail={
                        "reason": "RE2-inkompatibles filter_text_regex",
                        "pattern": trigger.filter_text_regex,
                    },
                )


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0063_clear_case_file_owner_mismatch"),
    ]

    operations = [
        migrations.RunPython(disable_invalid_regexes, migrations.RunPython.noop),
    ]
