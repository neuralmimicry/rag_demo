import json
from pathlib import Path

from assistant_pipeline.memory.query_rewriter import rewrite_query
from assistant_pipeline.retrieval import bind_answer_citations
from assistant_pipeline.security import apply_tool_use_guard, assistant_security_policy_from_config


EVAL_ROOT = Path(__file__).parent / "evals" / "assistant_rag"


def _load_fixture(name: str):
    with (EVAL_ROOT / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_assistant_rag_citation_eval_cases() -> None:
    for case in _load_fixture("citation_cases.json"):
        binding = bind_answer_citations(case["answer"], case["matches"])
        bindings = {
            item["claim_id"]: [citation["chunk_id"] for citation in item.get("citations") or []]
            for item in binding.claim_bindings
        }

        assert len(binding.citations) == case["expected"]["citation_count"], case["id"]
        assert binding.metadata["claim_count"] == case["expected"]["claim_count"], case["id"]
        assert binding.metadata["bound_claim_count"] == case["expected"]["bound_claim_count"], case["id"]
        assert binding.metadata["binding_coverage_ratio"] == case["expected"]["binding_coverage_ratio"], case["id"]
        assert bindings == case["expected"]["bindings"], case["id"]


def test_assistant_rag_rewrite_eval_cases() -> None:
    for case in _load_fixture("rewrite_cases.json"):
        rewrite = rewrite_query(case["query"], case["history"])

        assert rewrite.rewritten is case["expected"]["rewritten"], case["id"]
        assert rewrite.retrieval_query == case["expected"]["retrieval_query"], case["id"]
        assert rewrite.reason == case["expected"]["reason"], case["id"]


def test_assistant_rag_tool_refusal_eval_cases() -> None:
    for case in _load_fixture("tool_refusal_cases.json"):
        policy = assistant_security_policy_from_config(case["policy"])
        result = apply_tool_use_guard(
            route="assistant_rag_mcp",
            prompt=case["prompt"],
            mcp_request=case["mcp_request"],
            is_admin_user=case["is_admin_user"],
            policy=policy,
        )

        assert result.allowed is case["expected"]["allowed"], case["id"]
        assert result.metadata["risk_level"] == case["expected"]["risk_level"], case["id"]
        expected_error_code = case["expected"].get("error_code")
        if expected_error_code is not None:
            assert result.error_code == expected_error_code, case["id"]
