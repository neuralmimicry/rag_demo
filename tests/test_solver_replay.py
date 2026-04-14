from solver_command_policy import evaluate_command_policy
from solver_command_trust import CommandTrustStore
from solver_memory import SolverEpisode, SolverEpisodeStore
from solver_replay import build_solver_replay_analysis


def _episode(
    *,
    episode_id: str,
    source_path: str,
    iteration: int,
    outcome: str,
    summary: str,
    verification_failures=None,
    metadata=None,
):
    return SolverEpisode(
        episode_id=episode_id,
        source_path=source_path,
        iteration=iteration,
        created_at=f"2026-04-14T0{iteration}:00:00Z",
        outcome=outcome,
        summary=summary,
        requirement_ids=["REQ-001"],
        verification_failures=list(verification_failures or []),
        metadata=dict(metadata or {}),
    )


def test_build_solver_replay_analysis_surfaces_loops(tmp_path):
    episode_store = SolverEpisodeStore(str(tmp_path / "episodes.jsonl"), max_entries=20, compact_every=1)
    trust_store = CommandTrustStore(str(tmp_path / "command_trust.json"), max_shapes=20)
    decision = evaluate_command_policy("python -m pytest")
    trust_store.record(decision, success=False, exit_code=1)
    trust_store.record(decision, success=False, exit_code=1)

    command_result = {
        "shape": "python -m pytest",
        "category": "verification",
        "policy_risk": "low",
        "effective_risk": "medium",
        "trust_level": "watch",
        "trust_score": 0.33,
        "success": False,
    }
    episode_store.record(
        _episode(
            episode_id="ep-1",
            source_path="req/a.md",
            iteration=1,
            outcome="failure",
            summary="Verification failed on parser checks.",
            verification_failures=["parser checks failed"],
            metadata={
                "prompt_budget": {"omitted_sections": ["repo_context"]},
                "command_results": [command_result],
            },
        )
    )
    episode_store.record(
        _episode(
            episode_id="ep-2",
            source_path="req/a.md",
            iteration=2,
            outcome="deferred",
            summary="Source deferred after repeated verification failures.",
            verification_failures=["parser checks failed"],
            metadata={
                "prompt_budget": {"omitted_sections": ["repo_context", "research"]},
                "command_results": [command_result],
            },
        )
    )
    episode_store.record(
        _episode(
            episode_id="ep-3",
            source_path="req/b.md",
            iteration=1,
            outcome="success",
            summary="UI copy update completed successfully.",
            metadata={"prompt_budget": {"omitted_sections": []}},
        )
    )

    analysis = build_solver_replay_analysis(
        episode_store,
        command_trust_store=trust_store,
        limit=10,
    )

    assert analysis["outcomes"]["failure"] == 1
    assert analysis["outcomes"]["deferred"] == 1
    assert analysis["sources_needing_attention"][0]["source_path"] == "req/a.md"
    assert analysis["top_verification_failures"][0]["issue"] == "parser checks failed"
    assert analysis["prompt_budget"]["top_omitted_sections"][0]["section"] == "repo_context"
    assert analysis["command_patterns"][0]["shape"] == "python -m pytest"
    assert analysis["command_patterns"][0]["trust_level"] == "watch"
    assert analysis["recommendations"]
