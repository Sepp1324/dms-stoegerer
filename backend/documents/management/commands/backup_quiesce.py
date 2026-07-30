"""Setzt/löst die Backup-Schreibsperre (P1).

Von der Backup-CronJob aufgerufen: ``--on`` VOR dem Snapshot/pg_dump, ``--off``
danach. Solange die Sperre aktiv ist, weisen Upload/Mobile-Capture/Version die
Aufnahme mit HTTP 503 ab und die Beat-Tasks (Consume/Mail) überspringen ihren
Lauf – so sehen /data-Snapshot und DB-Dump denselben Zustand.

Beispiele:
    python manage.py backup_quiesce --on --ttl 1800
    python manage.py backup_quiesce --off
    python manage.py backup_quiesce --status
"""
from django.core.management.base import BaseCommand, CommandError

from documents.services.quiesce import (
    DEFAULT_QUIESCE_TTL,
    is_quiesced,
    set_quiesce,
)


class Command(BaseCommand):
    help = "Backup-Schreibsperre aktivieren (--on), aufheben (--off) oder anzeigen (--status)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--on", action="store_true", help="Schreibsperre aktivieren.")
        group.add_argument("--off", action="store_true", help="Schreibsperre aufheben.")
        group.add_argument(
            "--status", action="store_true", help="Aktuellen Zustand ausgeben."
        )
        parser.add_argument(
            "--ttl",
            type=int,
            default=DEFAULT_QUIESCE_TTL,
            help=(
                "TTL der Sperre in Sekunden (Notbremse gegen ein hängendes Backup; "
                f"nur mit --on relevant, Default {DEFAULT_QUIESCE_TTL})."
            ),
        )

    def handle(self, *args, **opts):
        if opts["status"]:
            state = "AKTIV" if is_quiesced() else "inaktiv"
            self.stdout.write(f"Backup-Schreibsperre: {state}")
            return
        try:
            if opts["on"]:
                set_quiesce(True, ttl=opts["ttl"])
                self.stdout.write(
                    self.style.WARNING(
                        f"Backup-Schreibsperre AKTIVIERT (TTL {opts['ttl']}s)."
                    )
                )
            else:  # --off
                set_quiesce(False)
                self.stdout.write(self.style.SUCCESS("Backup-Schreibsperre AUFGEHOBEN."))
        except Exception as exc:  # noqa: BLE001 – Redis weg o. Ä. sauber melden
            raise CommandError(f"Quiesce-Umschaltung fehlgeschlagen: {exc}") from exc
