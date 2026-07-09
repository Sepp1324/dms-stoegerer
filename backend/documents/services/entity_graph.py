"""Deterministischer Entitätsgraph für das private DMS-Gedächtnis."""
from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from documents.models import (
    Document,
    DocumentEntity,
    EntityIdentifier,
    EntityRelation,
    KnowledgeEntity,
)


GENERATED_SOURCES = {
    KnowledgeEntity.Source.OCR,
    KnowledgeEntity.Source.METADATA,
    KnowledgeEntity.Source.MAIL,
    KnowledgeEntity.Source.CONTRACT,
    KnowledgeEntity.Source.HEURISTIC,
}
IDENTIFIER_KINDS = {
    KnowledgeEntity.Kind.IBAN,
    KnowledgeEntity.Kind.EMAIL,
    KnowledgeEntity.Kind.PHONE,
    KnowledgeEntity.Kind.CONTRACT_NUMBER,
    KnowledgeEntity.Kind.POLICY_NUMBER,
    KnowledgeEntity.Kind.CUSTOMER_NUMBER,
    KnowledgeEntity.Kind.TAX_NUMBER,
}
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30}\b", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?<!\w)(?:\+|00)\d[\d\s()/.-]{7,}\d")
COMPANY_RE = re.compile(
    r"\b([A-ZÄÖÜ][\wÄÖÜäöüß&.,' -]{2,90}?\b"
    r"(?:GmbH|AG|KG|OG|e\.U\.|Bank|Versicherung(?:en)?|Service|Telekom|"
    r"Finanzamt|Magistrat|Gemeinde|Behörde|Krankenkasse))\b"
)
PERSON_RE = re.compile(
    r"\b(?:Herrn?|Frau|lautend auf|Person)\s+"
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß.'-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.'-]+){1,3})"
)
LABEL_PATTERNS = [
    (
        KnowledgeEntity.Kind.CONTRACT_NUMBER,
        DocumentEntity.Role.CONTRACT,
        re.compile(
            r"(?i)(?:vertrags(?:nummer|nr\.?)|vertrag)\s*[:#]?\s*"
            r"([A-Z0-9][A-Z0-9/\-.]{3,})"
        ),
    ),
    (
        KnowledgeEntity.Kind.POLICY_NUMBER,
        DocumentEntity.Role.CONTRACT,
        re.compile(
            r"(?i)(?:polizz(?:en)?nummer|police(?:nummer)?|versicherungsnummer)\s*[:#]?\s*"
            r"([A-Z0-9][A-Z0-9/\-.]{3,})"
        ),
    ),
    (
        KnowledgeEntity.Kind.CUSTOMER_NUMBER,
        DocumentEntity.Role.REFERENCE,
        re.compile(
            r"(?i)(?:kunden(?:nummer|nr\.?)|kundennummer)\s*[:#]?\s*"
            r"([A-Z0-9][A-Z0-9/\-.]{3,})"
        ),
    ),
    (
        KnowledgeEntity.Kind.TAX_NUMBER,
        DocumentEntity.Role.REFERENCE,
        re.compile(
            r"(?i)(?:steuernummer|abgabenkonto|tax(?:\s*id)?)\s*[:#]?\s*"
            r"([A-Z0-9][A-Z0-9/\-.]{3,})"
        ),
    ),
]


@dataclass(frozen=True)
class EntityHit:
    kind: str
    name: str
    role: str = DocumentEntity.Role.MENTION
    source: str = KnowledgeEntity.Source.OCR
    confidence: int = 70
    snippet: str = ""
    identifier_value: str = ""


def sync_document_entities(document: Document, *, actor=None) -> dict:
    """Extrahiert Entitäten und synchronisiert Dokumentlinks idempotent."""
    document = (
        Document.objects.select_related(
            "owner",
            "correspondent",
            "current_version",
            "contract_record",
        )
        .filter(pk=document.pk)
        .first()
    )
    if document is None:
        return {"status": "missing", "entities": 0, "links": 0, "relations": 0}

    hits = extract_entity_hits(document)
    active_link_keys = set()
    linked_entities: list[KnowledgeEntity] = []

    with transaction.atomic():
        for hit in hits:
            canonical = canonicalize(hit.kind, hit.identifier_value or hit.name)
            if not canonical:
                continue
            entity, _created = KnowledgeEntity.objects.get_or_create(
                owner=document.owner,
                kind=hit.kind,
                canonical_name=canonical,
                defaults={
                    "name": hit.name[:255],
                    "confidence": hit.confidence,
                    "source": hit.source,
                    "metadata": {},
                    "last_seen_at": timezone.now(),
                },
            )
            updates = []
            if hit.confidence > entity.confidence:
                entity.confidence = hit.confidence
                updates.append("confidence")
            if entity.name == entity.canonical_name and hit.name:
                entity.name = hit.name[:255]
                updates.append("name")
            entity.last_seen_at = timezone.now()
            updates.append("last_seen_at")
            if updates:
                entity.save(update_fields=list(dict.fromkeys(updates)))

            if hit.identifier_value or hit.kind in IDENTIFIER_KINDS:
                EntityIdentifier.objects.update_or_create(
                    entity=entity,
                    kind=hit.kind,
                    normalized_value=canonical,
                    defaults={
                        "value": (hit.identifier_value or hit.name)[:255],
                        "source": hit.source,
                        "confidence": hit.confidence,
                    },
                )

            link, _ = DocumentEntity.objects.update_or_create(
                document=document,
                entity=entity,
                role=hit.role,
                source=hit.source,
                defaults={
                    "confidence": hit.confidence,
                    "occurrences": _count_occurrences(document, hit),
                    "source_snippet": hit.snippet[:1000],
                },
            )
            active_link_keys.add((link.entity_id, link.role, link.source))
            linked_entities.append(entity)

        for link in DocumentEntity.objects.filter(
            document=document, source__in=GENERATED_SOURCES
        ):
            if (link.entity_id, link.role, link.source) not in active_link_keys:
                link.delete()
        relation_count = _sync_document_relations(document)

    return {
        "status": "synced",
        "entities": len({entity.id for entity in linked_entities}),
        "links": len(active_link_keys),
        "relations": relation_count,
    }


