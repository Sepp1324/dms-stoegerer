"""Dossier Builder: aus Recherchefrage wird speicherbare Beweisakte."""
from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime

from django.utils import timezone

from ai.providers import get_provider
from documents.models import Dossier
from documents.services.retrieval import format_sources_for_prompt, retrieve_context

_DOSSIER_SYSTEM = (
    "Du bist der Dossier-Copilot eines privaten Dokumenten-Management-Systems. "
    "Erstelle eine knappe, belastbare Beweisakte ausschließlich anhand der "
    "gelieferten Quellen. Erfinde keine Fakten, Beträge, Fristen oder Namen. "
    "Verwende Quellenmarker wie [S1], [S2]. Antworte auf Deutsch."
)


def generate_dossier(dossier: Dossier, documents_qs, *, limit: int = 14) -> Dossier:
    """Generiert ein Dossier aus sichtbaren Dokumenten und speichert es."""
    retrieval = retrieve_context(dossier.query, documents_qs, limit=limit)
    sources = retrieval["sources"]
    timeline = build_timeline(sources)
    entities = build_entities(sources)
    contracts = build_contracts(sources)
    summary, source = build_summary(dossier, sources, timeline, entities, contracts)

    dossier.summary = summary
    dossier.sources = sources
    dossier.timeline = timeline
    dossier.entities = entities
    dossier.contracts = contracts
    dossier.status = Dossier.Status.GENERATED
    dossier.generated_source = source
    dossier.generated_at = timezone.now()
    dossier.save(
        update_fields=[
            "summary",
            "sources",
            "timeline",
            "entities",
            "contracts",
            "status",
            "generated_source",
            "generated_at",
            "updated_at",
        ]
    )
    dossier.documents.set(dict.fromkeys(source["document"] for source in sources))
    return dossier


def build_summary(
    dossier: Dossier,
    sources: list[dict],
    timeline: list[dict],
    entities: list[dict],
    contracts: list[dict],
) -> tuple[str, str]:
    """Erstellt eine Zusammenfassung, KI wenn verfügbar, sonst lokale Heuristik."""
    if not sources:
        return (
            "Keine passenden Quellen gefunden. Das Dossier bleibt als Entwurf der Recherchefrage erhalten.",
            Dossier.Source.LOCAL,
        )

    provider = get_provider()
    if provider.available:
        prompt = (
            f"Dossier: {dossier.title}\n"
            f"Frage/Thema: {dossier.query}\n\n"
            f"Quellen:\n{format_sources_for_prompt(sources)}\n\n"
            "Erstelle ein Dossier mit diesen Abschnitten:\n"
            "1. Kurzfassung\n"
            "2. Wichtige Fakten\n"
            "3. Timeline\n"
            "4. Beteiligte Personen/Firmen/Identifier\n"
            "5. Offene Punkte\n"
            "Halte es knapp und zitiere Aussagen mit [S1] usw."
        )
        try:
            return provider.complete(prompt, system=_DOSSIER_SYSTEM).strip(), Dossier.Source.AI
        except Exception:
            return local_summary(dossier, sources, timeline, entities, contracts), Dossier.Source.ERROR

    return local_summary(dossier, sources, timeline, entities, contracts), Dossier.Source.UNAVAILABLE


def local_summary(
    dossier: Dossier,
    sources: list[dict],
    timeline: list[dict],
    entities: list[dict],
    contracts: list[dict],
) -> str:
    """Deterministischer Fallback ohne KI-Provider."""
    titles = ", ".join(source["document_title"] for source in sources[:5])
    first_source = sources[0]["id"] if sources else "S1"
    entity_names = ", ".join(item["name"] for item in entities[:6]) or "keine"
    contract_names = ", ".join(
        item.get("provider") or item.get("contract_type_label") or "Vertrag"
        for item in contracts[:4]
    ) or "keine"
    latest = timeline[0]["title"] if timeline else sources[0]["document_title"]
    return (
        f"Das Dossier „{dossier.title}“ basiert auf {len(sources)} Quellen. "
        f"Zentrale Dokumente: {titles} [{first_source}]. "
        f"Neuester relevanter Eintrag: {latest}. "
        f"Erkannte Beteiligte/Identifier: {entity_names}. "
        f"Erkannte Verträge: {contract_names}."
    )


