from refiner.rag_engine import RagDocument, RagIndex

from assistant_pipeline.retrieval import (
    attach_dense_artifact,
    bind_answer_citations,
    build_dense_artifact,
    grade_retrieval_coverage,
    hybrid_retrieval_policy_from_config,
    hybrid_retrieval_scope_fragment,
    merge_retrieval_matches,
    plan_retrieval_retry,
    retrieval_coverage_policy_from_config,
    retrieval_coverage_scope_fragment,
    retrieval_planner_policy_from_config,
    retrieval_planner_scope_fragment,
    retrieval_rerank_policy_from_config,
    retrieval_rerank_scope_fragment,
    retrieve_matches,
    rerank_retrieval_matches,
    search_dense,
)


class _SparseMissIndex:
    def __init__(self):
        self.chunks = [
            {
                "chunk_id": "chunk-1",
                "source": "ops.md",
                "text": "Retry failed ledger operations after the nightly sync step.",
                "metadata": {},
                "citation": "[ops.md p.1]",
            }
        ]
        self.search_calls = []

    def search(self, query: str, *, limit: int, min_score: float):
        self.search_calls.append({"query": query, "limit": limit, "min_score": min_score})
        return []


def test_search_dense_recovers_near_match_from_chunk_text() -> None:
    index = RagIndex.build(
        "ops",
        [
            RagDocument(
                doc_id="doc-1",
                source="ops.md",
                text="Retry failed ledger operations after the nightly sync step.",
                metadata={},
            )
        ],
    )

    result = search_dense(index, "retrying failed operation", limit=2, min_score=0.05)

    assert result.candidates
    assert result.candidates[0].chunk_id.startswith("doc-1:")
    assert result.metadata["chunk_count"] == len(index.chunks)
    assert result.metadata["backend"] == "ephemeral"


def test_search_dense_uses_attached_dense_artifact_when_available() -> None:
    index = RagIndex.build(
        "ops",
        [
            RagDocument(
                doc_id="doc-1",
                source="ops.md",
                text="Retry failed ledger operations after the nightly sync step.",
                metadata={},
            )
        ],
    )
    attach_dense_artifact(index, build_dense_artifact(index))

    result = search_dense(index, "retrying failed operation", limit=2, min_score=0.05)

    assert result.candidates
    assert result.metadata["backend"] == "persisted"
    assert result.metadata["algorithm"] == "dense_hash_v1"


def test_retrieve_matches_can_return_dense_only_candidates() -> None:
    index = _SparseMissIndex()
    policy = hybrid_retrieval_policy_from_config({"enabled": True, "min_dense_score": 0.05})

    result = retrieve_matches(index, "retrying failed operation", limit=1, min_score=0.0, policy=policy)

    assert result.metadata["strategy"] == "dense_only"
    assert result.metadata["dense_candidate_count"] == 1
    assert result.matches[0]["chunk_id"] == "chunk-1"


def test_hybrid_retrieval_scope_fragment_changes_with_policy() -> None:
    sparse_policy = hybrid_retrieval_policy_from_config({"enabled": False})
    hybrid_policy = hybrid_retrieval_policy_from_config({"enabled": True, "candidate_multiplier": 6})

    assert hybrid_retrieval_scope_fragment(sparse_policy) == "retrieval=sparse"
    assert "retrieval=hybrid_v1" in hybrid_retrieval_scope_fragment(hybrid_policy)
    assert "cm=6" in hybrid_retrieval_scope_fragment(hybrid_policy)


def test_grade_retrieval_coverage_marks_partial_when_terms_are_missing() -> None:
    policy = retrieval_coverage_policy_from_config({"coverage_enabled": True, "min_query_term_coverage": 0.8})

    grade = grade_retrieval_coverage(
        "customer sync failures",
        [{"chunk_id": "chunk-1", "text": "Customer sync happens every night.", "score": 0.9}],
        policy,
    )

    assert grade.status == "partial"
    assert grade.sufficient is False
    assert "failure" in grade.missing_terms
    assert grade.coverage_ratio < 0.8


def test_plan_retrieval_retry_uses_missing_terms_and_clause_decomposition() -> None:
    coverage_policy = retrieval_coverage_policy_from_config({"coverage_enabled": True, "min_query_term_coverage": 0.9})
    planner_policy = retrieval_planner_policy_from_config({"retry_enabled": True, "max_retry_queries": 3})
    grade = grade_retrieval_coverage(
        "customer sync and failures",
        [{"chunk_id": "chunk-1", "text": "Customer sync happens every night.", "score": 0.9}],
        coverage_policy,
    )

    plan = plan_retrieval_retry("customer sync and failures", grade, planner_policy)

    assert plan.reason in {"multi_clause", "missing_terms"}
    assert "customer sync" in plan.queries
    assert any("failure" in query for query in plan.queries)