def extract_entity_hits(document: Document) -> list[EntityHit]:
    """Liefert deduplizierte Entitäts-Treffer für ein Dokument."""
    version = document.current_version
    ocr_text = version.ocr_text if version else ""
    searchable = "\n".join(
        part
        for part in [
            document.title or "",
            document.mail_subject or "",
            document.mail_sender or "",
            ocr_text or "",
        ]
        if part
    )
    hits: list[EntityHit] = []

    if document.correspondent_id:
        kind = _classify_party(document.correspondent.name)
        hits.append(
            EntityHit(
                kind=kind,
                name=document.correspondent.name,
                role=DocumentEntity.Role.CORRESPONDENT,
                source=KnowledgeEntity.Source.METADATA,
                confidence=92,
            )
        )

    if document.mail_sender:
        for match in EMAIL_RE.finditer(document.mail_sender):
            hits.append(_identifier_hit(KnowledgeEntity.Kind.EMAIL, match, DocumentEntity.Role.SENDER, KnowledgeEntity.Source.MAIL))

    try:
        contract = document.contract_record
    except Exception:  # noqa: BLE001 - reverse OneToOne fehlt bei Nicht-Verträgen
        contract = None
    if contract is not None:
        if contract.provider:
            hits.append(
                EntityHit(
                    kind=_classify_party(contract.provider),
                    name=contract.provider,
                    role=DocumentEntity.Role.CONTRACT,
                    source=KnowledgeEntity.Source.CONTRACT,
                    confidence=max(70, contract.confidence),
                )
            )
        if contract.contract_number:
            hits.append(
                EntityHit(
                    kind=KnowledgeEntity.Kind.CONTRACT_NUMBER,
                    name=contract.contract_number,
                    identifier_value=contract.contract_number,
                    role=DocumentEntity.Role.CONTRACT,
                    source=KnowledgeEntity.Source.CONTRACT,
                    confidence=max(75, contract.confidence),
                )
            )

    for match in IBAN_RE.finditer(searchable):
        raw = match.group(0)
        normalized = canonicalize(KnowledgeEntity.Kind.IBAN, raw)
        if 15 <= len(normalized) <= 34:
            hits.append(_identifier_hit(KnowledgeEntity.Kind.IBAN, match, DocumentEntity.Role.ACCOUNT))
    for match in EMAIL_RE.finditer(searchable):
        hits.append(_identifier_hit(KnowledgeEntity.Kind.EMAIL, match))
    for match in PHONE_RE.finditer(searchable):
        normalized = canonicalize(KnowledgeEntity.Kind.PHONE, match.group(0))
        if len(normalized) >= 8:
            hits.append(_identifier_hit(KnowledgeEntity.Kind.PHONE, match))
    for kind, role, pattern in LABEL_PATTERNS:
        for match in pattern.finditer(searchable):
            value = _clean_identifier(match.group(1))
            if value:
                hits.append(
                    EntityHit(
                        kind=kind,
                        name=value,
                        identifier_value=value,
                        role=role,
                        source=KnowledgeEntity.Source.OCR,
                        confidence=82,
                        snippet=_snippet(searchable, match.start(), match.end()),
                    )
                )
    for match in COMPANY_RE.finditer(searchable):
        name = _clean_name(match.group(1))
        if name and len(name) <= 120:
            hits.append(
                EntityHit(
                    kind=_classify_party(name),
                    name=name,
                    role=DocumentEntity.Role.MENTION,
                    source=KnowledgeEntity.Source.OCR,
                    confidence=66,
                    snippet=_snippet(searchable, match.start(), match.end()),
                )
            )
    for match in PERSON_RE.finditer(searchable):
        name = _clean_name(match.group(1))
        if _looks_like_person_name(name):
            hits.append(
                EntityHit(
                    kind=KnowledgeEntity.Kind.PERSON,
                    name=name,
                    role=DocumentEntity.Role.MENTION,
                    source=KnowledgeEntity.Source.OCR,
                    confidence=64,
                    snippet=_snippet(searchable, match.start(), match.end()),
                )
            )

    return _dedupe_hits(hits)


