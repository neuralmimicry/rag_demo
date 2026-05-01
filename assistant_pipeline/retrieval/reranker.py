"""Deterministic reranking for retrieved evidence candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from assistant_pipeline.retrieval.match_utils import clone_match, match_citation, match_chunk_id, match_metadata, match_score, match_text
from assistant_pipeline.retrieval.text_utils import clean_retrieval_text, core_retrieval_phrase, retrieval_keywords, retrieval_query_terms

_RERANK_ALGORITHM_VERSION = "rerank_v1"
_PHRASE_SPLIT_RE = re.compile(r"[;,\n?!]+|\b(?:and|also|plus|then|or|vs|versus)\b", re.IGNORECASE)
_QUOTE_RE = re.compile(r"['\"]([^'\"]{3,})['\"]")


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: Any, default: int, *, minimum: int = 1, maximum: int = 24) -> int:
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return min(maximum, max(minimum, number))


@dataclass(frozen=True)
class RetrievalRerankPolicy:
    """Runtime configuration for deterministic retrieval reranking."""

    enabled: bool = False
    max_phrase_terms: int = 6


@dataclass(frozen=True)
class RetrievalRerankResult:
    """Reranked matches and trace-friendly metadata."""

    matches: List[Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


def retrieval_rerank_policy_from_config(config: Mapping[str, Any] | None) -> RetrievalRerankPolicy:
    """Build a typed reranking policy from runtime config."""

    values = dict(config or {})
    return RetrievalRerankPolicy(
        enabled=_flag(values.get("rerank_enabled"), False),
        max_phrase_terms=_int(values.get("rerank_max_phrase_terms"), 6, minimum=2, maximum=12),
    )


def retrieval_rerank_scope_fragment(policy: RetrievalRerankPolicy) -> str:
    """Return a stable scope fragment for cache and trace grouping."""

    if not policy.enabled:
        return "rerank=off"
    return f"rerank={_RERANK_ALGORITHM_VERSION}:max_terms={policy.max_phrase_terms}"


def _auxiliary_text(match: Any) -> str:
    metadata = match_metadata(match)
    parts: List[str] = [
        str(match_citation(match) or "").strip(),
        str(getattr(match, "source", "") if not isinstance(match, Mapping) else match.get("source") or "").strip(),
    ]
    heading_path = metadata.get("heading_path")
    if isinstance(heading_path, (list, tuple)):
        parts.extend(str(part).strip() for part in heading_path if str(part).strip())
    locator_summary = metadata.get("locator_summary")
    if locator_summary:
        parts.append(str(locator_summary).strip())
    return clean_retrieval_text(" ".join(part for part in parts if part))


def _query_phrases(query_text: str, *, max_phrase_terms: int) -> Tuple[str, ...]:
    phrases: List[str] = []
    seen = set()

    def add_phrase(value: Any) -> None:
        cleaned = core_retrieval_phrase(value, min_terms=2)
        if not cleaned:
            return
        term_count = len(retrieval_keywords(cleaned))
        if term_count < 2 or term_count > max_phrase_terms:
            return
        lowered = cleaned.lower()
        if lowered in seen:
            return
        phrases.append(lowered)
        seen.add(lowered)

    cleaned_query = clean_retrieval_text(query_text).strip(" ?.!:")
    add_phrase(cleaned_query)
    for part in _PHRASE_SPLIT_RE.split(cleaned_query):
        add_phrase(part)
    for phrase in _QUOTE_RE.findall(str(query_text or "")):
        add_phrase(phrase)
    return tuple(phrases)


def rerank_retrieval_matches(
    query_text: str,
    matches: Sequence[Any],
    policy: RetrievalRerankPolicy,
) -> RetrievalRerankResult:
    """Rerank matches deterministically using query-term and phrase evidence."""

    items = list(matches or [])
    if not items:
        return RetrievalRerankResult(matches=[], metadata={"enabled": policy.enabled, "candidate_count": 0})
    if not policy.enabled:
        return RetrievalRerankResult(
            matches=list(items),
            metadata={"enabled": False, "candidate_count": len(items), "algorithm": "disabled"},
        )

    query_terms = retrieval_query_terms(query_text)
    query_phrases = _query_phrases(query_text, max_phrase_terms=policy.max_phrase_terms)
    max_original_score = max((match_score(match) for match in items), default=0.0)
    reranked: List[Tuple[float, int, str, Any]] = []

    for original_index, match in enumerate(items):
        body_text = clean_retrieval_text(match_text(match)).lower()
        auxiliary_text = _auxiliary_text(match).lower()
        combined_text = clean_retrieval_text(f"{body_text} {auxiliary_text}").lower()

        body_terms = set(retrieval_keywords(body_text))
        auxiliary_terms = set(retrieval_keywords(auxiliary_text))
        term_ratio = (
            len([term for term in query_terms if term in body_terms]) / float(len(query_terms))
            if query_terms
            else 0.0
        )
        metadata_ratio = (
            len([term for term in query_terms if term in auxiliary_terms]) / float(len(query_terms))
            if query_terms
            else 0.0
        )
        phrase_ratio = (
            len([phrase for phrase in query_phrases if phrase in combined_text]) / float(len(query_phrases))
            if query_phrases
            else 0.0
        )
        original_norm = max(0.0, min(1.0, match_score(match) / max_original_score)) if max_original_score > 0 else 0.0
        exact_query_bonus = 0.05 if clean_retrieval_text(query_text).lower() in combined_text else 0.0
        combined_score = (
            0.30 * original_norm
            + 0.40 * term_ratio
            + 0.20 * phrase_ratio
            + 0.10 * metadata_ratio
            + exact_query_bonus
        )
        reranked.append(
            (
                round(combined_score, 4),
                original_index,
                match_chunk_id(match),
                clone_match(match, score=round(combined_score, 4)),
            )
        )

    original_order = [match_chunk_id(match) for match in items]
    reranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    reranked_matches = [match for _, _, _, match in reranked]
    reranked_order = [match_chunk_id(match) for match in reranked_matches]
    changed_order = sum(1 for left, right in zip(original_order, reranked_order) if left != right)
    return RetrievalRerankResult(
        matches=reranked_matches,
        metadata={
            "enabled": True,
            "candidate_count": len(items),
            "algorithm": _RERANK_ALGORITHM_VERSION,
            "query_term_count": len(query_terms),
            "query_phrase_count": len(query_phrases),
            "changed_order": changed_order,
            "top_score": float(reranked[0][0]) if reranked else 0.0,
        },
    )
