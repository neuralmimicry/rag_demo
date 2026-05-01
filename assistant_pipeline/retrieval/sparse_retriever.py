"""Sparse retrieval wrapper around the existing RAG index contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


def _match_score(match: Any) -> float:
    value = getattr(match, "score", None)
    if value is None and isinstance(match, dict):
        value = match.get("score")
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _chunk_id(match: Any) -> str:
    value = getattr(match, "chunk_id", None)
    if value is None and isinstance(match, dict):
        value = match.get("chunk_id")
    return str(value or "").strip()


@dataclass(frozen=True)
class SparseRetrievalCandidate:
    """One sparse retrieval candidate with its original result payload."""

    chunk_id: str
    rank: int
    score: float
    match: Any


@dataclass(frozen=True)
class SparseRetrievalResult:
    """Sparse retrieval output plus trace-friendly metadata."""

    candidates: List[SparseRetrievalCandidate]
    metadata: Dict[str, Any] = field(default_factory=dict)


def search_sparse(index: Any, query_text: str, *, limit: int, min_score: float = 0.0) -> SparseRetrievalResult:
    """Run the existing sparse retrieval path and wrap the results consistently."""

    if index is None or not str(query_text or "").strip() or int(limit or 0) <= 0:
        return SparseRetrievalResult(candidates=[], metadata={"strategy": "sparse", "candidate_count": 0})
    raw_matches = list(index.search(str(query_text), limit=int(limit), min_score=float(min_score)) or [])
    candidates: List[SparseRetrievalCandidate] = []
    for rank, match in enumerate(raw_matches, start=1):
        chunk_id = _chunk_id(match)
        if not chunk_id:
            continue
        candidates.append(
            SparseRetrievalCandidate(
                chunk_id=chunk_id,
                rank=rank,
                score=_match_score(match),
                match=match,
            )
        )
    return SparseRetrievalResult(
        candidates=candidates,
        metadata={
            "strategy": "sparse",
            "candidate_count": len(candidates),
        },
    )
