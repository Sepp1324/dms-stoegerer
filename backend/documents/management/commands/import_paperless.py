"""Importiert einen paperless-ngx-Export in das DMS (STOAA-327/329).

Quelle ist ein Verzeichnis, das der paperless-ngx ``document_exporter`` erzeugt
hat: eine ``manifest.json`` plus die Originaldateien. Je Dokument wird die
Originaldatei über die bestehende Pipeline aufgenommen
(``create_document_from_file`` + ``process_version``), sodass OCR, Thumbnails
und Klassifizierung wie bei consume/upload laufen.

    python manage.py import_paperless --source <export-dir> --owner <username>

Optionen:
    --dry-run     nur anzeigen, keine DB-/Datei-Änderung
    --with-tags   Tags aus dem Export mit importieren (default: aus)

Idempotenz: Der Inhalts-SHA-256 dedupliziert (dieselbe Hash-Quelle wie die
Pipeline). Ein zweiter Lauf über denselben Export legt 0 neue Dokumente an.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime

from documents import pipeline, storage
from documents.models import Correspondent, DocumentType, Tag


class Command(BaseCommand):
    help = "Importiert einen paperless-ngx-Export (manifest.json + Dateien) ins DMS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=True,
            help="Verzeichnis des paperless-ngx-Exports (enthält manifest.json).",
        )
        parser.add_argument(
            "--owner",
            required=True,
            help="Benutzername, dem die importierten Dokumente gehören.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur anzeigen, was importiert würde – keine DB-/Datei-Änderung.",
        )
        parser.add_argument(
            "--with-tags",
            action="store_true",
            help="Tags aus dem Export mit importieren (default: aus).",
        )

    def handle(self, *args, **options):
        source = Path(options["source"]).expanduser()
        dry_run = options["dry_run"]
        with_tags = options["with_tags"]

        # Owner-Auflösung: --owner ist ein Benutzername. Unbekannt -> Fehler.
        User = get_user_model()
        try:
            owner = User.objects.get(username=options["owner"])
        except User.DoesNotExist:
            raise CommandError(f"Unbekannter Benutzer (--owner): {options['owner']!r}")

        manifest_path = source / "manifest.json"
        if not manifest_path.is_file():
            raise CommandError(f"manifest.json nicht gefunden unter {manifest_path}")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"manifest.json nicht lesbar: {exc}")

        if not isinstance(manifest, list):
            raise CommandError("manifest.json hat kein erwartetes Format (Liste von Objekten).")

        # pk -> Name Maps für die referenzierten Stammdaten aus dem Export.
        correspondents = _name_map(manifest, "documents.correspondent")
        document_types = _name_map(manifest, "documents.documenttype")
        tags = _name_map(manifest, "documents.tag")

        docs = [e for e in manifest if e.get("model") == "documents.document"]
        if not docs:
            self.stdout.write(self.style.WARNING("Keine documents.document-Einträge im Manifest."))

        imported = 0
        skipped = 0
        failed = 0

        for entry in docs:
            fields = entry.get("fields", {}) or {}
            title = (fields.get("title") or "").strip() or "Ohne Titel"
            rel = entry.get("__exported_file_name__") or fields.get("original_filename")
            if not rel:
                failed += 1
                self.stderr.write(f"  FEHLER „{title}“: keine Originaldatei im Manifest-Eintrag.")
                continue

            src_file = (source / rel)
            if not src_file.is_file():
                failed += 1
                self.stderr.write(f"  FEHLER „{title}“: Datei fehlt: {rel}")
                continue

            try:
                sha = pipeline.sha256_of(src_file)
            except OSError as exc:
                failed += 1
                self.stderr.write(f"  FEHLER „{title}“: Hash fehlgeschlagen: {exc}")
                continue

            # Idempotenz: identischer Inhalt bereits vorhanden -> überspringen.
            # Owner-scoped (P1): nur im Bestand des Ziel-Owners (--owner) suchen,
            # damit ein gleicher Inhalt bei einem anderen Nutzer den Import nicht
            # blockiert.
            if pipeline.find_duplicate_version(sha, owner=owner):
                skipped += 1
                self.stdout.write(f"  übersprungen (dedup) „{title}“")
                continue

            corr_name = correspondents.get(fields.get("correspondent"))
            dtype_name = document_types.get(fields.get("document_type"))
            created = _parse_created(fields.get("created"))
            tag_names = (
                [tags[t] for t in (fields.get("tags") or []) if t in tags]
                if with_tags
                else []
            )

            if dry_run:
                imported += 1
                extra = []
                if corr_name:
                    extra.append(f"corr={corr_name}")
                if dtype_name:
                    extra.append(f"typ={dtype_name}")
                if tag_names:
                    extra.append(f"tags={','.join(tag_names)}")
                self.stdout.write(
                    f"  [dry-run] würde importieren „{title}“"
                    + (f" ({'; '.join(extra)})" if extra else "")
                )
                continue

            try:
                self._import_one(
                    src_file=src_file,
                    sha=sha,
                    title=title,
                    owner=owner,
                    created=created,
                    corr_name=corr_name,
                    dtype_name=dtype_name,
                    tag_names=tag_names,
                )
                imported += 1
                self.stdout.write(f"  importiert „{title}“")
            except Exception as exc:  # pragma: no cover - defensiv pro Datei
                failed += 1
                self.stderr.write(f"  FEHLER „{title}“: {exc}")

        summary = f"Fertig: {imported} importiert, {skipped} übersprungen, {failed} fehlgeschlagen."
        if dry_run:
            summary = "[dry-run] " + summary
        self.stdout.write(self.style.SUCCESS(summary))

    def _import_one(
        self,
        *,
        src_file: Path,
        sha: str,
        title: str,
        owner,
        created,
        corr_name,
        dtype_name,
        tag_names,
    ):
        """Legt ein Dokument aus einer Originaldatei an und stößt die Pipeline an."""
        # Original in den storage-Bereich kopieren (wie consume/mail), damit die
        # Quelle unberührt bleibt und die Pipeline auf einer eigenen Datei arbeitet.
        data = src_file.read_bytes()
        target, detected_mime = storage.save_bytes(data, src_file.suffix)

        with transaction.atomic():
            document, version = pipeline.create_document_from_file(
                str(target),
                title=title,
                owner=owner,
                mime=detected_mime,
                size=len(data),
                ingest_source="paperless_import",
            )
            # Hash sofort setzen, damit weitere identische Dateien im selben Lauf
            # zuverlässig dedupliziert werden (die Pipeline berechnet ihn später
            # aus der Datei erneut – identischer Wert).
            version.sha256 = sha
            version.save(update_fields=["sha256"])

            update_fields = []
            if created is not None:
                document.created_at = created
                update_fields.append("created_at")
            if corr_name:
                document.correspondent = Correspondent.objects.get_or_create(name=corr_name)[0]
                update_fields.append("correspondent")
            if dtype_name:
                document.document_type = DocumentType.objects.get_or_create(name=dtype_name)[0]
                update_fields.append("document_type")
            if update_fields:
                document.save(update_fields=update_fields)
            if tag_names:
                tag_objs = [
                    Tag.objects.get_or_create(name=name, parent=None)[0] for name in tag_names
                ]
                document.tags.set(tag_objs)

        # Nachgelagerte Verarbeitung (OCR/Thumbnail/Klassifizierung) synchron –
        # ein Management-Command läuft nicht zwingend mit Celery-Worker.
        pipeline.process_version(version)


def _name_map(manifest, model_label):
    """pk -> fields.name für alle Einträge eines Modells im Manifest."""
    out = {}
    for entry in manifest:
        if entry.get("model") == model_label:
            name = (entry.get("fields", {}) or {}).get("name")
            if entry.get("pk") is not None and name:
                out[entry["pk"]] = name
    return out


def _parse_created(value):
    """paperless liefert ``created`` als ISO-Datetime oder Datum."""
    if not value:
        return None
    return parse_datetime(value) or parse_date(value)
