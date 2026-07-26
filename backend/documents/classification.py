"""Regelbasierte Klassifizierung (deterministisch, erklärbar).

Läuft nach dem OCR in der Pipeline. Anders als die KI (Vorschläge zum
Bestätigen) setzen Regeln Metadaten **direkt** – nachvollziehbar über ein
Audit-Log und das Feld ``Document.classification``.

Regel-Schema (``ClassificationRule``):
  match: {"text_contains": ["Rechnung", "Invoice"], "text_regex": "SR-\\d+",
          "subject_contains": ["Rechnung"], "from_contains": ["@stadtwerke.de"]}
         – mehrere Bedingungen werden UND-verknüpft; eine Wortliste bei
           text_contains/subject_contains/from_contains ist ODER-verknüpft
           (irgendeines enthalten). ``subject_contains``/``from_contains``
           matchen auf Betreff bzw. Absender der Quell-E-Mail (bei IMAP-Ingest
           an ``Document.mail_subject``/``mail_sender`` hinterlegt); leer/fehlend
           bedeutet keine Bedingung (rückwärtskompatibel zu reinen Text-Regeln).
  then:  {"document_type": "Rechnung", "correspondent": "Stadtwerke",
          "tags": ["Finanzen"], "storage_path": "Rechnungen",
          "folder": "Versicherungen / Wüstenrot"}
         – Einzelwerte (Typ/Korrespondent/Ablagepfad) werden nur gesetzt, wenn
           noch nicht belegt; Tags werden ergänzt.
"""
from __future__ import annotations

import logging

from django.db import transaction

from . import regex_safe

logger = logging.getLogger(__name__)

# Obergrenze für aus ``then`` übernommene Namen (deckt die Model-max_length ab).
_MAX_THEN_FIELD_LEN = 255


def _then_text(then: dict, key: str) -> str:
    """Ein Einzelwert aus ``then`` – NUR als String akzeptiert, getrimmt/gekappt.

    Robust gegen fehlerhafte JSON-Daten: eine Liste/ein Dict würde von ``str()``
    zu Müll (z. B. ``"['x']"``); solche Werte werden verworfen statt gespeichert.
    """
    value = then.get(key)
    if not value:
        return ""
    if not isinstance(value, str):
        logger.warning(
            "Regel-then[%s] ist kein String (%s) – ignoriert.", key, type(value).__name__
        )
        return ""
    return value.strip()[:_MAX_THEN_FIELD_LEN]


def _then_tags(then: dict) -> list[str]:
    """Die Tag-Liste aus ``then`` – NUR eine echte Liste von Strings.

    Verhindert das zeichenweise Zerlegen eines Strings: ``tags: "steuer"`` würde
    sonst zu den Tags ``s, t, e, u, e, r``. Nicht-Listen werden verworfen.
    """
    raw = then.get("tags")
    if not raw:
        return []
    if not isinstance(raw, list):
        logger.warning(
            "Regel-then[tags] ist keine Liste (%s) – ignoriert.", type(raw).__name__
        )
        return []
    tags: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()[:_MAX_THEN_FIELD_LEN]
            if name and name not in tags:
                tags.append(name)
    return tags


def _searchable_text(document) -> str:
    parts = [document.title or ""]
    version = document.current_version
    if version and version.ocr_text:
        parts.append(version.ocr_text)
    return " ".join(parts).lower()


def _contains_any(match, key: str, haystack: str) -> bool | None:
    """ODER-verknüpfte Wortliste gegen ``haystack`` (bereits lowercase).

    Rückgabe ``None`` = Feld leer/fehlend → keine Bedingung; sonst Trefferstatus.
    """
    raw = match.get(key)
    if not raw:
        return None
    needles = [
        str(n).lower()
        for n in (raw if isinstance(raw, list) else [raw])
        if str(n).strip()
    ]
    if not needles:
        return None
    return any(n in haystack for n in needles)


