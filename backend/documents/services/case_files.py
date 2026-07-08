"""Services für Vorgangsakten.

Eine Akte bündelt Dokumente zu einem fachlichen Vorgang. Die Zusammenfassung ist
bewusst quellengebunden: Jede UI-Ausgabe kann auf die Dokumente der Akte
zurückgeführt werden, auch wenn kein KI-Provider verfügbar ist.
"""
from __future__ import annotations

import re
from html import escape

from django.utils import timezone

from ai.providers import get_provider
from documents.models import CaseFile


_CASE_SUMMARY_SYSTEM = (
    "Du bist der Akten-Copilot eines privaten Dokumenten-Management-Systems. "
    "Fasse den Vorgang ausschließlich anhand der gelieferten Dokumentquellen "
    "zusammen. Erfinde keine Fakten, Fristen, Beträge oder Namen. "
    "Antworte auf Deutsch, kurz, strukturiert und nenne Quellenmarker wie [S1]. "
    "Hebe offene Punkte und nächste Schritte hervor, wenn die Quellen das hergeben."
)


def summarize_case_file(case_file: CaseFile) -> dict:
    """Erzeugt und speichert eine Zusammenfassung für eine Akte."""
    sources = _sources(case_file)
    if not sources:
        summary = "Diese Akte enthält noch keine Dokumente."
        source = "empty"
    else:
        provider = get_provider()
        if provider.available:
            summary, source = _ai_summary(case_file, sources, provider)
        else:
            summary, source = _local_summary(case_file, sources), "local"

    case_file.ai_summary = summary
    case_file.ai_summary_source = source
    case_file.ai_summary_generated_at = timezone.now()
    case_file.save(
        update_fields=[
            "ai_summary",
            "ai_summary_source",
            "ai_summary_generated_at",
            "updated_at",
        ]
    )
    return {"summary": summary, "source": source, "sources": sources}


def _sources(case_file: CaseFile, *, limit: int = 12) -> list[dict]:
    docs = (
        case_file.documents.select_related(
            "correspondent",
            "document_type",
            "folder",
            "current_version",
        )
        .prefetch_related("current_version__page_texts")
        .order_by("-created_at", "-added_at", "-id")[:limit]
    )
    out = []
    for doc in docs:
        version = doc.current_version
        text = ""
        page_no = None
        if version:
            page = version.page_texts.order_by("page_no").first()
            if page and page.text:
                text = page.text
                page_no = page.page_no
            else:
                text = version.ocr_text or ""
        snippet = _snippet(text)
        out.append(
            {
                "id": f"S{len(out) + 1}",
                "document": doc.id,
                "document_title": doc.title,
                "folder_path": doc.folder.full_path if doc.folder_id else None,
                "page": page_no,
                "snippet": snippet,
                "snippet_html": escape(snippet),
            }
        )
    return out


def _ai_summary(case_file: CaseFile, sources: list[dict], provider) -> tuple[str, str]:
    source_block = "\n\n".join(
        (
            f"[{source['id']}] Dokument: {source['document_title']}\n"
            f"Ordner: {source['folder_path'] or '-'}\n"
            f"Seite: {source['page'] or '-'}\n"
            f"Ausschnitt: {source['snippet'] or '-'}"
        )
        for source in sources
    )
    prompt = (
        f"Akte: {case_file.title}\n"
        f"Beschreibung: {case_file.description or '-'}\n\n"
        f"Quellen:\n{source_block}\n\n"
        "Erstelle eine Vorgangszusammenfassung mit: Kurzlage, wichtige Fakten, "
        "offene Punkte/nächste Schritte. Nutze Quellenmarker."
    )
    try:
        return provider.complete(prompt, system=_CASE_SUMMARY_SYSTEM).strip(), "ai"
    except Exception:  # noqa: BLE001 - UI bekommt Fallback, Pod-Logs den Providerfehler
        return _local_summary(case_file, sources), "error"


def _local_summary(case_file: CaseFile, sources: list[dict]) -> str:
    titles = ", ".join(source["document_title"] for source in sources[:5])
    newest = sources[0]["document_title"] if sources else "kein Dokument"
    return (
        f"Die Akte „{case_file.title}“ enthält {len(sources)} berücksichtigte "
        f"Dokumente. Neuester Eintrag: {newest} [S1]. "
        f"Relevante Dokumente: {titles}."
    )


def _snippet(text: str, *, max_len: int = 420) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"