def canonicalize(kind: str, value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if kind == KnowledgeEntity.Kind.IBAN:
        return re.sub(r"\s+", "", value).upper()
    if kind == KnowledgeEntity.Kind.EMAIL:
        return value.lower()
    if kind == KnowledgeEntity.Kind.PHONE:
        return re.sub(r"[^\d+]", "", value).replace("00", "+", 1)
    if kind in {
        KnowledgeEntity.Kind.CONTRACT_NUMBER,
        KnowledgeEntity.Kind.POLICY_NUMBER,
        KnowledgeEntity.Kind.CUSTOMER_NUMBER,
        KnowledgeEntity.Kind.TAX_NUMBER,
    }:
        return _clean_identifier(value)
    return re.sub(r"\s+", " ", value).casefold()


def _sync_document_relations(document: Document) -> int:
    EntityRelation.objects.filter(
        document=document, source=KnowledgeEntity.Source.HEURISTIC
    ).delete()
    links = list(
        DocumentEntity.objects.select_related("entity").filter(document=document)
    )
    party_entities = [
        link.entity
        for link in links
        if link.entity.kind
        in {
            KnowledgeEntity.Kind.PERSON,
            KnowledgeEntity.Kind.COMPANY,
            KnowledgeEntity.Kind.AUTHORITY,
        }
    ]
    identifier_entities = [
        link.entity for link in links if link.entity.kind in IDENTIFIER_KINDS
    ]
    created_or_seen = 0
    for party in party_entities[:8]:
        for identifier in identifier_entities[:12]:
            if party.id == identifier.id:
                continue
            EntityRelation.objects.update_or_create(
                from_entity=party,
                to_entity=identifier,
                relation_type=EntityRelation.RelationType.USES_IDENTIFIER,
                document=document,
                defaults={
                    "confidence": min(party.confidence, identifier.confidence),
                    "source": KnowledgeEntity.Source.HEURISTIC,
                },
            )
            created_or_seen += 1
    if len(party_entities) > 1:
        base = party_entities[0]
        for other in party_entities[1:8]:
            if base.id == other.id:
                continue
            EntityRelation.objects.update_or_create(
                from_entity=base,
                to_entity=other,
                relation_type=EntityRelation.RelationType.MENTIONED_WITH,
                document=document,
                defaults={
                    "confidence": min(base.confidence, other.confidence),
                    "source": KnowledgeEntity.Source.HEURISTIC,
                },
            )
            created_or_seen += 1
    return created_or_seen


def _identifier_hit(kind: str, match, role=DocumentEntity.Role.MENTION, source=KnowledgeEntity.Source.OCR):
    value = match.group(0)
    return EntityHit(
        kind=kind,
        name=value,
        identifier_value=value,
        role=role,
        source=source,
        confidence=78,
        snippet=_snippet(match.string, match.start(), match.end()),
    )


def _dedupe_hits(hits: list[EntityHit]) -> list[EntityHit]:
    by_key: dict[tuple[str, str, str, str], EntityHit] = {}
    for hit in hits:
        canonical = canonicalize(hit.kind, hit.identifier_value or hit.name)
        key = (hit.kind, canonical, hit.role, hit.source)
        previous = by_key.get(key)
        if previous is None or hit.confidence > previous.confidence:
            by_key[key] = hit
    return list(by_key.values())


def _classify_party(name: str) -> str:
    lower = (name or "").lower()
    if any(word in lower for word in ("finanzamt", "magistrat", "gemeinde", "behörde", "bka", "bvaeb", "ögk")):
        return KnowledgeEntity.Kind.AUTHORITY
    if any(word in lower for word in ("gmbh", " ag", " kg", "bank", "versicherung", "telekom", "service")):
        return KnowledgeEntity.Kind.COMPANY
    return KnowledgeEntity.Kind.PERSON if _looks_like_person_name(name) else KnowledgeEntity.Kind.COMPANY


def _looks_like_person_name(name: str) -> bool:
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    if not 2 <= len(parts) <= 4:
        return False
    blocked = {"gruppe", "service", "bank", "versicherung", "gmbh", "ag", "kg"}
    return not any(part.casefold().strip(".,") in blocked for part in parts)


def _clean_identifier(raw: str) -> str:
    return re.sub(r"[^A-Z0-9/\-.]", "", (raw or "").upper()).strip(".-/")[:128]


def _clean_name(raw: str) -> str:
    value = re.sub(r"\s+", " ", raw or "").strip(" ,.;:-")
    return value[:255]


def _snippet(text: str, start: int, end: int, width: int = 90) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _count_occurrences(document: Document, hit: EntityHit) -> int:
    version = document.current_version
    haystack = " ".join(
        part
        for part in [
            document.title or "",
            document.mail_subject or "",
            document.mail_sender or "",
            version.ocr_text if version else "",
        ]
        if part
    )
    needle = hit.identifier_value or hit.name
    if not needle:
        return 1
    return max(1, haystack.casefold().count(needle.casefold()))
