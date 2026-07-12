"""Misst die echte Cosine-Ähnlichkeitsverteilung des Embedding-Bestands.

Alle semantischen Features (Suche, Auto-Ablage/Autopilot, Dubletten) hängen an
Schwellwerten, die per Default nur geschätzt sind. Dieses Command zeigt, wo die
tatsächlichen Nachbar-Ähnlichkeiten deines Bestands liegen – als Histogramm,
Perzentile und Beispiel-Paare je Band – damit die Schwellen aus Daten statt aus
dem Bauch gesetzt werden. Read-only, ändert nichts.

    python manage.py calibrate_embeddings
    python manage.py calibrate_embeddings --owner sepp --examples 5
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from documents.models import Document
from documents.services import duplicates

# Bänder von „identisch" nach „unähnlich". Obergrenze exklusiv.
BANDS = [
    (0.99, 1.01),
    (0.97, 0.99),
    (0.95, 0.97),
    (0.93, 0.95),
    (0.90, 0.93),
    (0.85, 0.90),
    (0.80, 0.85),
    (0.70, 0.80),
    (0.00, 0.70),
]


def _percentile(sorted_values, pct):
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round((pct / 100.0) * (len(sorted_values) - 1))))
    return sorted_values[idx]


class Command(BaseCommand):
    help = "Zeigt die Cosine-Ähnlichkeitsverteilung des Embedding-Bestands (Schwellen-Kalibrierung)."

    def add_arguments(self, parser):
        parser.add_argument("--owner", help="Nur Dokumente dieses Users (username, case-insensitiv).")
        parser.add_argument("--limit", type=int, default=0, help="Max. Anzahl Dokumente (0 = alle).")
        parser.add_argument("--examples", type=int, default=3, help="Beispiel-Paare je Band.")

    def handle(self, *args, **options):
        from ai import embeddings

        if not embeddings.enabled():
            self.stderr.write("Embeddings sind deaktiviert (EMBEDDING_ENABLED=false).")
            return

        qs = (
            Document.objects.filter(current_version__isnull=False)
            .exclude(current_version__ocr_text="")
            .select_related("current_version")
        )
        if options["owner"]:
            User = get_user_model()
            try:
                user = User.objects.get(username__iexact=options["owner"])
            except User.DoesNotExist:
                raise CommandError(f"Kein User mit username '{options['owner']}'.")
            qs = qs.filter(owner=user)

        docs = list(qs)
        if options["limit"] > 0:
            docs = docs[: options["limit"]]
        if len(docs) < 2:
            self.stdout.write("Zu wenige Dokumente mit Embeddings für eine Verteilung (>= 2 nötig).")
            return

        scores = []
        band_examples = {band: [] for band in BANDS}
        without_neighbor = 0
        without_embeddings = 0

        for doc in docs:
            res = duplicates.find_duplicates(doc, docs, threshold=0.0, limit=1)
            if res["status"] != "ok":
                without_embeddings += 1
                continue
            if not res["results"]:
                without_neighbor += 1
                continue
            hit = res["results"][0]
            score = hit["score"]
            scores.append(score)
            for band in BANDS:
                if band[0] <= score < band[1]:
                    if len(band_examples[band]) < options["examples"]:
                        band_examples[band].append((score, doc.title, hit["title"]))
                    break

        if not scores:
            self.stdout.write("Keine Nachbar-Ähnlichkeiten berechenbar (fehlen Embeddings? Backfill nötig).")
            return

        scores.sort(reverse=True)
        total = len(scores)
        peak = max(1, max(sum(1 for s in scores if b[0] <= s < b[1]) for b in BANDS))

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Nächste-Nachbar-Ähnlichkeit je Dokument ==="))
        self.stdout.write(
            f"Dokumente: {len(docs)}  |  ausgewertet: {total}  |  "
            f"ohne Embeddings: {without_embeddings}  |  ohne Nachbar: {without_neighbor}\n"
        )

        self.stdout.write("Histogramm (Anteil der Dokumente, deren nächster Nachbar in diesem Band liegt):")
        for band in BANDS:
            count = sum(1 for s in scores if band[0] <= s < band[1])
            bar = "█" * int(round((count / peak) * 40))
            label = f"{band[0]:.2f}–{min(band[1], 1.0):.2f}"
            self.stdout.write(f"  {label}  {bar} {count} ({count / total * 100:4.1f}%)")

        self.stdout.write("\nPerzentile der Nachbar-Ähnlichkeit:")
        asc = list(reversed(scores))
        for pct in (50, 75, 90, 95, 99):
            self.stdout.write(f"  p{pct}: {_percentile(asc, pct):.3f}")
        self.stdout.write(
            f"  min: {scores[-1]:.3f}  max: {scores[0]:.3f}  mean: {sum(scores) / total:.3f}"
        )

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Beispiel-Paare je Band (zum Augenschein) ==="))
        for band in BANDS:
            examples = band_examples[band]
            if not examples:
                continue
            self.stdout.write(f"\n[{band[0]:.2f}–{min(band[1], 1.0):.2f}]")
            for score, a_title, b_title in examples:
                self.stdout.write(f"  {score:.3f}  „{a_title}\"  ↔  „{b_title}\"")

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Aktuelle Schwellen ==="))
        self.stdout.write(f"  EMBEDDING_MIN_SIMILARITY   = {settings.EMBEDDING_MIN_SIMILARITY}")
        self.stdout.write(f"  AUTO_FILE_MIN_CONFIDENCE   = {settings.AUTO_FILE_MIN_CONFIDENCE}")
        self.stdout.write(f"  DUPLICATE_THRESHOLD        = {settings.DUPLICATE_THRESHOLD}")
        self.stdout.write(f"  DUPLICATE_STRONG_THRESHOLD = {settings.DUPLICATE_STRONG_THRESHOLD}")
        self.stdout.write(
            "\nLesehilfe: Prüfe die Beispiel-Paare von oben nach unten. Setz "
            "DUPLICATE_STRONG_THRESHOLD an die Grenze, ab der Paare wirklich DASSELBE "
            "Dokument sind; DUPLICATE_THRESHOLD dorthin, wo sie noch klar verwandt sind. "
            "Für die Bedeutungssuche ist EMBEDDING_MIN_SIMILARITY meist etwas unter dem "
            "p50 sinnvoll (Recall vor Präzision)."
        )
