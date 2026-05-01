"""Conservative semantic-cache helpers for assistant and RAG responses."""

from __future__ import annotations

import copy
import difflib
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_WORD_RE = re.compile(r"[a-z0-9]{2,}")
_WS_RE = re.compile(r"\s+")
_STOPWORDS = {"a", "an", "the"}


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class SemanticCachePolicy:
    """Runtime configuration for semantic-cache lookups and writes."""

    enabled: bool = False
    ttl_hours: float = 12.0
    min_similarity: float = 0.94
    max_candidates: int = 20


@dataclass(frozen=True)
class SemanticCacheSignature:
    """Normalised query signature used for cache matching."""

    query_text: str
    normalized_query: str
    query_terms: Tuple[str, ...]
    query_hash: str


@dataclass(frozen=True)
class SemanticCacheHit:
    """Best cache hit returned from a lookup."""

    cache_id: str
    similarity: float
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticCacheLookupResult:
    """Full lookup result for tracing and service decisions."""

    signature: SemanticCacheSignature
    hit: Optional[SemanticCacheHit] = None
    candidate_count: int = 0


@dataclass(frozen=True)
class SemanticCacheWriteResult:
    """Result of writing a response into the semantic cache."""

    cache_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


def semantic_cache_policy_from_config(config: Mapping[str, Any] | None) -> SemanticCachePolicy:
    """Build a typed cache policy from runtime config."""

    values = dict(config or {})
    try:
        ttl_hours = max(0.0, float(values.get("ttl_hours") or 12.0))
    except Exception:
        ttl_hours = 12.0
    try:
        min_similarity = min(1.0, max(0.0, float(values.get("min_similarity") or 0.94)))
    except Exception:
        min_similarity = 0.94
    try:
        max_candidates = max(1, int(values.get("max_candidates") or 20))
    except Exception:
        max_candidates = 20
    return SemanticCachePolicy(
        enabled=_flag(values.get("enabled"), False),
        ttl_hours=ttl_hours,
        min_similarity=min_similarity,
        max_candidates=max_candidates,
    )


def semantic_cache_signature(query_text: str) -> SemanticCacheSignature:
    """Normalise one cache query into a deterministic signature."""

    raw_query = str(query_text or "").strip()
    lowered = raw_query.lower()
    normalized_query = _WS_RE.sub(" ", " ".join(_WORD_RE.findall(lowered))).strip()
    query_terms = tuple(dict.fromkeys(_WORD_RE.findall(normalized_query)))
    query_hash = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest() if normalized_query else ""
    return SemanticCacheSignature(
        query_text=raw_query,
        normalized_query=normalized_query,
        query_terms=query_terms,
        query_hash=query_hash,
    )


def semantic_similarity(signature: SemanticCacheSignature, candidate: Mapping[str, Any]) -> float:
    """Score a cache candidate using conservative token and string overlap."""

    normalized_candidate = str(candidate.get("normalized_query") or "").strip().lower()
    if not signature.normalized_query or not normalized_candidate:
        return 0.0
    if normalized_candidate == signature.normalized_query:
        return 1.0
    candidate_terms_raw = candidate.get("query_terms")
    if isinstance(candidate_terms_raw, list):
        candidate_terms = tuple(str(item).strip().lower() for item in candidate_terms_raw if str(item).strip())
    else:
        candidate_terms = tuple(_WORD_RE.findall(normalized_candidate))
    current_terms = set(signature.query_terms)
    other_terms = set(candidate_terms)
    jaccard_score = 0.0
    containment_score = 0.0
    if current_terms and other_terms:
        overlap = len(current_terms & other_terms)
        jaccard_score = overlap / float(len(current_terms | other_terms))
        containment_score = overlap / float(min(len(current_terms), len(other_terms)))
    char_score = difflib.SequenceMatcher(None, signature.normalized_query, normalized_candidate).ratio()
    ordered_term_score = difflib.SequenceMatcher(None, " ".join(signature.query_terms), " ".join(candidate_terms)).ratio()
    contains_bonus = 0.0
    shorter = min(len(signature.normalized_query), len(normalized_candidate))
    if shorter >= 24 and (
        signature.normalized_query in normalized_candidate or normalized_candidate in signature.normalized_query
    ):
        contains_bonus = 0.08
    signature_keywords = tuple(term for term in signature.query_terms if term not in _STOPWORDS)
    candidate_keywords = tuple(term for term in candidate_terms if term not in _STOPWORDS)
    keyword_bonus = 0.0
    if signature_keywords and candidate_keywords and signature_keywords == candidate_keywords:
        keyword_bonus = 0.05
    score = (
        (containment_score * 0.45)
        + (jaccard_score * 0.2)
        + (char_score * 0.2)
        + (ordered_term_score * 0.15)
        + contains_bonus
        + keyword_bonus
    )
    return min(1.0, round(score, 4))


