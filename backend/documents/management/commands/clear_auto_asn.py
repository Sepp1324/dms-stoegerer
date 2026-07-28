"""Leert alle vorhandenen ASNs (Sticker-only-Umstieg).

Nach dem Wechsel auf das Sticker-only-Modell tragen Alt-Dokumente noch ihre früher
automatisch vergebenen ASNs. Diese Command entfernt sie (setzt ``asn = None``) und
setzt den ``ASNCounter`` auf 0 zurück, damit die aufgeklebten Sticker-Nummern frei
sind und beim (Re-)Scan sauber übernommen werden.

ZERSTÖREND – erfordert ``--yes``:

    python manage.py clear_auto_asn --dry-run
    python manage.py clear_auto_asn --yes
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from documents.models import ASNCounter, AuditLogEntry, Document


class Command(BaseCommand):
    help = "Leert alle ASNs + setzt den ASNCounter auf 0 (Sticker-only-Umstieg, zerstörend)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Bestätigung – ohne diese Flag wird nichts geändert.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur anzeigen, wie viele ASNs geleert würden.",
        )

    def handle(self, *args, **options):
        count = Document.objects.exclude(asn__isnull=True).count()

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] würde {count} ASN(s) leeren und den Zähler auf 0 setzen."
            )
            return

        if not options["yes"]:
            self.stderr.write(
                "Abbruch: --yes erforderlich (die Aktion ist zerstörend)."
            )
            return

        # ATOMAR + auditiert (P2): ASNs leeren, Zähler unter Sperre auf 0 setzen und
        # den Vorgang protokollieren – in EINER Transaktion. Der Zählerlock
        # serialisiert gegen parallele Vergaben; ein Abbruch rollt alles zurück.
        with transaction.atomic():
            counter = ASNCounter.objects.select_for_update().filter(pk=1).first()
            cleared = list(
                Document.objects.exclude(asn__isnull=True).values_list("pk", "asn")
            )
            Document.objects.exclude(asn__isnull=True).update(asn=None)
            if counter is None:
                ASNCounter.objects.create(pk=1, last_value=0)
            else:
                counter.last_value = 0
                counter.save(update_fields=["last_value"])
            AuditLogEntry.objects.create(
                action="asn_clear_all",
                object_type="Document",
                object_id="",
                detail={
                    "count": len(cleared),
                    "cleared": [
                        {"document_id": pk, "from": asn} for pk, asn in cleared
                    ],
                },
            )
        self.stdout.write(
            self.style.SUCCESS(f"{count} ASN(s) geleert, ASNCounter auf 0 gesetzt.")
        )
