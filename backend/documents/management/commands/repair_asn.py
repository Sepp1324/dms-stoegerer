"""Repariert durch Fehl-Erkennung vergiftete ASNs.

Hintergrund: Eine per Fuzzy-OCR-Text falsch "erkannte" ASN wurde früher übernommen
und zog dabei den ``ASNCounter`` hoch (z. B. auf 19910). Dadurch bekamen alle
folgenden Dokumente absurd hohe Auto-Nummern. Diese Command setzt den Zähler auf
die höchste plausible ASN zurück und vergibt den betroffenen Dokumenten saubere,
fortlaufende Nummern.

    python manage.py repair_asn --dry-run     # nur anzeigen
    python manage.py repair_asn               # anwenden
    python manage.py repair_asn --threshold 5000
"""
from django.core.management.base import BaseCommand

from documents.models import ASNCounter, Document
from documents.services.asn import allocate_asn


class Command(BaseCommand):
    help = "Repariert vergiftete ASNs (Zähler zurücksetzen + betroffene Dokumente neu nummerieren)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--threshold",
            type=int,
            default=None,
            help="ASNs oberhalb dieses Werts gelten als vergiftet "
            "(Default: max(1000, 10 × Dokumentanzahl)).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur anzeigen, was passieren würde – keine Änderung.",
        )

    def handle(self, *args, **options):
        total = Document.objects.count()
        threshold = options["threshold"] or max(1000, total * 10)

        valid_max = (
            Document.objects.filter(asn__lte=threshold)
            .order_by("-asn")
            .values_list("asn", flat=True)
            .first()
            or 0
        )
        poisoned = list(Document.objects.filter(asn__gt=threshold).order_by("id"))

        self.stdout.write(
            f"Schwelle={threshold}, plausibles Maximum={valid_max}, "
            f"vergiftet={len(poisoned)}"
        )
        if not poisoned:
            self.stdout.write(self.style.SUCCESS("Nichts zu reparieren."))
            return

        if options["dry_run"]:
            nxt = valid_max
            for d in poisoned:
                nxt += 1
                self.stdout.write(
                    f"  [dry-run] #{d.id} ASN {d.asn} -> {nxt}  „{d.title}“"
                )
            return

        # Zähler auf plausibles Maximum zurücksetzen, dann lückenlos neu vergeben.
        counter, _ = ASNCounter.objects.get_or_create(pk=1, defaults={"last_value": 0})
        counter.last_value = valid_max
        counter.save(update_fields=["last_value"])

        for d in poisoned:
            new = allocate_asn()
            Document.objects.filter(pk=d.pk).update(asn=new)
            self.stdout.write(f"  #{d.id} ASN {d.asn} -> {new}  „{d.title}“")

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {len(poisoned)} Dokument(e) repariert, Zähler jetzt "
                f"{ASNCounter.objects.get(pk=1).last_value}."
            )
        )