def lookup_semantic_cache(
    store: Any,
    *,
    owner: str,
    route: str,
    intent: str,
    scope_key: str,
    query_text: str,
    policy: SemanticCachePolicy,
) -> SemanticCacheLookupResult:
    """Look up the best cache hit for a request scope and query."""

    signature = semantic_cache_signature(query_text)
    if not policy.enabled or store is None or not owner or not route or not scope_key or not signature.normalized_query:
        return SemanticCacheLookupResult(signature=signature, candidate_count=0)
    try:
        candidates = list(
            store.list_candidates(
                owner,
                route,
                scope_key,
                intent=intent,
                limit=policy.max_candidates,
            )
            or []
        )
    except Exception:
        return SemanticCacheLookupResult(signature=signature, candidate_count=0)
    best_hit: Optional[SemanticCacheHit] = None
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        score = semantic_similarity(signature, candidate)
        if score < policy.min_similarity:
            continue
        payload = candidate.get("response_payload") if isinstance(candidate.get("response_payload"), dict) else {}
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        hit = SemanticCacheHit(
            cache_id=str(candidate.get("cache_id") or "").strip(),
            similarity=score,
            payload=copy.deepcopy(payload),
            metadata=dict(metadata),
        )
        if best_hit is None or hit.similarity > best_hit.similarity:
            best_hit = hit
    if best_hit and best_hit.cache_id:
        try:
            store.record_hit(best_hit.cache_id)
        except Exception:
            pass
    return SemanticCacheLookupResult(signature=signature, hit=best_hit, candidate_count=len(candidates))


def store_semantic_cache(
    store: Any,
    *,
    owner: str,
    route: str,
    intent: str,
    scope_key: str,
    query_text: str,
    response_payload: Dict[str, Any],
    policy: SemanticCachePolicy,
    metadata: Optional[Dict[str, Any]] = None,
) -> SemanticCacheWriteResult:
    """Persist a response in the semantic cache when the policy allows it."""

    signature = semantic_cache_signature(query_text)
    if not policy.enabled or store is None or not owner or not route or not scope_key or not signature.normalized_query:
        return SemanticCacheWriteResult(metadata={"stored": False})
    try:
        cache_id = str(
            store.upsert_entry(
                owner,
                route,
                scope_key,
                intent=intent,
                query_text=signature.query_text,
                normalized_query=signature.normalized_query,
                query_terms=list(signature.query_terms),
                response_payload=copy.deepcopy(response_payload),
                metadata=dict(metadata or {}),
                ttl_hours=policy.ttl_hours,
            )
            or ""
        ).strip()
    except Exception:
        return SemanticCacheWriteResult(metadata={"stored": False})
    return SemanticCacheWriteResult(
        cache_id=cache_id,
        metadata={
            "stored": bool(cache_id),
            "normalized_query": signature.normalized_query,
            "query_hash": signature.query_hash,
        },
    )