def rule_matches(rule, text: str, *, subject: str = "", sender: str = "") -> bool:
    # Defensiv: ``match`` MUSS ein Dict sein. Ein per Fixture/Alt-DB gespeichertes
    # ``match`` als Liste/Skalar (``or {}`` fängt nur falsy) würde sonst unten an
    # ``.get`` mit AttributeError die ganze Klassifizierung abbrechen.
    match = rule.match if isinstance(rule.match, dict) else {}
    checks = []

    for key, haystack in (
        ("text_contains", text),
        ("subject_contains", (subject or "").lower()),
        ("from_contains", (sender or "").lower()),
    ):
        result = _contains_any(match, key, haystack)
        if result is not None:
            checks.append(result)

    regex = match.get("text_regex")
    if regex:
        # ReDoS-Schutz (P1): nutzerdefiniertes Muster über RE2 (lineare Laufzeit,
        # kein Backtracking), Text gedeckelt. Siehe documents.regex_safe.
        checks.append(regex_safe.search(regex, text))

    # Ohne erkannte Bedingung greift die Regel nicht (verhindert Alles-Treffer).
    return bool(checks) and all(checks)


def _get_or_create_folder(path: str, owner):
    """Legt einen fachlichen Ordnerpfad wie ``Akte / Unterordner`` an – für den
    Eigentümer des Dokuments.

    Regeln speichern bewusst Namen statt IDs: So bleiben sie lesbar, exportierbar
    und über Umgebungen hinweg stabil. Leere Pfadsegmente werden ignoriert.

    Owner-Scope (P1): Der Ordnerbaum darf pro Eigentümer denselben Namen mehrfach
    tragen. Ohne ``owner`` im Lookup wäre die Suche mehrdeutig (``Steuer`` für Alice
    UND Bob → ``MultipleObjectsReturned``, Dokument bliebe in CLASSIFICATION_RUNNING)
    oder ein Treffer würde ein Dokument einem FREMDEN Ordner zuweisen. Deshalb wird
    der komplette Pfad owner-gebunden aufgelöst/angelegt (jedes Segment mit
    ``owner``); so bleibt der Baum single-owner und eindeutig.
    """
    from .models import DocumentFolder

    parts = [part.strip() for part in path.split("/") if part.strip()]
    parent = None
    folder = None
    for name in parts:
        folder, _ = DocumentFolder.objects.get_or_create(
            name=name, parent=parent, owner=owner
        )
        parent = folder
    return folder


