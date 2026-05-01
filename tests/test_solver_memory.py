from refiner.solver_memory import SolverEpisode, SolverEpisodeStore


def _episode(
    *,
    episode_id: str,
    source_path: str,
    iteration: int,
    outcome: str,
    summary: str,
    requirement_ids=None,
    metadata=None,
):
    return SolverEpisode(
        episode_id=episode_id,
        source_path=source_path,
        iteration=iteration,
        created_at=f"2026-04-14T0{iteration}:00:00Z",
        outcome=outcome,
        summary=summary,
        requirement_ids=list(requirement_ids or []),
        modified_files=[f"{source_path}.py"],
        commands=["python -m pytest"],
        verification_failures=["assertion failed"] if outcome == "failure" else [],
        metadata=dict(metadata or {}),
    )


def test_solver_episode_store_search_prefers_same_source_and_requirement_ids(tmp_path):
    store = SolverEpisodeStore(str(tmp_path / "episodes.jsonl"), max_entries=10, compact_every=1)
    store.record(
        _episode(
            episode_id="ep-1",
            source_path="requirements/api.md",
            iteration=1,
            outcome="failure",
            summary="Parser fix failed verification on API payload handling.",
            requirement_ids=["REQ-001"],
        )
    )
    store.record(
        _episode(
            episode_id="ep-2",
            source_path="requirements/ui.md",
            iteration=1,
            outcome="success",
            summary="Landing page copy update completed successfully.",
            requirement_ids=["REQ-200"],
        )
    )

    results = store.search(
        "API payload parser verification failure",
        source_path="requirements/api.md",
        requirement_ids=["REQ-001"],
        limit=2,
    )

    assert results
    assert results[0].source_path == "requirements/api.md"
    assert results[0].requirement_ids == ["REQ-001"]


def test_solver_episode_store_compacts_and_formats_prompt_context(tmp_path):
    path = tmp_path / "episodes.jsonl"
    store = SolverEpisodeStore(str(path), max_entries=2, compact_every=1)
    store.record(
        _episode(
            episode_id="ep-1",
            source_path="req/a.md",
            iteration=1,
            outcome="partial",
            summary="First attempt.",
            requirement_ids=["REQ-001"],
        )
    )
    store.record(
        _episode(
            episode_id="ep-2",
            source_path="req/a.md",
            iteration=2,
            outcome="failure",
            summary="Second attempt failed.",
            requirement_ids=["REQ-002"],
        )
    )
    store.record(
        _episode(
            episode_id="ep-3",
            source_path="req/a.md",
            iteration=3,
            outcome="success",
            summary="Third attempt succeeded.",
            requirement_ids=["REQ-003"],
        )
    )

    reloaded = SolverEpisodeStore(str(path), max_entries=2, compact_every=1)
    recent = reloaded.recent(source_path="req/a.md", limit=5)
    prompt = reloaded.format_for_prompt(
        "third attempt succeeded",
        source_path="req/a.md",
        requirement_ids=["REQ-003"],
        limit=2,
        max_chars=220,
    )

    assert len(recent) == 2
    assert all(item.episode_id in {"ep-2", "ep-3"} for item in recent)
    assert "Relevant solver memory" in prompt
    assert "REQ-003" in prompt or "Third attempt succeeded." in prompt


def test_solver_episode_store_round_trips_bounded_metadata(tmp_path):
    path = tmp_path / "episodes.jsonl"
    store = SolverEpisodeStore(str(path), max_entries=4, compact_every=1)
    store.record(
        _episode(
            episode_id="ep-meta",
            source_path="req/replay.md",
            iteration=1,
            outcome="failure",
            summary="Replay metadata should persist.",
            requirement_ids=["REQ-001"],
            metadata={
                "prompt_budget": {
                    "used_chars": 1024,
                    "omitted_sections": ["repo_context", "research"],
                },
                "command_results": [
                    {
                        "shape": "python -m pytest",
                        "success": False,
                    }
                ],
            },
        )
    )

    reloaded = SolverEpisodeStore(str(path), max_entries=4, compact_every=1)
    episode = reloaded.snapshot(limit=1)[0]

    assert episode.metadata["prompt_budget"]["used_chars"] == 1024
    assert episode.metadata["prompt_budget"]["omitted_sections"] == ["repo_context", "research"]
    assert episode.metadata["command_results"][0]["shape"] == "python -m pytest"
