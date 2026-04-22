"""Deterministic dense-projection helpers for hybrid retrieval.

The current dense backend remains intentionally lightweight: it uses stable
token and character-gram feature hashing rather than an external embedding
service. This module adds a persisted artefact format so chunk projections can
be built once during ingestion and reused during retrieval.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Tuple

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-/]{2,}")
WS_RE = re.compile(r"\s+")
DENSE_ARTIFACT_ALGORITHM = "dense_hash_v1"
DEFAULT_DENSE_DIMENSIONS = 256


def clean_dense_text(text: Any) -> str:
    """Return a normalised text form suitable for hashing."""

    return WS_RE.sub(" ", str(text or "").strip().lower())


def _text_hash(text: Any) -> str:
    return hashlib.sha256(clean_dense_text(text).encode("utf-8")).hexdigest()


def _feature_tokens(text: str) -> Iterable[str]:
    cleaned = clean_dense_text(text)
    if not cleaned:
        return []
    features: List[str] = []
    tokens = TOKEN_RE.findall(cleaned)
    for token in tokens:
        features.append(f"tok:{token}")
        for part in re.split(r"[-_/]", token):
            part = part.strip()
            if len(part) >= 3:
                features.append(f"sub:{part}")
    compact = cleaned.replace(" ", "_")
    for gram_size in (3, 4):
        if len(compact) < gram_size:
            continue
        for offset in range(0, len(compact) - gram_size + 1):
            gram = compact[offset : offset + gram_size]
            if "__" in gram:
                continue
            features.append(f"gram:{gram}")
    return features


def project_dense_text(text: Any, *, dimensions: int = DEFAULT_DENSE_DIMENSIONS) -> Dict[int, float]:
    """Project text into a stable sparse vector in a bounded feature space."""

    counts = Counter(_feature_tokens(clean_dense_text(text)))
    if not counts:
        return {}
    vector: Dict[int, float] = {}
    dimension_count = max(1, int(dimensions or DEFAULT_DENSE_DIMENSIONS))
    for feature, weight in counts.items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        slot = int.from_bytes(digest, byteorder="big", signed=False) % dimension_count
        vector[slot] = vector.get(slot, 0.0) + float(weight)
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm <= 0.0:
        return {}
    return {slot: value / norm for slot, value in vector.items()}


def cosine_similarity(left: Dict[int, float], right: Dict[int, float]) -> float:
    """Return cosine similarity for two sparse vectors."""

    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    score = 0.0
    for slot, value in left.items():
        score += value * right.get(slot, 0.0)
    return max(0.0, min(1.0, score))


def dense_chunk_id(chunk: Any) -> str:
    """Extract a stable chunk identifier from dict or object payloads."""

    value = getattr(chunk, "chunk_id", None)
    if value is None and isinstance(chunk, Mapping):
        value = chunk.get("chunk_id")
    return str(value or "").strip()


def dense_chunk_text(chunk: Any) -> str:
    """Extract chunk text from dict or object payloads."""

    value = getattr(chunk, "text", None)
    if value is None and isinstance(chunk, Mapping):
        value = chunk.get("text")
    return str(value or "").strip()


def dense_artifact_path_for_index(index_path: str) -> str:
    """Return the sidecar path used for persisted dense projections."""

    path = str(index_path or "").strip()
    if not path:
        return ""
    if path.endswith(".json"):
        return f"{path[:-5]}.dense.json"
    return f"{path}.dense.json"


@dataclass
class DenseArtifactEntry:
    """Persisted sparse vector for one chunk."""

    chunk_id: str
    slots: Tuple[int, ...]
    values: Tuple[float, ...]
    text_hash: str = ""
    _vector: Dict[int, float] = field(default_factory=dict, init=False, repr=False, compare=False)

    def vector(self) -> Dict[int, float]:
        if not self._vector:
            self._vector = dict(zip(self.slots, self.values))
        return self._vector

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "slots": list(self.slots),
            "values": list(self.values),
            "text_hash": self.text_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DenseArtifactEntry":
        chunk_id = str(payload.get("chunk_id") or "").strip()
        raw_slots = payload.get("slots") or []
        raw_values = payload.get("values") or []
        slots: List[int] = []
        values: List[float] = []
        for slot, value in zip(raw_slots, raw_values):
            try:
                slots.append(int(slot))
                values.append(float(value))
            except Exception:
                continue
        return cls(
            chunk_id=chunk_id,
            slots=tuple(slots),
            values=tuple(values),
            text_hash=str(payload.get("text_hash") or "").strip(),
        )


@dataclass
class DenseIndexArtifact:
    """Persisted dense projection bundle for one RAG index."""

    algorithm: str = DENSE_ARTIFACT_ALGORITHM
    dimensions: int = DEFAULT_DENSE_DIMENSIONS
    entries: Tuple[DenseArtifactEntry, ...] = ()
    chunk_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "dimensions": self.dimensions,
            "chunk_count": self.chunk_count,
            "metadata": dict(self.metadata or {}),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DenseIndexArtifact":
        entries: List[DenseArtifactEntry] = []
        for item in payload.get("entries") or []:
            if not isinstance(item, Mapping):
                continue
            entry = DenseArtifactEntry.from_dict(item)
            if entry.chunk_id and entry.slots and len(entry.slots) == len(entry.values):
                entries.append(entry)
        try:
            dimensions = max(1, int(payload.get("dimensions") or DEFAULT_DENSE_DIMENSIONS))
        except Exception:
            dimensions = DEFAULT_DENSE_DIMENSIONS
        try:
            chunk_count = max(0, int(payload.get("chunk_count") or len(entries)))
        except Exception:
            chunk_count = len(entries)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return cls(
            algorithm=str(payload.get("algorithm") or DENSE_ARTIFACT_ALGORITHM).strip() or DENSE_ARTIFACT_ALGORITHM,
            dimensions=dimensions,
            entries=tuple(entries),
            chunk_count=chunk_count,
            metadata=dict(metadata),
        )


def build_dense_artifact(index: Any, *, dimensions: int = DEFAULT_DENSE_DIMENSIONS) -> DenseIndexArtifact:
    """Build a persisted dense artefact from an in-memory RAG index."""

    chunks = list(getattr(index, "chunks", None) or [])
    entries: List[DenseArtifactEntry] = []
    dimensions = max(1, int(dimensions or DEFAULT_DENSE_DIMENSIONS))
    for chunk in chunks:
        chunk_id = dense_chunk_id(chunk)
        if not chunk_id:
            continue
        text = dense_chunk_text(chunk)
        vector = project_dense_text(text, dimensions=dimensions)
        if not vector:
            continue
        slots = tuple(sorted(vector))
        entries.append(
            DenseArtifactEntry(
                chunk_id=chunk_id,
                slots=slots,
                values=tuple(vector[slot] for slot in slots),
                text_hash=_text_hash(text),
            )
        )
    return DenseIndexArtifact(
        algorithm=DENSE_ARTIFACT_ALGORITHM,
        dimensions=dimensions,
        entries=tuple(entries),
        chunk_count=len(chunks),
        metadata={
            "entry_count": len(entries),
        },
    )


def attach_dense_artifact(index: Any, artifact: DenseIndexArtifact | None) -> DenseIndexArtifact | None:
    """Attach a dense artefact to an index after validating chunk identities."""

    if index is None or artifact is None:
        return None
    chunks = list(getattr(index, "chunks", None) or [])
    chunk_lookup = {dense_chunk_id(chunk): chunk for chunk in chunks if dense_chunk_id(chunk)}
    validated_entries: List[DenseArtifactEntry] = []
    for entry in artifact.entries:
        if not entry.chunk_id:
            continue
        chunk = chunk_lookup.get(entry.chunk_id)
        if chunk is None:
            continue
        if entry.text_hash and entry.text_hash != _text_hash(dense_chunk_text(chunk)):
            continue
        validated_entries.append(entry)
    attached = DenseIndexArtifact(
        algorithm=artifact.algorithm,
        dimensions=max(1, int(artifact.dimensions or DEFAULT_DENSE_DIMENSIONS)),
        entries=tuple(validated_entries),
        chunk_count=len(chunks),
        metadata={
            **dict(artifact.metadata or {}),
            "attached_entry_count": len(validated_entries),
        },
    )
    setattr(index, "dense_artifact", attached)
    setattr(index, "_dense_chunk_lookup", chunk_lookup)
    return attached


def ensure_dense_artifact(index: Any, *, dimensions: int = DEFAULT_DENSE_DIMENSIONS) -> DenseIndexArtifact:
    """Return an attached dense artefact, building one when needed."""

    existing = getattr(index, "dense_artifact", None)
    if isinstance(existing, DenseIndexArtifact) and int(existing.dimensions or 0) == max(1, int(dimensions or 1)):
        attached = attach_dense_artifact(index, existing)
        if attached is not None:
            return attached
    built = build_dense_artifact(index, dimensions=dimensions)
    attached = attach_dense_artifact(index, built)
    return attached or built