def assign_folder_from_rules(document) -> str | None:
    """Zieht NUR den fehlenden Ordner-Schritt der bereits im ersten Lauf gematchten
    Regeln nach (owner-gebunden) und protokolliert ihn revisionssicher.

    Für den Triage-Nachlauf (P1/P2): apply_rules überspringt den Ordner bei
    owner=None; setzt ein Workflow danach den Owner, wird hier NUR die noch fehlende
    Ordnerzuordnung der SCHON gematchten (globalen) Regeln nachgezogen – neue
    owner-spezifische Regeln werden NICHT teilweise angewendet. Damit KEIN doppelter
    ``classify``-Audit entsteht (der Erst-Lauf hat bereits einen), wird der Nachlauf
    mit dem EIGENEN Audit-Typ ``classify_folder`` protokolliert; zusätzlich wird
    ``document.classification`` konsolidiert (der persistierte Stand enthält den
    Ordner). Gibt den zugewiesenen ``full_path`` zurueck oder ``None``.
    """
    from .models import AuditLogEntry, ClassificationRule

    if document.folder is not None or document.owner_id is None:
        return None

    # NUR Regeln nachziehen, die BEREITS im ersten Lauf gematcht haben (in
    # document.classification["rules"]). Diese Regeln griffen global (owner=None) und
    # setzten alle ihre Aktionen AUSSER dem Ordner (bei owner=None übersprungen) –
    # hier fehlt also nur noch der Ordner. NEUE, owner-spezifische Regeln hier NICHT
    # anzuwenden: sonst würde nur ihr Ordner gesetzt (Typ/Korrespondent/Ablagepfad/
    # Tags fehlten), obwohl die Regel als angewendet gälte -> inkonsistent. Ihre
    # vollständige Anwendung bleibt einem echten Reclassify vorbehalten.
    already_matched = set((document.classification or {}).get("rules") or [])
    if not already_matched:
        return None

    text = _searchable_text(document)
    subject = getattr(document, "mail_subject", "") or ""
    sender = getattr(document, "mail_sender", "") or ""
    # WICHTIG: owner__isnull=True erzwingen (P1). Der Erst-Lauf lief auf einem
    # ownerlosen Triage-Dokument und konnte daher NUR globale Regeln (owner=None)
    # matchen; ``classification["rules"]`` speichert aber nur den REGELNAMEN. Ohne
    # Owner-Filter würde ``name__in`` auch eine gleichnamige FREMDE (owner-
    # spezifische) Regel laden, die den Ordner bestimmen könnte – ein Owner-Leck
    # über Namensgleichheit. Nur globale Regeln kommen für den Nachlauf infrage.
    rules = (
        ClassificationRule.objects.filter(
            enabled=True, owner__isnull=True, name__in=already_matched
        )
        .order_by("priority", "name")
    )
    for rule in rules:
        if not rule_matches(rule, text, subject=subject, sender=sender):
            continue
        # Defensiv (P2): ``then`` MUSS ein Dict sein. Ein per Alt-DB/Fixture
        # gespeichertes ``then`` als Liste (``or {}`` fängt nur falsy) würde sonst
        # an ``.get`` mit AttributeError abbrechen – der Nachlauf läuft in
        # classify_version, sodass ein Triage-Dokument NACH der Workflow-Owner-
        # Zuweisung auf FAILED ginge. apply_rules ist bereits robust; hier
        # nachziehen. (Neue Regeln werden vom Serializer strikt validiert.)
        then = rule.then if isinstance(rule.then, dict) else {}
        folder_path = _then_text(then, "folder")
        if not folder_path:
            continue
        folder = _get_or_create_folder(folder_path, document.owner)
        if folder is None:
            continue
        document.folder = folder
        # Klassifizierungsstand konsolidieren: nur applied.folder ergänzen (die Regel
        # steht bereits in classification["rules"]) -> DB-Stand spiegelt den Ordner.
        classification = dict(document.classification or {})
        applied = dict(classification.get("applied") or {})
        applied["folder"] = folder.full_path
        classification["applied"] = applied
        document.classification = classification
        document.save(update_fields=["folder", "classification"])
        # Eigener Audit-Typ (kein zweiter ``classify``): nachvollziehbar, dass die
        # Ordnerzuordnung NACH der Workflow-Owner-Zuweisung nachgezogen wurde.
        AuditLogEntry.objects.create(
            action="classify_folder",
            object_type="Document",
            object_id=str(document.id),
            detail={
                "rule": rule.name,
                "folder": folder.full_path,
                "trigger": "workflow_owner_followup",
            },
        )
        return folder.full_path
    return None