def test_plan_retrieval_retry_extracts_core_clauses_and_quoted_phrases() -> None:
    coverage_policy = retrieval_coverage_policy_from_config({"coverage_enabled": True, "min_query_term_coverage": 0.95})
    planner_policy = retrieval_planner_policy_from_config({"retry_enabled": True, "max_retry_queries": 2})
    grade = grade_retrieval_coverage(
        'How does customer sync work and what about "retry queue" status?',
        [{"chunk_id": "chunk-1", "text": "Customer sync happens every night.", "score": 0.9}],
        coverage_policy,
    )

    plan = plan_retrieval_retry('How does customer sync work and what about "retry queue" status?', grade, planner_policy)

    assert plan.reason == "multi_clause"
    assert plan.queries == ("customer sync", "retry queue status")
    assert "retry queue" in plan.metadata["quoted_phrases"]


def test_rerank_retrieval_matches_promotes_stronger_query_evidence() -> None:
    policy = retrieval_rerank_policy_from_config({"rerank_enabled": True})
    matches = [
        {
            "chunk_id": "chunk-1",
            "source": "ops.md",
            "citation": "[Ops Guide p.3]",
            "text": "Retry happens nightly after processing.",
            "metadata": {},
            "score": 0.95,
        },
        {
            "chunk_id": "chunk-2",
            "source": "ops.md",
            "citation": "[Ops Guide p.7]",
            "text": "Customer sync failures move to the retry queue for another pass.",
            "metadata": {"heading_path": ["Customer Sync Failures"]},
            "score": 0.72,
        },
    ]

    result = rerank_retrieval_matches("customer sync failures retry queue", matches, policy)

    assert [match["chunk_id"] for match in result.matches] == ["chunk-2", "chunk-1"]
    assert result.metadata["algorithm"] == "rerank_v1"
    assert result.metadata["changed_order"] >= 2


def test_merge_retrieval_matches_deduplicates_chunks_and_keeps_best_score() -> None:
    merged = merge_retrieval_matches(
        [
            [{"chunk_id": "chunk-1", "text": "Customer sync happens every night.", "score": 0.4}],
            [
                {"chunk_id": "chunk-1", "text": "Customer sync happens every night.", "score": 0.7},
                {"chunk_id": "chunk-2", "text": "Failures move to the retry queue.", "score": 0.6},
            ],
        ],
        limit=3,
    )

    assert [match["chunk_id"] for match in merged] == ["chunk-1", "chunk-2"]
    assert merged[0]["score"] >= 0.7


def test_retrieval_scope_fragments_cover_hybrid_coverage_and_retry_settings() -> None:
    coverage_policy = retrieval_coverage_policy_from_config(
        {"coverage_enabled": True, "min_query_term_coverage": 0.75, "refuse_on_insufficient": True}
    )
    planner_policy = retrieval_planner_policy_from_config({"retry_enabled": True, "max_retry_queries": 2})
    rerank_policy = retrieval_rerank_policy_from_config({"rerank_enabled": True, "rerank_max_phrase_terms": 5})

    assert "coverage=v1" in retrieval_coverage_scope_fragment(coverage_policy)
    assert "q=0.750" in retrieval_coverage_scope_fragment(coverage_policy)
    assert retrieval_planner_scope_fragment(planner_policy) == "retry=v1:max=2:min_terms=2"
    assert retrieval_rerank_scope_fragment(rerank_policy) == "rerank=rerank_v1:max_terms=5"


def test_bind_answer_citations_matches_explicit_and_implicit_claims() -> None:
    matches = [
        {
            "chunk_id": "chunk-sync",
            "source": "ops.md",
            "citation": "[Ops Guide p.4]",
            "text": "Customer sync runs nightly after the ledger checkpoint.",
            "metadata": {"heading_path": ["Customer Sync"]},
            "score": 0.91,
        },
        {
            "chunk_id": "chunk-retry",
            "source": "ops.md",
            "citation": "[Ops Guide p.7]",
            "text": "Failures move to the retry queue for another pass.",
            "metadata": {"heading_path": ["Failure Handling"]},
            "score": 0.88,
        },
    ]

    binding = bind_answer_citations(
        "Customer sync runs nightly [Ops Guide p.4]. Failures move to the retry queue.",
        matches,
    )

    claim_bindings = {
        item["claim_id"]: [citation["chunk_id"] for citation in item.get("citations") or []]
        for item in binding.claim_bindings
    }
    assert [source["chunk_id"] for source in binding.citations] == ["chunk-sync", "chunk-retry"]
    assert claim_bindings["claim-001"] == ["chunk-sync"]
    assert claim_bindings["claim-002"] == ["chunk-retry"]
    assert binding.metadata["binding_coverage_ratio"] == 1.0
