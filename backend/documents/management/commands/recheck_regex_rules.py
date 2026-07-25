"""Prüft alle Regel-/Trigger-Regexe gegen RE2 und meldet (optional deaktiviert)
die RE2-inkompatiblen (P2).

Ergänzt die einmalige Umstellungs-Migration (0064): nach Importen/Restores oder
manuellen DB-Änderungen lässt sich der Bestand hiermit erneut prüfen.

    python manage.py recheck_regex_rules            # nur berichten
    python manage.py recheck_regex_rules --disable  # ungültige deaktivieren
"""
from django.core.management.base import BaseCommand

from documents import regex_safe
from documents.models import AuditLogEntry, ClassificationRule, WorkflowTrigger


def _invalid(pattern) -> bool:
    if not pattern:
        return False
    try:
        regex_safe.compile_user_regex(str(pattern))
        return False
    except regex_safe.InvalidRegex:
        return True


class Command(BaseCommand):
    help = "Prüft Regel-/Trigger-Regexe gegen RE2; --disable deaktiviert ungültige."

    def add_arguments(self, parser):
        parser.add_argument(
            "--disable",
            action="store_true",
            help="RE2-inkompatible Regeln/Workflows deaktivieren (statt nur berichten).",
        )

    def handle(self, *args, **options):
        disable = options["disable"]
        invalid_rules = 0
        invalid_workflows = 0

        for rule in ClassificationRule.objects.all():
            match = rule.match if isinstance(rule.match, dict) else {}
            pattern = match.get("text_regex")
            if not _invalid(pattern):
                continue
            invalid_rules += 1
            self.stdout.write(
                f"Regel #{rule.id} '{rule.name}': RE2-inkompatibles text_regex "
                f"{pattern!r}{' (aktiv)' if rule.enabled else ' (bereits deaktiviert)'}"
            )
            if disable and rule.enabled:
                rule.enabled = False
                rule.save(update_fields=["enabled"])
                AuditLogEntry.objects.create(
                    action="rule_disabled_invalid_regex",
                    object_type="ClassificationRule",
                    object_id=str(rule.id),
                    detail={"reason": "RE2-inkompatibles text_regex", "pattern": pattern},
                )

        for trigger in WorkflowTrigger.objects.exclude(
            filter_text_regex=""
        ).select_related("workflow"):
            if not _invalid(trigger.filter_text_regex):
                continue
            invalid_workflows += 1
            workflow = trigger.workflow
            self.stdout.write(
                f"Workflow #{getattr(workflow, 'id', '?')} "
                f"'{getattr(workflow, 'name', '?')}': RE2-inkompatibles "
                f"filter_text_regex {trigger.filter_text_regex!r}"
            )
            if disable and workflow is not None and workflow.enabled:
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

        action = "deaktiviert" if disable else "gefunden"
        self.stdout.write(
            self.style.WARNING(
                f"{invalid_rules} Regel(n) und {invalid_workflows} Workflow(s) "
                f"mit RE2-inkompatiblem Regex {action}."
            )
            if (invalid_rules or invalid_workflows)
            else self.style.SUCCESS("Alle Regel-/Trigger-Regexe sind RE2-kompatibel.")
        )