def apply_rules(document) -> dict:
    """Wendet alle passenden Regeln (nach Priorität) auf ein Dokument an."""
    from .models import (
        AuditLogEntry,
        ClassificationRule,
        Correspondent,
        DocumentType,
        StoragePath,
        Tag,
    )

    text = _searchable_text(document)
    subject = getattr(document, "mail_subject", "") or ""
    sender = getattr(document, "mail_sender", "") or ""
    matched: list[str] = []
    applied: dict = {}

    from django.db.models import Q

    # Owner-Scoping (P1): nur globale (owner=null) ODER dem Dokument-Eigentümer
    # gehörende Regeln – keine fremden Regeln auf fremden Dokumenten.
    rules = ClassificationRule.objects.filter(enabled=True).filter(
        Q(owner__isnull=True) | Q(owner_id=document.owner_id)
    ).order_by("priority", "name")

    # Atomar (P2): Stammdaten (Typ/Korrespondent/Ablage/Ordner/Tags) werden VOR dem
    # abschließenden Dokument-Update + Audit angelegt. Ohne Transaktion hinterließe
    # ein Fehler mittendrin teilweise Stammdaten OHNE zugehörige Klassifizierung/
    # Audit. Die gesamte Anwendung inkl. Audit läuft daher in einer Transaktion.
    with transaction.atomic():
        for rule in rules:
            if not rule_matches(rule, text, subject=subject, sender=sender):
                continue
            matched.append(rule.name)
            then = rule.then if isinstance(rule.then, dict) else {}

            dt = _then_text(then, "document_type")
            if dt and document.document_type is None:
                document.document_type, _ = DocumentType.objects.get_or_create(name=dt)
                applied["document_type"] = dt

            corr = _then_text(then, "correspondent")
            if corr and document.correspondent is None:
                document.correspondent, _ = Correspondent.objects.get_or_create(
                    name=corr
                )
                applied["correspondent"] = corr

            sp = _then_text(then, "storage_path")
            if sp and document.storage_path is None:
                document.storage_path, _ = StoragePath.objects.get_or_create(
                    name=sp,
                    defaults={"path_template": "archive/{jahr}/{korrespondent}/{titel}"},
                )
                applied["storage_path"] = sp

            folder_path = _then_text(then, "folder")
            # Owner-Konsistenz (P1): Der fachliche Ordner ist owner-gebunden. Ein
            # Triage-Dokument (owner=None) darf hier KEINEN Ordner bekommen – die
            # Klassifizierung läuft VOR der Workflow-Engine, die den Owner ggf. erst
            # setzt. Sonst entstünde ein nutzereigenes Dokument in einem ownerlosen
            # Ordner (und owner=None-Anlage könnte mehrdeutige NULL-Root-Treffer geben).
            # Für ownerlose Dokumente bleibt die Ordnerzuordnung offen (Admin-Triage
            # bzw. spätere Zuordnung, wenn der Owner feststeht).
            if folder_path and document.folder is None and document.owner_id is not None:
                folder = _get_or_create_folder(folder_path, document.owner)
                if folder is not None:
                    document.folder = folder
                    applied["folder"] = folder.full_path

            for name in _then_tags(then):
                tag, _ = Tag.objects.get_or_create(name=name, parent=None)
                document.tags.add(tag)
                applied.setdefault("tags", [])
                if name not in applied["tags"]:
                    applied["tags"].append(name)

        if matched:
            document.classification = {"rules": matched, "applied": applied}
            document.save()
            AuditLogEntry.objects.create(
                action="classify",
                object_type="Document",
                object_id=str(document.id),
                detail={"rules": matched, "applied": applied},
            )

    return {"rules": matched, "applied": applied}


def classify_documents(documents) -> dict:
    """Wendet ``apply_rules`` auf mehrere Dokumente an und aggregiert das Ergebnis.

    Zählt Dokumente, an denen tatsächlich etwas geändert wurde (``applied`` nicht
    leer) als ``updated``, ansonsten als ``unchanged``. Teilfehler an einzelnen
    Dokumenten brechen die Massenaktion **nicht** ab – sie werden pro Dokument in
    ``errors`` (``{"id", "error"}``) gesammelt, die übrigen laufen weiter.

    Gemeinsame Kernlogik für den synchronen Endpoint und den Celery-Task
    (``tasks.bulk_classify_documents``), damit beide Pfade identisch zählen.
    """
    updated = 0
    unchanged = 0
    errors: list[dict] = []
    for document in documents:
        try:
            result = apply_rules(document)
        except Exception as exc:  # pragma: no cover - defensiv, pro Dokument isoliert
            logger.exception("Bulk-Klassifizierung fehlgeschlagen: doc=%s", document.id)
            errors.append({"id": document.id, "error": str(exc)})
            continue
        if result.get("applied"):
            updated += 1
        else:
            unchanged += 1
    return {"updated": updated, "unchanged": unchanged, "errors": errors}
