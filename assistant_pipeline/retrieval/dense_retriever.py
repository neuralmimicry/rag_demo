"""Dense retrieval helpers backed by persisted or ephemeral projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from assistant_pipeline.retrieval.dense_artifacts import (
    DenseIndexArtifact,
    cosine_similarity,
    dense_chunk_id,
    ensure_dense_artifact,
    project_dense_text,
)


@dataclass(frozen=True)
class DenseRetrievalCandidate:
    """One dense retrieval candidate backed by an original chunk payload."""

    chunk_id: str
    rank: int
    score: float
    chunk: Any


@dataclass(frozen=True)
class DenseRetrievalResult:
    """Dense retrieval output plus trace-friendly metadata."""

    candidates: List[DenseRetrievalCandidate]
    metadata: Dict[str, Any] = field(default_factory=dict)


def _chunk_lookup(index: Any) -> Dict[str, Any]:
    cached = getattr(index, "_dense_chunk_lookup", None)
    if isinstance(cached, dict) and cached:
        return cached
    lookup = {dense_chunk_id(chunk): chunk for chunk in list(getattr(index, "chunks", None) or []) if dense_chunk_id(chunk)}
    setattr(index, "_dense_chunk_lookup", lookup)
    return lookup


def _dense_backend(index: Any) -> tuple[DenseIndexArtifact | None, str]:
    artifact = getattr(index, "dense_artifact", None)
    if isinstance(artifact, DenseIndexArtifact):
        attached = ensure_dense_artifact(index, dimensions=artifact.dimensions)
        return attached, "persisted"
    return ensure_dense_artifact(index), "ephemeral"


def search_dense(index: Any, query_text: str, *, limit: int, min_score: float = 0.0) -> DenseRetrievalResult:
    """Return the top-N dense candidates for one query."""

    chunks = list(getattr(index, "chunks", None) or [])
    if not chunks or not str(query_text or "").strip() or int(limit or 0) <= 0:
        return DenseRetrievalResult(
            candidates=[],
            metadata={"candidate_count": 0, "chunk_count": len(chunks), "backend": "unavailable"},
        )
    artifact, backend = _dense_backend(index)
    if artifact is None:
        return DenseRetrievalResult(
            candidates=[],
            metadata={"candidate_count": 0, "chunk_count": len(chunks), "backend": "unavailable"},
        )
    query_vector = project_dense_text(query_text, dimensions=artifact.dimensions)
    if not query_vector:
        return DenseRetrievalResult(
            candidates=[],
            metadata={
                "candidate_count": 0,
                "chunk_count": artifact.chunk_count or len(chunks),
                "backend": backend,
                "algorithm": artifact.algorithm,
            },
        )
    lookup = _chunk_lookup(index)
    scored: List[DenseRetrievalCandidate] = []
    for entry in artifact.entries:
        chunk = lookup.get(entry.chunk_id)
        if chunk is None:
            continue
        score = cosine_similarity(query_vector, entry.vector())
        if score < float(min_score):
            continue
        scored.append(DenseRetrievalCandidate(chunk_id=entry.chunk_id, rank=0, score=score, chunk=chunk))
    scored.sort(key=lambda item: (-item.score, item.chunk_id))
    limited = [
        DenseRetrievalCandidate(chunk_id=item.chunk_id, rank=rank, score=item.score, chunk=item.chunk)
        for rank, item in enumerate(scored[: int(limit)], start=1)
    ]
    return DenseRetrievalResult(
        candidates=limited,
        metadata={
            "candidate_count": len(limited),
            "chunk_count": artifact.chunk_count or len(chunks),
            "backend": backend,
            "algorithm": artifact.algorithm,
        },
    )
