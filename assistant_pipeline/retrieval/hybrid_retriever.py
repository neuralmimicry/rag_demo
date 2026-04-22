"""Hybrid retrieval coordinator for assistant and RAG routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional

from assistant_pipeline.retrieval.dense_retriever import DenseRetrievalCandidate, search_dense
from assistant_pipeline.retrieval.sparse_retriever import SparseRetrievalCandidate, search_sparse

_RRF_K = 60.0
_HYBRID_ALGORITHM_VERSION = "hybrid_v1"


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


def _int(value: Any, default: int, *, minimum: int = 1, maximum: int = 1000) -> int:
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return min(maximum, max(minimum, number))


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
class HybridRetrievalPolicy:
    """Runtime configuration for the retrieval coordinator."""

    enabled: bool = False
    sparse_weight: float = 0.65
    dense_weight: float = 0.35
    candidate_multiplier: int = 4
    min_dense_score: float = 0.18


@dataclass(frozen=True)
class HybridRetrievalResult:
    """Retrieved matches plus metadata for tracing and cache scoping."""

    matches: List[Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


def hybrid_retrieval_policy_from_config(config: Mapping[str, Any] | None) -> HybridRetrievalPolicy:
    """Build a typed retrieval policy from runtime config."""

    values = dict(config or {})
    sparse_weight = _float(values.get("sparse_weight"), 0.65)
    dense_weight = _float(values.get("dense_weight"), 0.35)
    total = sparse_weight + dense_weight
    if total <= 0.0:
        sparse_weight, dense_weight = 1.0, 0.0
    else:
        sparse_weight = sparse_weight / total
        dense_weight = dense_weight / total
    return HybridRetrievalPolicy(
        enabled=_flag(values.get("enabled"), False),
        sparse_weight=sparse_weight,
        dense_weight=dense_weight,
        candidate_multiplier=_int(values.get("candidate_multiplier"), 4, minimum=1, maximum=12),
        min_dense_score=_float(values.get("min_dense_score"), 0.18, minimum=0.0, maximum=1.0),
    )


def hybrid_retrieval_scope_fragment(policy: HybridRetrievalPolicy) -> str:
    """Return a stable cache-scope fragment for the active retrieval strategy."""

    if not policy.enabled:
        return "retrieval=sparse"
    return (
        f"retrieval={_HYBRID_ALGORITHM_VERSION}:"
        f"sw={policy.sparse_weight:.3f}:dw={policy.dense_weight:.3f}:"
        f"cm={policy.candidate_multiplier}:md={policy.min_dense_score:.3f}"
    )


def _candidate_rank_score(rank: int, weight: float) -> float:
    if rank <= 0 or weight <= 0.0:
        return 0.0
    return weight / (_RRF_K + float(rank))


def _candidate_normalised_score(score: float, ceiling: float) -> float:
    if ceiling <= 0.0:
        return 0.0
    return max(0.0, min(1.0, float(score) / ceiling))


def _fused_match(
    template: Any,
    *,
    sparse_score: float,
    dense_score: float,
    sparse_rank: int,
    dense_rank: int,
    max_sparse_score: float,
    max_dense_score: float,
    policy: HybridRetrievalPolicy,
) -> Any:
    combined_score = (
        policy.sparse_weight * _candidate_normalised_score(sparse_score, max_sparse_score)
        + policy.dense_weight * _candidate_normalised_score(dense_score, max_dense_score)
        + _candidate_rank_score(sparse_rank, policy.sparse_weight)
        + _candidate_rank_score(dense_rank, policy.dense_weight)
    )
    return _clone_match(template, score=round(combined_score, 4))


def retrieve_matches(index: Any, query_text: str, *, limit: int, min_score: float, policy: HybridRetrievalPolicy) -> HybridRetrievalResult:
    """Return sparse or hybrid matches while preserving sparse-first behaviour."""

    candidate_limit = max(int(limit or 0), int(limit or 0) * policy.candidate_multiplier)
    sparse = search_sparse(index, query_text, limit=max(1, candidate_limit), min_score=min_score)
    if not policy.enabled:
        return HybridRetrievalResult(
            matches=[candidate.match for candidate in sparse.candidates[: int(limit)]],
            metadata={
                "strategy": "sparse",
                "sparse_candidate_count": len(sparse.candidates),
                "dense_candidate_count": 0,
                "fused_candidate_count": len(sparse.candidates[: int(limit)]),
                "algorithm": "sparse_only",
            },
        )

    dense = search_dense(index, query_text, limit=max(1, candidate_limit), min_score=policy.min_dense_score)
    if not dense.candidates:
        return HybridRetrievalResult(
            matches=[candidate.match for candidate in sparse.candidates[: int(limit)]],
            metadata={
                "strategy": "sparse",
                "sparse_candidate_count": len(sparse.candidates),
                "dense_candidate_count": 0,
                "fused_candidate_count": len(sparse.candidates[: int(limit)]),
                "algorithm": f"{_HYBRID_ALGORITHM_VERSION}:sparse_fallback",
                "dense_backend": str(dense.metadata.get("backend") or ""),
                "dense_algorithm": str(dense.metadata.get("algorithm") or ""),
            },
        )

    sparse_by_id = {candidate.chunk_id: candidate for candidate in sparse.candidates}
    dense_by_id = {candidate.chunk_id: candidate for candidate in dense.candidates}
    chunk_ids = list(dict.fromkeys([*sparse_by_id.keys(), *dense_by_id.keys()]))
    max_sparse_score = max([candidate.score for candidate in sparse.candidates], default=0.0)
    max_dense_score = max([candidate.score for candidate in dense.candidates], default=0.0)
    fused: List[tuple[float, str, Any]] = []
    for chunk_id in chunk_ids:
        sparse_candidate: Optional[SparseRetrievalCandidate] = sparse_by_id.get(chunk_id)
        dense_candidate: Optional[DenseRetrievalCandidate] = dense_by_id.get(chunk_id)
        template = sparse_candidate.match if sparse_candidate is not None else dense_candidate.chunk if dense_candidate is not None else None
        if template is None:
            continue
        match = _fused_match(
            template,
            sparse_score=sparse_candidate.score if sparse_candidate is not None else 0.0,
            dense_score=dense_candidate.score if dense_candidate is not None else 0.0,
            sparse_rank=sparse_candidate.rank if sparse_candidate is not None else 0,
            dense_rank=dense_candidate.rank if dense_candidate is not None else 0,
            max_sparse_score=max_sparse_score,
            max_dense_score=max_dense_score,
            policy=policy,
        )
        fused_score = float(_field(match, "score", 0.0) or 0.0)
        fused.append((fused_score, chunk_id, match))
    fused.sort(key=lambda item: (-item[0], item[1]))
    limited = [match for _, _, match in fused[: int(limit)]]
    sparse_count = len(sparse.candidates)
    dense_count = len(dense.candidates)
    strategy = "hybrid"
    if sparse_count == 0 and dense_count > 0:
        strategy = "dense_only"
    return HybridRetrievalResult(
        matches=limited,
        metadata={
            "strategy": strategy,
            "sparse_candidate_count": sparse_count,
            "dense_candidate_count": dense_count,
            "fused_candidate_count": len(limited),
            "algorithm": _HYBRID_ALGORITHM_VERSION,
            "dense_backend": str(dense.metadata.get("backend") or ""),
            "dense_algorithm": str(dense.metadata.get("algorithm") or ""),
        },
    )
