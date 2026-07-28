"""Repariert durch Fehl-Erkennung vergiftete ASNs.

Hintergrund: Eine per Fuzzy-OCR-Text falsch "erkannte" ASN wurde früher übernommen
und zog dabei den ``ASNCounter`` hoch (z. B. auf 19910). Dadurch bekamen alle
folgenden Dokumente absurd hohe Auto-Nummern. Diese Command setzt den Zähler auf
die höchste plausible ASN zurück und vergibt den betroffenen Dokumenten saubere,
fortlaufende Nummern.

    python manage.py repair_asn --dry-run     # nur anzeigen
    python manage.py repair_asn --yes         # anwenden (Bestätigung nötig)
    python manage.py repair_asn --yes --threshold 5000

Atomar + auditiert (P2): Zähler-Reset und Neu-Nummerierung laufen in EINER
Transaktion unter Zählersperre – ein Abbruch oder ein paralleler Upload kann
keinen inkonsistenten Zählerstand hinterlassen. Die Reparatur wird als
``AuditLogEntry`` (action=``asn_repair``) mit allen Umbenennungen protokolliert.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from documents.models import ASNCounter, AuditLogEntry, Document


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
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Bestätigung – ohne diese Flag (und ohne --dry-run) wird nichts geändert.",
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

        if not options["yes"]:
            self.stderr.write(
                "Abbruch: --yes erforderlich (die Aktion ändert ASNs und den Zähler). "
                "Zum reinen Anzeigen: --dry-run."
            )
            return

        # ATOMAR unter Zählersperre (P2): Reset + Neu-Vergabe + Audit in EINER
        # Transaktion. Der select_for_update-Lock serialisiert gegen parallele
        # Uploads/Claims (die ebenfalls über den ASNCounter allozieren) – ein Abbruch
        # rollt ALLES zurück, es bleibt kein inkonsistenter Zählerstand. valid_max und
        # die Vergiftungsliste werden UNTER der Sperre neu bestimmt, damit keine
        # zwischenzeitliche Vergabe übersehen oder der Zähler zurückgeregelt wird.
        with transaction.atomic():
            counter = ASNCounter.objects.select_for_update().filter(pk=1).first()
            if counter is None:
                ASNCounter.objects.get_or_create(pk=1, defaults={"last_value": 0})
                counter = ASNCounter.objects.select_for_update().get(pk=1)

            valid_max = (
                Document.objects.filter(asn__lte=threshold)
                .order_by("-asn")
                .values_list("asn", flat=True)
                .first()
                or 0
            )
            poisoned = list(Document.objects.filter(asn__gt=threshold).order_by("id"))
            counter.last_value = valid_max

            remaps = []
            for d in poisoned:
                counter.last_value += 1
                new = counter.last_value
                Document.objects.filter(pk=d.pk).update(asn=new)
                remaps.append({"document_id": d.pk, "from": d.asn, "to": new})
                self.stdout.write(f"  #{d.id} ASN {d.asn} -> {new}  „{d.title}“")

            counter.save(update_fields=["last_value"])
            AuditLogEntry.objects.create(
                action="asn_repair",
                object_type="Document",
                object_id="",
                detail={
                    "threshold": threshold,
                    "valid_max": valid_max,
                    "count": len(remaps),
                    "remaps": remaps,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {len(poisoned)} Dokument(e) repariert, Zähler jetzt "
                f"{ASNCounter.objects.get(pk=1).last_value}."
            )
        )
