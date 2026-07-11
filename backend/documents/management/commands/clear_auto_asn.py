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

from documents.models import ASNCounter, Document


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

        Document.objects.exclude(asn__isnull=True).update(asn=None)
        ASNCounter.objects.update_or_create(pk=1, defaults={"last_value": 0})
        self.stdout.write(
            self.style.SUCCESS(f"{count} ASN(s) geleert, ASNCounter auf 0 gesetzt.")
        )
