"""Deterministic retrieval-coverage grading helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from assistant_pipeline.retrieval.match_utils import match_text
from assistant_pipeline.retrieval.text_utils import retrieval_keywords, retrieval_query_terms


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _float(value: Any, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = float(default)
    return min(maximum, max(minimum, number))


def _int(value: Any, default: int, *, minimum: int = 0, maximum: int = 1000) -> int:
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return min(maximum, max(minimum, number))


def _evidence_terms(matches: Sequence[Any]) -> Tuple[str, ...]:
    terms: List[str] = []
    seen = set()
    for match in matches:
        for canonical in retrieval_keywords(match_text(match)):
            if canonical in seen:
                continue
            terms.append(canonical)
            seen.add(canonical)
    return tuple(terms)


@dataclass(frozen=True)
class RetrievalCoveragePolicy:
    """Runtime configuration for retrieval-coverage grading."""

    enabled: bool = False
    min_query_term_coverage: float = 0.5
    min_match_count: int = 1
    min_context_chars: int = 24
    refuse_on_insufficient: bool = True


@dataclass(frozen=True)
class RetrievalCoverageGrade:
    """Coverage grade for one retrieval result set."""

    status: str
    sufficient: bool
    coverage_ratio: float
    query_terms: Tuple[str, ...]
    matched_terms: Tuple[str, ...]
    missing_terms: Tuple[str, ...]
    match_count: int
    context_chars: int
    metadata: Dict[str, Any] = field(default_factory=dict)


def retrieval_coverage_policy_from_config(config: Mapping[str, Any] | None) -> RetrievalCoveragePolicy:
    """Build a typed coverage policy from runtime config."""

    values = dict(config or {})
    return RetrievalCoveragePolicy(
        enabled=_flag(values.get("coverage_enabled"), False),
        min_query_term_coverage=_float(values.get("min_query_term_coverage"), 0.5),
        min_match_count=_int(values.get("min_match_count"), 1, minimum=0, maximum=10),
        min_context_chars=_int(values.get("min_context_chars"), 24, minimum=0, maximum=8000),
        refuse_on_insufficient=_flag(values.get("refuse_on_insufficient"), True),
    )


def retrieval_coverage_scope_fragment(policy: RetrievalCoveragePolicy) -> str:
    """Return a stable scope fragment for cache and trace grouping."""

    if not policy.enabled:
        return "coverage=off"
    return (
        f"coverage=v1:q={policy.min_query_term_coverage:.3f}:"
        f"m={policy.min_match_count}:c={policy.min_context_chars}:"
        f"r={1 if policy.refuse_on_insufficient else 0}"
    )


def grade_retrieval_coverage(
    query_text: str,
    matches: Sequence[Any],
    policy: RetrievalCoveragePolicy,
) -> RetrievalCoverageGrade:
    """Grade how well retrieved evidence covers the current query."""

    query_terms = retrieval_query_terms(query_text)
    evidence_terms = _evidence_terms(matches)
    evidence_set = set(evidence_terms)
    matched_terms = tuple(term for term in query_terms if term in evidence_set)
    missing_terms = tuple(term for term in query_terms if term not in evidence_set)
    match_count = len(matches)
    context_chars = sum(len(match_text(match).strip()) for match in matches if match_text(match).strip())
    coverage_ratio = 1.0 if not query_terms and match_count > 0 else 0.0
    if query_terms:
        coverage_ratio = len(matched_terms) / float(len(query_terms))
    sufficient = (
        match_count >= policy.min_match_count
        and context_chars >= policy.min_context_chars
        and coverage_ratio >= policy.min_query_term_coverage
    )
    if sufficient:
        status = "sufficient"
    elif match_count > 0 or context_chars > 0 or matched_terms:
        status = "partial"
    else:
        status = "insufficient"
    return RetrievalCoverageGrade(
        status=status,
        sufficient=sufficient,
        coverage_ratio=round(coverage_ratio, 4),
        query_terms=query_terms,
        matched_terms=matched_terms,
        missing_terms=missing_terms,
        match_count=match_count,
        context_chars=context_chars,
        metadata={
            "status": status,
            "sufficient": sufficient,
            "coverage_ratio": round(coverage_ratio, 4),
            "match_count": match_count,
            "context_chars": context_chars,
            "query_term_count": len(query_terms),
            "matched_term_count": len(matched_terms),
            "missing_terms": list(missing_terms[:8]),
        },
    )
