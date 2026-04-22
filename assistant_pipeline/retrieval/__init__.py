"""Retrieval helpers for assistant and RAG routes.

The sparse path continues to use the existing lexical `RagIndex.search()`
contract. The dense path now reuses the same deterministic hashed projection as
before, but can persist chunk projections as sidecar artefacts so retrieval
does not have to rebuild them on every request.
"""

from assistant_pipeline.retrieval.dense_artifacts import (
    DEFAULT_DENSE_DIMENSIONS,
    DENSE_ARTIFACT_ALGORITHM,
    DenseArtifactEntry,
    DenseIndexArtifact,
    attach_dense_artifact,
    build_dense_artifact,
    dense_artifact_path_for_index,
    ensure_dense_artifact,
)
from assistant_pipeline.retrieval.coverage_grader import (
    RetrievalCoverageGrade,
    RetrievalCoveragePolicy,
    grade_retrieval_coverage,
    retrieval_coverage_policy_from_config,
    retrieval_coverage_scope_fragment,
)
from assistant_pipeline.retrieval.dense_retriever import (
    DenseRetrievalCandidate,
    DenseRetrievalResult,
    search_dense,
)
from assistant_pipeline.retrieval.hybrid_retriever import (
    HybridRetrievalPolicy,
    HybridRetrievalResult,
    hybrid_retrieval_policy_from_config,
    hybrid_retrieval_scope_fragment,
    retrieve_matches,
)
from assistant_pipeline.retrieval.retrieval_planner import (
    RetrievalPlannerPolicy,
    RetrievalRetryPlan,
    merge_retrieval_matches,
    plan_retrieval_retry,
    retrieval_planner_policy_from_config,
    retrieval_planner_scope_fragment,
)
from assistant_pipeline.retrieval.sparse_retriever import (
    SparseRetrievalCandidate,
    SparseRetrievalResult,
    search_sparse,
)

__all__ = [
    "DEFAULT_DENSE_DIMENSIONS",
    "DENSE_ARTIFACT_ALGORITHM",
    "DenseArtifactEntry",
    "DenseRetrievalCandidate",
    "DenseRetrievalResult",
    "DenseIndexArtifact",
    "RetrievalCoverageGrade",
    "RetrievalCoveragePolicy",
    "HybridRetrievalPolicy",
    "HybridRetrievalResult",
    "RetrievalPlannerPolicy",
    "RetrievalRetryPlan",
    "SparseRetrievalCandidate",
    "SparseRetrievalResult",
    "attach_dense_artifact",
    "build_dense_artifact",
    "dense_artifact_path_for_index",
    "ensure_dense_artifact",
    "grade_retrieval_coverage",
    "hybrid_retrieval_policy_from_config",
    "hybrid_retrieval_scope_fragment",
    "merge_retrieval_matches",
    "plan_retrieval_retry",
    "retrieve_matches",
    "retrieval_coverage_policy_from_config",
    "retrieval_coverage_scope_fragment",
    "retrieval_planner_policy_from_config",
    "retrieval_planner_scope_fragment",
    "search_dense",
    "search_sparse",
]