def build_timeline(sources: list[dict]) -> list[dict]:
    """Baut eine kompakte Quellen-Timeline aus den gefundenen Dokumenten."""
    # Der Retriever liefert keine Datumsfelder; in V1 sortieren wir nach
    # Quellenreihenfolge und halten den Beleg eindeutig per Source-ID fest.
    seen = OrderedDict()
    for source in sources:
        doc_id = source["document"]
        if doc_id in seen:
            seen[doc_id]["sources"].append(source["id"])
            continue
        seen[doc_id] = {
            "document": doc_id,
            "title": source["document_title"],
            "folder_path": source.get("folder_path"),
            "page": source.get("page"),
            "sources": [source["id"]],
            "reason": source.get("reason") or "",
        }
    return list(seen.values())


def build_entities(sources: list[dict]) -> list[dict]:
    """Aggregiert erkannte Entitäten aus Source-Cards."""
    entities: OrderedDict[tuple[int | None, str, str], dict] = OrderedDict()
    for source in sources:
        for entity in source.get("entities") or []:
            key = (entity.get("id"), entity.get("kind", ""), entity.get("name", ""))
            item = entities.setdefault(
                key,
                {
                    "id": entity.get("id"),
                    "kind": entity.get("kind"),
                    "name": entity.get("name"),
                    "roles": [],
                    "sources": [],
                    "identifiers": entity.get("identifiers") or [],
                },
            )
            role = entity.get("role")
            if role and role not in item["roles"]:
                item["roles"].append(role)
            if source["id"] not in item["sources"]:
                item["sources"].append(source["id"])
    return list(entities.values())


def build_contracts(sources: list[dict]) -> list[dict]:
    """Aggregiert Vertragskontext aus Source-Cards."""
    contracts: OrderedDict[int | str, dict] = OrderedDict()
    for source in sources:
        contract = source.get("contract")
        if not contract:
            continue
        key = contract.get("id") or f"{contract.get('provider')}:{contract.get('contract_number')}"
        item = contracts.setdefault(key, {**contract, "sources": []})
        if source["id"] not in item["sources"]:
            item["sources"].append(source["id"])
    return list(contracts.values())


def render_markdown(dossier: Dossier) -> str:
    """Exportiert ein Dossier als Markdown mit Quellenbelegen."""
    lines = [
        f"# {dossier.title}",
        "",
        f"**Frage/Thema:** {dossier.query}",
        f"**Status:** {dossier.get_status_display()}",
        f"**Erzeugt:** {_format_dt(dossier.generated_at)}",
        f"**Quelle:** {dossier.get_generated_source_display()}",
        "",
        "## Kurzfassung",
        "",
        dossier.summary or "_Noch keine Zusammenfassung erzeugt._",
        "",
        "## Timeline",
        "",
    ]
    if dossier.timeline:
        for item in dossier.timeline:
            source_refs = ", ".join(item.get("sources") or [])
            lines.append(f"- {item.get('title')} ({source_refs})")
    else:
        lines.append("- _Keine Timeline-Einträge._")

    lines.extend(["", "## Beteiligte Entitäten", ""])
    if dossier.entities:
        for item in dossier.entities:
            refs = ", ".join(item.get("sources") or [])
            lines.append(f"- {item.get('name')} · {item.get('kind')} ({refs})")
    else:
        lines.append("- _Keine Entitäten erkannt._")

    lines.extend(["", "## Verträge / Fristen", ""])
    if dossier.contracts:
        for item in dossier.contracts:
            refs = ", ".join(item.get("sources") or [])
            label = item.get("provider") or item.get("contract_type_label") or "Vertrag"
            number = item.get("contract_number") or "-"
            lines.append(
                f"- {label} · Nr. {number} · Status {item.get('status_label') or '-'} ({refs})"
            )
    else:
        lines.append("- _Keine Verträge erkannt._")

    lines.extend(["", "## Quellen", ""])
    if dossier.sources:
        for source in dossier.sources:
            page = f", Seite {source.get('page')}" if source.get("page") else ""
            snippet = re.sub(r"\s+", " ", source.get("snippet") or "").strip()
            lines.append(
                f"- [{source.get('id')}] Dokument #{source.get('document')}: "
                f"{source.get('document_title')}{page}\n  > {snippet}"
            )
    else:
        lines.append("- _Keine Quellen._")
    return "\n".join(lines).rstrip() + "\n"


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.isoformat()
