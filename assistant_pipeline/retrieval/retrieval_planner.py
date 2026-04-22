"""Retry planning and match-merging helpers for retrieval loops."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from assistant_pipeline.retrieval.coverage_grader import RetrievalCoverageGrade

_CLAUSE_SPLIT_RE = re.compile(r"[;,\n]+|\b(?:and|also|plus|then)\b", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-/]{2,}")


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
    return _WS_RE.sub(" ", str(text or "").strip())


def _keywords(text: str) -> Tuple[str, ...]:
    seen = set()
    terms: List[str] = []
    for raw in _WORD_RE.findall(_clean_query(text).lower()):
        token = raw.strip()
        if len(token) < 3 or token in seen:
            continue
        terms.append(token)
        seen.add(token)
    return tuple(terms)


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _metadata(item: Any) -> Dict[str, Any]:
    value = _field(item, "metadata", {})
    return dict(value) if isinstance(value, dict) else {}


def _citation(item: Any) -> str:
    citation = str(_field(item, "citation", "") or _metadata(item).get("citation") or "").strip()
    return citation or str(_field(item, "source", "") or "source")


def _clone_match(template: Any, *, score: float) -> Any:
    payload = {
        "chunk_id": str(_field(template, "chunk_id", "") or "").strip(),
        "source": str(_field(template, "source", "") or "").strip(),
        "score": float(score),
        "text": str(_field(template, "text", "") or ""),
        "metadata": _metadata(template),
        "citation": _citation(template),
    }
    if isinstance(template, Mapping):
        cloned = dict(template)
        cloned.update(payload)
        return cloned
    match_type = getattr(template, "__class__", None)
    if match_type is not None:
        try:
            return match_type(**payload)
        except Exception:
            pass
    return SimpleNamespace(**payload)


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


def plan_retrieval_retry(
    query_text: str,
    grade: RetrievalCoverageGrade,
    policy: RetrievalPlannerPolicy,
) -> RetrievalRetryPlan:
    """Return retry queries for one additional retrieval pass."""

    if not policy.enabled or grade.sufficient or policy.max_retry_queries <= 0:
        return RetrievalRetryPlan(queries=(), reason="retry_disabled", metadata={"retry_enabled": policy.enabled})
    clauses = _split_clauses(query_text, min_clause_terms=policy.min_clause_terms)
    candidates: List[str] = []
    for clause in clauses:
        if clause.lower() != _clean_query(query_text).lower():
            candidates.append(clause)
    if grade.missing_terms:
        candidates.append(" ".join(grade.missing_terms[: max(1, policy.max_retry_queries * 2)]))
    keywords = _keywords(query_text)
    if keywords:
        condensed = " ".join(keywords)
        if condensed and condensed.lower() != _clean_query(query_text).lower():
            candidates.append(condensed)
    queries: List[str] = []
    seen = set()
    for candidate in candidates:
        cleaned = _clean_query(candidate)
        lowered = cleaned.lower()
        if not cleaned or lowered in seen:
            continue
        queries.append(cleaned)
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
            "missing_terms": list(grade.missing_terms[:8]),
        },
    )


def merge_retrieval_matches(match_groups: Iterable[Sequence[Any]], *, limit: int) -> List[Any]:
    """Merge retrieval matches from multiple queries without duplicating chunks."""

    merged: Dict[str, Dict[str, Any]] = {}
    for matches in match_groups:
        for match in matches:
            chunk_id = str(_field(match, "chunk_id", "") or "").strip()
            if not chunk_id:
                continue
            try:
                score = float(_field(match, "score", 0.0) or 0.0)
            except Exception:
                score = 0.0
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
        ranked.append((combined_score, chunk_id, _clone_match(state["match"], score=round(combined_score, 4))))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [match for _, _, match in ranked[: max(0, int(limit))]]
