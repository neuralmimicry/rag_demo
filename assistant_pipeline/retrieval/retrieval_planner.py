"""Retry planning and match-merging helpers for retrieval loops."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from assistant_pipeline.retrieval.coverage_grader import RetrievalCoverageGrade
from assistant_pipeline.retrieval.match_utils import clone_match, match_chunk_id, match_score
from assistant_pipeline.retrieval.text_utils import (
    clean_retrieval_text,
    core_retrieval_phrase,
    retrieval_keywords,
)

_CLAUSE_SPLIT_RE = re.compile(r"[;,\n?!]+|\b(?:and|also|plus|then|or|vs|versus)\b", re.IGNORECASE)
_QUOTE_RE = re.compile(r"['\"]([^'\"]{3,})['\"]")
_MAX_KEYWORD_WINDOW_TERMS = 4


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: Any, default: int, *, minimum: int = 0, maximum: int = 1000) -> int:
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return min(maximum, max(minimum, number))


def _clean_query(text: Any) -> str:
    return clean_retrieval_text(text)


def _keywords(text: str) -> Tuple[str, ...]:
    return retrieval_keywords(text)


@dataclass(frozen=True)
class RetrievalPlannerPolicy:
    """Runtime configuration for one retry/decomposition pass."""

    enabled: bool = False
    max_retry_queries: int = 3
    min_clause_terms: int = 2


@dataclass(frozen=True)
class RetrievalRetryPlan:
    """Retry plan produced from one insufficient retrieval result."""

    queries: Tuple[str, ...]
    reason: str
    clauses: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)


def retrieval_planner_policy_from_config(config: Mapping[str, Any] | None) -> RetrievalPlannerPolicy:
    """Build a typed retry-planner policy from runtime config."""

    values = dict(config or {})
    return RetrievalPlannerPolicy(
        enabled=_flag(values.get("retry_enabled"), False),
        max_retry_queries=_int(values.get("max_retry_queries"), 3, minimum=0, maximum=8),
        min_clause_terms=_int(values.get("min_clause_terms"), 2, minimum=1, maximum=6),
    )


def retrieval_planner_scope_fragment(policy: RetrievalPlannerPolicy) -> str:
    """Return a stable scope fragment for cache and trace grouping."""

    if not policy.enabled:
        return "retry=off"
    return f"retry=v1:max={policy.max_retry_queries}:min_terms={policy.min_clause_terms}"


def _split_clauses(query_text: str, *, min_clause_terms: int) -> Tuple[str, ...]:
    clauses: List[str] = []
    seen = set()
    for part in _CLAUSE_SPLIT_RE.split(_clean_query(query_text)):
        clause = _clean_query(part).strip(" ?.!:")
        if not clause:
            continue
        if len(_keywords(clause)) < min_clause_terms:
            continue
        lowered = clause.lower()
        if lowered in seen:
            continue
        clauses.append(clause)
        seen.add(lowered)
    return tuple(clauses)


def _core_clause(text: str, *, min_clause_terms: int) -> str:
    return core_retrieval_phrase(text, min_terms=min_clause_terms)


def _quoted_phrases(query_text: str, *, min_clause_terms: int) -> Tuple[str, ...]:
    phrases: List[str] = []
    seen = set()
    for raw in _QUOTE_RE.findall(str(query_text or "")):
        phrase = _core_clause(raw, min_clause_terms=min_clause_terms)
        if not phrase or len(_keywords(phrase)) < min_clause_terms:
            continue
        lowered = phrase.lower()
        if lowered in seen:
            continue
        phrases.append(phrase)
        seen.add(lowered)
    return tuple(phrases)


def _keyword_windows(query_text: str, *, min_clause_terms: int) -> Tuple[str, ...]:
    terms = _keywords(query_text)
    if len(terms) <= _MAX_KEYWORD_WINDOW_TERMS:
        return ()
    windows: List[str] = []
    seen = set()
    for window_size in range(_MAX_KEYWORD_WINDOW_TERMS, min_clause_terms - 1, -1):
        if window_size <= 0 or len(terms) < window_size:
            continue
        for start in range(0, len(terms) - window_size + 1):
            window = " ".join(terms[start : start + window_size]).strip()
            lowered = window.lower()
            if not window or lowered in seen:
                continue
            windows.append(window)
            seen.add(lowered)
    return tuple(windows)


def plan_retrieval_retry(
    query_text: str,
    grade: RetrievalCoverageGrade,
    policy: RetrievalPlannerPolicy,
) -> RetrievalRetryPlan:
    """Return retry queries for one additional retrieval pass."""

    if not policy.enabled or grade.sufficient or policy.max_retry_queries <= 0:
        return RetrievalRetryPlan(queries=(), reason="retry_disabled", metadata={"retry_enabled": policy.enabled})
    clauses = _split_clauses(query_text, min_clause_terms=policy.min_clause_terms)
    core_query = _core_clause(query_text, min_clause_terms=policy.min_clause_terms)
    core_clauses = tuple(
        clause
        for clause in (
            _core_clause(clause, min_clause_terms=policy.min_clause_terms)
            for clause in clauses
        )
        if clause
    )
    quoted_phrases = _quoted_phrases(query_text, min_clause_terms=policy.min_clause_terms)
    keyword_windows = _keyword_windows(query_text, min_clause_terms=policy.min_clause_terms)
    candidates: List[Tuple[str, str]] = []

    def add_candidate(value: str, reason_code: str) -> None:
        cleaned = _clean_query(value).strip(" ?.!:")
        if not cleaned:
            return
        candidates.append((cleaned, reason_code))

    for clause in core_clauses:
        if clause.lower() != _clean_query(query_text).lower():
            add_candidate(clause, "core_clause")
    for phrase in quoted_phrases:
        add_candidate(phrase, "quoted_phrase")
    if grade.missing_terms:
        add_candidate(" ".join(grade.missing_terms[: max(1, policy.max_retry_queries * 2)]), "missing_terms")
        for term in grade.missing_terms[: max(1, min(policy.max_retry_queries, 3))]:
            add_candidate(term, "missing_term")
    if core_query and core_query.lower() != _clean_query(query_text).lower():
        add_candidate(core_query, "core_query")
    if not core_clauses:
        for window in keyword_windows:
            add_candidate(window, "keyword_window")
    for clause in clauses:
        cleaned_clause = _clean_query(clause).strip(" ?.!:")
        if cleaned_clause.lower() == _clean_query(query_text).lower():
            continue
        if _core_clause(clause, min_clause_terms=policy.min_clause_terms).lower() != cleaned_clause.lower():
            continue
        add_candidate(cleaned_clause, "clause")
    keywords = _keywords(query_text)
    if keywords and not core_clauses and not grade.missing_terms:
        condensed = " ".join(keywords)
        if condensed and condensed.lower() != _clean_query(query_text).lower():
            add_candidate(condensed, "condensed")
    queries: List[str] = []
    query_reasons: List[Dict[str, str]] = []
    seen = set()
    for candidate, reason_code in candidates:
        cleaned = _clean_query(candidate)
        lowered = cleaned.lower()
        if not cleaned or lowered in seen:
            continue
        queries.append(cleaned)
        query_reasons.append({"query": cleaned, "reason": reason_code})
        seen.add(lowered)
        if len(queries) >= policy.max_retry_queries:
            break
    reason = "missing_terms"
    if len(clauses) > 1:
        reason = "multi_clause"
    elif not grade.missing_terms:
        reason = "low_coverage"
    return RetrievalRetryPlan(
        queries=tuple(queries),
        reason=reason,
        clauses=clauses,
        metadata={
            "query_count": len(queries),
            "reason": reason,
            "clauses": list(clauses),
            "core_clauses": list(core_clauses),
            "quoted_phrases": list(quoted_phrases),
            "keyword_windows": list(keyword_windows[:6]),
            "missing_terms": list(grade.missing_terms[:8]),
            "query_reasons": query_reasons,
        },
    )


def merge_retrieval_matches(match_groups: Iterable[Sequence[Any]], *, limit: int) -> List[Any]:
    """Merge retrieval matches from multiple queries without duplicating chunks."""

    merged: Dict[str, Dict[str, Any]] = {}
    for matches in match_groups:
        for match in matches:
            chunk_id = match_chunk_id(match)
            if not chunk_id:
                continue
            score = match_score(match)
            state = merged.get(chunk_id)
            if state is None:
                merged[chunk_id] = {
                    "score_sum": score,
                    "best_score": score,
                    "hit_count": 1,
                    "match": match,
                }
                continue
            state["score_sum"] += score
            state["hit_count"] += 1
            if score >= state["best_score"]:
                state["best_score"] = score
                state["match"] = match
    ranked: List[tuple[float, str, Any]] = []
    for chunk_id, state in merged.items():
        combined_score = float(state["best_score"]) + min(0.2, 0.05 * max(0, state["hit_count"] - 1))
        ranked.append((combined_score, chunk_id, clone_match(state["match"], score=round(combined_score, 4))))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [match for _, _, match in ranked[: max(0, int(limit))]]
