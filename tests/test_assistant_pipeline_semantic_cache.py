from assistant_pipeline.cache import (
    lookup_semantic_cache,
    semantic_cache_policy_from_config,
    semantic_cache_signature,
    semantic_similarity,
    store_semantic_cache,
)


class _FakeCacheStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.list_calls = []
        self.upsert_calls = []
        self.hit_calls = []

    def list_candidates(self, owner, route, scope_key, *, intent="", limit=20):
        self.list_calls.append(
            {
                "owner": owner,
                "route": route,
                "scope_key": scope_key,
                "intent": intent,
                "limit": limit,
            }
        )
        return list(self.rows[:limit])

    def upsert_entry(self, owner, route, scope_key, **kwargs):
        self.upsert_calls.append({"owner": owner, "route": route, "scope_key": scope_key, **kwargs})
        return "cache-1"

    def record_hit(self, cache_id):
        self.hit_calls.append(cache_id)


def test_semantic_cache_signature_normalises_queries() -> None:
    signature = semantic_cache_signature("  How does the customer sync work?  ")

    assert signature.normalized_query == "how does the customer sync work"
    assert signature.query_terms == ("how", "does", "the", "customer", "sync", "work")
    assert signature.query_hash


def test_semantic_similarity_scores_near_matches_above_threshold() -> None:
    current = semantic_cache_signature("How does customer sync work")
    candidate = {
        "normalized_query": "how does the customer sync work",
        "query_terms": ["how", "does", "the", "customer", "sync", "work"],
    }

    score = semantic_similarity(current, candidate)

    assert score >= 0.94


def test_lookup_semantic_cache_returns_best_hit_and_records_hit() -> None:
    store = _FakeCacheStore(
        rows=[
            {
                "cache_id": "cache-1",
                "normalized_query": "how does the customer sync work",
                "query_terms": ["how", "does", "the", "customer", "sync", "work"],
                "response_payload": {"answer": "Use the overnight sync window.", "rag_matches": []},
                "metadata": {"rag_index": "ops"},
            }
        ]
    )
    policy = semantic_cache_policy_from_config({"enabled": True, "min_similarity": 0.9})

    result = lookup_semantic_cache(
        store,
        owner="alice",
        route="assistant_rag_mcp",
        intent="assistant_rag_mcp:rag_grounded",
        scope_key="assistant_rag_mcp:ops:version-1",
        query_text="How does customer sync work?",
        policy=policy,
    )

    assert result.hit is not None
    assert result.hit.payload["answer"] == "Use the overnight sync window."
    assert store.hit_calls == ["cache-1"]


def test_store_semantic_cache_writes_normalised_query_and_payload() -> None:
    store = _FakeCacheStore()
    policy = semantic_cache_policy_from_config({"enabled": True, "ttl_hours": 6})

    result = store_semantic_cache(
        store,
        owner="alice",
        route="rag_query",
        intent="rag_query:retrieval",
        scope_key="rag_query:ops:version-1",
        query_text="How does the customer sync work?",
        response_payload={"name": "ops", "query": "How does the customer sync work?", "matches": [], "context": "..."},
        policy=policy,
        metadata={"version_id": "version-1"},
    )

    assert result.cache_id == "cache-1"
    assert store.upsert_calls[0]["normalized_query"] == "how does the customer sync work"
    assert store.upsert_calls[0]["ttl_hours"] == 6.0
