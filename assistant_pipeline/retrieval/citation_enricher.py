"""Citation and evidence helpers for retrieval-backed assistant responses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from refiner.document_schema import format_locator

from assistant_pipeline.retrieval.match_utils import (
    match_chunk_id,
    match_citation,
    match_field,
    match_metadata,
    match_score,
    match_text,
)
from assistant_pipeline.retrieval.text_utils import clean_retrieval_text, retrieval_query_terms

_CLAIM_SPLIT_RE = re.compile(r"(?:\n{2,}|(?<=[.!?])\s+)")
_BULLET_PREFIX_RE = re.compile(r"^[\s>*-]+")


@dataclass(frozen=True)
class CitationBindingResult:
    """Structured claim-to-evidence binding for one answer."""

    citations: List[Dict[str, Any]] = field(default_factory=list)
    claim_bindings: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def rag_match_citation(match: Any) -> str:
    """Return the preferred citation label for a retrieval match payload."""

    return match_citation(match)


def _match_locator(metadata: Mapping[str, Any]) -> str:
    return format_locator(
        page_start=metadata.get("page_start"),
        page_end=metadata.get("page_end"),
        block_start=metadata.get("block_start"),
        block_end=metadata.get("block_end"),
    )


def _heading_path(metadata: Mapping[str, Any]) -> List[str]:
    raw = metadata.get("heading_path")
    if not isinstance(raw, list):
        return []
    return [str(part).strip() for part in raw if str(part).strip()]


def serialize_rag_match(match: Any) -> Dict[str, Any]:
    """Return a transport-safe retrieval match payload with locator hints."""

    metadata = match_metadata(match)
    return {
        "chunk_id": match_chunk_id(match),
        "source": str(match_field(match, "source", "") or "").strip(),
        "score": round(match_score(match), 4),
        "text": match_text(match),
        "metadata": metadata,
        "citation": rag_match_citation(match),
        "locator": _match_locator(metadata),
        "heading_path": _heading_path(metadata),
    }


def render_rag_context(matches: Sequence[Any]) -> str:
    """Render RAG context blocks with stable citation labels and headings."""

    blocks: List[str] = []
    for match in matches or []:
        citation = rag_match_citation(match)
        metadata = match_metadata(match)
        heading_path = _heading_path(metadata)
        text = match_text(match).strip()
        if not text:
            continue
        if heading_path:
            heading_label = " > ".join(heading_path[-3:])
            blocks.append(f"[{citation}]\nHeading path: {heading_label}\n{text}")
        else:
            blocks.append(f"[{citation}]\n{text}")
    return "\n\n".join(blocks)


def _truncate(text: str, *, limit: int) -> str:
    cleaned = clean_retrieval_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(0, limit - 3)].rstrip()}..."


def build_citation_sources(matches: Sequence[Any]) -> List[Dict[str, Any]]:
    """Normalise retrieval matches into a deduplicated evidence set."""

    sources: List[Dict[str, Any]] = []
    seen_chunk_ids = set()
    for match in matches or []:
        chunk_id = match_chunk_id(match)
        if not chunk_id or chunk_id in seen_chunk_ids:
            continue
        metadata = match_metadata(match)
        source = {
            "chunk_id": chunk_id,
            "source": str(match_field(match, "source", "") or "").strip(),
            "citation": rag_match_citation(match),
            "locator": _match_locator(metadata),
            "heading_path": _heading_path(metadata),
            "score": round(match_score(match), 4),
            "text_preview": _truncate(match_text(match), limit=280),
            "metadata": metadata,
        }
        sources.append(source)
        seen_chunk_ids.add(chunk_id)
    return sources


def _answer_claims(answer: Any, *, max_claims: int) -> List[str]:
    claims: List[str] = []
    seen = set()
    cleaned_answer = str(answer or "").strip()
    for block in _CLAIM_SPLIT_RE.split(cleaned_answer):
        candidate = _BULLET_PREFIX_RE.sub("", str(block or "").strip())
        if not candidate:
            continue
        canonical = clean_retrieval_text(candidate).lower()
        if canonical in seen:
            continue
        seen.add(canonical)
        claims.append(candidate)
        if len(claims) >= max(1, int(max_claims)):
            break
    return claims


def _claim_terms(text: str) -> Tuple[str, ...]:
    stripped = str(text or "")
    stripped = re.sub(r"\[[^\]]+\]", " ", stripped)
    return retrieval_query_terms(stripped)


def _citation_lookup(sources: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for source in sources or []:
        citation = str(source.get("citation") or "").strip()
        if citation:
            lookup[citation.lower()] = dict(source)
    return lookup


def _source_terms(source: Mapping[str, Any]) -> Tuple[str, ...]:
    text = " ".join(
        [
            str(source.get("source") or ""),
            str(source.get("citation") or ""),
            " ".join(source.get("heading_path") or []),
            str(source.get("text_preview") or ""),
        ]
    )
    return retrieval_query_terms(text)


def _binding_score(claim_terms: Sequence[str], source_terms: Sequence[str], claim_text: str, source: Mapping[str, Any]) -> float:
    overlap = {term for term in claim_terms if term in source_terms}
    if not overlap:
        return 0.0
    score = float(len(overlap))
    source_preview = clean_retrieval_text(source.get("text_preview")).lower()
    normalised_claim = clean_retrieval_text(claim_text).lower()
    if normalised_claim and normalised_claim in source_preview:
        score += 2.0
    if len(overlap) >= 2:
        score += 1.0
    heading_terms = set(retrieval_query_terms(" ".join(source.get("heading_path") or [])))
    if overlap & heading_terms:
        score += 0.5
    return score


def bind_answer_citations(
    answer: Any,
    matches: Sequence[Any],
    *,
    max_claims: int = 12,
    max_bindings_per_claim: int = 2,
) -> CitationBindingResult:
    """Bind answer claims to exact retrieved chunks and locator metadata."""

    citations = build_citation_sources(matches)
    if not citations:
        return CitationBindingResult(
            citations=[],
            claim_bindings=[],
            metadata={
                "claim_count": 0,
                "bound_claim_count": 0,
                "unbound_claim_count": 0,
                "binding_coverage_ratio": 0.0,
                "citation_count": 0,
                "explicit_citation_claim_count": 0,
            },
        )

    lookup = _citation_lookup(citations)
    prepared_sources = [
        {
            **source,
            "_terms": _source_terms(source),
        }
        for source in citations
    ]
    claim_bindings: List[Dict[str, Any]] = []
    bound_claims = 0
    explicit_claims = 0
    for claim_index, claim_text in enumerate(_answer_claims(answer, max_claims=max_claims), start=1):
        explicit_sources: List[Dict[str, Any]] = []
        lowered_claim = claim_text.lower()
        for citation, source in lookup.items():
            if citation in lowered_claim:
                explicit_sources.append(dict(source))
        binding_reason = "unbound"
        selected_sources: List[Dict[str, Any]] = []
        if explicit_sources:
            explicit_claims += 1
            binding_reason = "explicit_citation"
            selected_sources = explicit_sources[: max(1, int(max_bindings_per_claim))]
        else:
            claim_terms = _claim_terms(claim_text)
            scored: List[Tuple[float, Dict[str, Any]]] = []
            for source in prepared_sources:
                score = _binding_score(claim_terms, source.get("_terms") or (), claim_text, source)
                if score <= 0.0:
                    continue
                scored.append((score, source))
            scored.sort(
                key=lambda item: (
                    -item[0],
                    -float(item[1].get("score") or 0.0),
                    str(item[1].get("citation") or ""),
                )
            )
            for score, source in scored[: max(1, int(max_bindings_per_claim))]:
                if score < 2.0 and len(_claim_terms(claim_text)) > 1:
                    continue
                selected = dict(source)
                selected["binding_score"] = round(score, 3)
                selected_sources.append(selected)
            if selected_sources:
                binding_reason = "term_overlap"
        if selected_sources:
            bound_claims += 1
        claim_bindings.append(
            {
                "claim_id": f"claim-{claim_index:03d}",
                "claim_text": claim_text,
                "bound": bool(selected_sources),
                "binding_reason": binding_reason,
                "citations": [
                    {
                        "chunk_id": str(source.get("chunk_id") or ""),
                        "citation": str(source.get("citation") or ""),
                        "source": str(source.get("source") or ""),
                        "locator": str(source.get("locator") or ""),
                        "heading_path": list(source.get("heading_path") or []),
                        "binding_score": source.get("binding_score"),
                    }
                    for source in selected_sources
                ],
            }
        )
    claim_count = len(claim_bindings)
    return CitationBindingResult(
        citations=citations,
        claim_bindings=claim_bindings,
        metadata={
            "claim_count": claim_count,
            "bound_claim_count": bound_claims,
            "unbound_claim_count": max(0, claim_count - bound_claims),
            "binding_coverage_ratio": round(bound_claims / claim_count, 4) if claim_count else 0.0,
            "citation_count": len(citations),
            "explicit_citation_claim_count": explicit_claims,
        },
    )
