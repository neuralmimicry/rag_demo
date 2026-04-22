import json
from types import SimpleNamespace

import flask
import pytest

from refiner.solver_memory import SolverEpisode

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


class _DraftProvider:
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=(
                "## Overview\n"
                "Build a colourful pupil tracker.\n\n"
                "## Functional Requirements\n"
                "- Track reading streaks.\n"
                "- Award badges for progress.\n"
            ),
            provider="fake_provider",
            model="fake_model",
        )


class _PlaygroundProvider:
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=json.dumps(
                {
                    "summary": "A quick classroom helper for pupils.",
                    "steps": [
                        "Design a bright home screen.",
                        "Add a short activity flow.",
                        "Store a simple score locally.",
                    ],
                    "requirements_text": (
                        "Overview: Build a reading helper.\n\n"
                        "Requirements Register:\n"
                        "- REQ-001: Show a cheerful dashboard.\n"
                        "- REQ-002: Track reading points.\n"
                        "- REQ-003: Reward weekly progress.\n"
                    ),
                    "project_name": "School Helper",
                }
            ),
            provider="fake_provider",
            model="fake_model",
        )


class _AskProvider:
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text="Prioritise testable acceptance criteria and keep scope narrow.",
            provider="fake_provider",
            model="fake_model",
        )


class _FormFillProvider:
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=json.dumps(
                [
                    {"field_id": "workflow", "value": "project_solver", "rationale": "Matches the requested automation."},
                    {"field_id": "project_name", "value": "Replay Demo", "rationale": "Keeps the title short and specific."},
                ]
            ),
            provider="fake_provider",
            model="fake_model",
        )


class _FakeCentralEpisodeStore:
    def __init__(self):
        self.entries = {}
        self.record_calls = []
        self.search_calls = []
        self.recent_calls = []

    def seed(self, owner: str, episode: SolverEpisode) -> None:
        self.entries.setdefault(owner, []).append(episode)

    def record(self, owner: str, episode: SolverEpisode) -> None:
        self.record_calls.append({"owner": owner, "episode": episode})
        self.entries.setdefault(owner, []).append(episode)

    def recent(self, owner: str, *, source_path: str | None = None, limit: int = 3):
        self.recent_calls.append({"owner": owner, "source_path": source_path, "limit": limit})
        rows = list(self.entries.get(owner, []))
        if source_path:
            rows = [row for row in rows if row.source_path == source_path]
        rows.sort(key=lambda row: row.created_at, reverse=True)
        return rows[:limit]

    def search(self, owner: str, query_text: str, *, source_path: str | None = None, limit: int = 3, **kwargs):
        self.search_calls.append(
            {
                "owner": owner,
                "query_text": query_text,
                "source_path": source_path,
                "limit": limit,
                **kwargs,
            }
        )
        query_tokens = {token for token in str(query_text or "").lower().split() if token}
        rows = list(self.entries.get(owner, []))
        if source_path:
            rows = [row for row in rows if row.source_path == source_path]
        scored = []
        for row in rows:
            haystack = row.search_blob().lower()
            score = sum(1 for token in query_tokens if token in haystack)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [row for _, row in scored[:limit]]


def _setup_authenticated_user(monkeypatch, tmp_path):
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "integration_tester")
    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)
    monkeypatch.setattr(
        refiner_web,
        "_resolve_llm_settings",
        lambda **kwargs: {
            "provider": "openai",
            "model": "gpt-5.1",
            "base_url": "",
            "api_key": "test-key",
        },
    )
    monkeypatch.setattr(refiner_web, "_guardrail_scan", lambda prompt: None)
    monkeypatch.setattr(refiner_web, "ASSISTANT_MEMORY_ROOT", str(tmp_path / "assistant_memory"))
    monkeypatch.setattr(refiner_web, "_opencode_available_for_playground", lambda: False)
    monkeypatch.setattr(refiner_web, "_estimate_calibration", lambda: {})


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_requirements_draft_uses_and_records_memory(monkeypatch, tmp_path):
    provider = _DraftProvider()
    _setup_authenticated_user(monkeypatch, tmp_path)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: provider)

    scope = refiner_web._assistant_memory_scope(
        "assistant_requirements",
        mode="draft",
        profile="requirements",
    )
    store = refiner_web._assistant_memory_store("integration_tester")
    store.record(
        SolverEpisode(
            episode_id="ep-1",
            source_path=scope,
            iteration=1,
            created_at="2026-04-14T01:00:00Z",
            outcome="success",
            summary="Drafted a reading tracker with badges and weekly rewards.",
            requirement_ids=["REQ-001", "REQ-002"],
            notes=["prompt: reading tracker", "context: colourful dashboard"],
            metadata={"project_name": "Reading Hero"},
        )
    )

    with refiner_web.app.test_client() as client:
        response = client.post(
            "/api/assistant/requirements",
            json={
                "mode": "draft",
                "requirements_text": "Build a colourful reading tracker for pupils.",
                "messages": [],
            },
        )

    assert response.status_code == 200
    data = response.get_json()
    assert "Requirements Register" in data["reply"]
    assert provider.calls
    prompt_text = provider.calls[0]["messages"][-1]["content"]
    assert "Relevant patterns from this user's earlier successful drafts" in prompt_text
    assert "Drafted a reading tracker with badges" in prompt_text

    episodes = refiner_web._assistant_memory_store("integration_tester").snapshot(source_path=scope)
    assert len(episodes) == 2
    assert episodes[-1].requirement_ids
    assert any("assistant_profile: requirements" in note for note in episodes[-1].notes)


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_playground_plan_uses_and_records_memory(monkeypatch, tmp_path):
    provider = _PlaygroundProvider()
    _setup_authenticated_user(monkeypatch, tmp_path)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: provider)

    scope = refiner_web._assistant_memory_scope("playground_plan")
    store = refiner_web._assistant_memory_store("integration_tester")
    store.record(
        SolverEpisode(
            episode_id="ep-1",
            source_path=scope,
            iteration=1,
            created_at="2026-04-14T01:00:00Z",
            outcome="success",
            summary="Built a reading helper with a bright reward dashboard.",
            requirement_ids=["REQ-001"],
            notes=["prompt: reading helper", "context: rewards and points"],
            metadata={
                "project_name": "Reading Hero",
                "steps": ["Add rewards", "Track points"],
            },
        )
    )

    with refiner_web.app.test_client() as client:
        response = client.post("/api/playground/plan", json={"prompt": "Build a reading quiz."})

    assert response.status_code == 200
    request_payload = json.loads(provider.calls[0]["messages"][0]["content"])
    assert "reference_patterns" in request_payload
    assert request_payload["reference_patterns"][0]["project_name"] == "Reading Hero"

    episodes = refiner_web._assistant_memory_store("integration_tester").snapshot(source_path=scope)
    assert len(episodes) == 2
    assert episodes[-1].metadata["project_name"] == "School Helper"
    assert episodes[-1].metadata["steps"] == [
        "Design a bright home screen.",
        "Add a short activity flow.",
        "Store a simple score locally.",
    ]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_requirements_ask_uses_and_records_memory(monkeypatch, tmp_path):
    provider = _AskProvider()
    _setup_authenticated_user(monkeypatch, tmp_path)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: provider)

    scope = refiner_web._assistant_memory_scope(
        "assistant_requirements",
        mode="ask",
        profile="requirements",
    )
    store = refiner_web._assistant_memory_store("integration_tester")
    store.record(
        SolverEpisode(
            episode_id="ep-1",
            source_path=scope,
            iteration=1,
            created_at="2026-04-14T01:00:00Z",
            outcome="success",
            summary="Use acceptance criteria before implementation detail when clarifying scope.",
            notes=[
                "prompt: clarify scope",
                "context: requirements should remain testable and narrow",
            ],
            metadata={"mode": "ask"},
        )
    )

    with refiner_web.app.test_client() as client:
        response = client.post(
            "/api/assistant/requirements",
            json={
                "mode": "ask",
                "prompt": "How should I tighten this requirements draft so the build stays small but still testable?",
                "requirements_text": (
                    "We need a requirements draft for a small classroom helper. "
                    "The product should stay narrow in scope, remain testable, and avoid feature creep."
                ),
                "messages": [
                    {"role": "user", "content": "Keep the project small."},
                    {"role": "assistant", "content": "Focus on a single user journey."},
                    {"role": "user", "content": "How do I make the acceptance criteria clearer?"},
                ],
            },
        )

    assert response.status_code == 200
    assert provider.calls
    prompt_text = provider.calls[0]["messages"][-1]["content"]
    assert "Relevant prior successful guidance for similar requirements questions" in prompt_text
    assert "Use acceptance criteria before implementation detail" in prompt_text

    episodes = refiner_web._assistant_memory_store("integration_tester").snapshot(source_path=scope)
    assert len(episodes) == 2
    assert episodes[-1].metadata["mode"] == "ask"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_requirements_ask_reads_central_memory_and_dual_writes(monkeypatch, tmp_path):
    provider = _AskProvider()
    central_store = _FakeCentralEpisodeStore()

    _setup_authenticated_user(monkeypatch, tmp_path)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(refiner_web, "CENTRAL_STORE", SimpleNamespace(assistant_episodes=central_store))

    scope = refiner_web._assistant_memory_scope(
        "assistant_requirements",
        mode="ask",
        profile="requirements",
    )
    central_store.seed(
        "integration_tester",
        SolverEpisode(
            episode_id="central-1",
            source_path=scope,
            iteration=4,
            created_at="2026-04-14T01:00:00Z",
            outcome="success",
            summary="Use acceptance criteria before implementation detail when tightening scope.",
            notes=[
                "prompt: clarify scope",
                "context: requirements should remain testable and narrow",
            ],
            metadata={"mode": "ask"},
        ),
    )

    with refiner_web.app.test_client() as client:
        response = client.post(
            "/api/assistant/requirements",
            json={
                "mode": "ask",
                "prompt": "How should I tighten this requirements draft so the build stays small but still testable?",
                "requirements_text": (
                    "We need a requirements draft for a small classroom helper. "
                    "The product should stay narrow in scope, remain testable, and avoid feature creep."
                ),
                "messages": [
                    {"role": "user", "content": "Keep the project small."},
                    {"role": "assistant", "content": "Focus on a single user journey."},
                    {"role": "user", "content": "How do I make the acceptance criteria clearer?"},
                ],
            },
        )

    assert response.status_code == 200
    assert provider.calls
    prompt_text = provider.calls[0]["messages"][-1]["content"]
    assert "Relevant prior successful guidance for similar requirements questions" in prompt_text
    assert "Use acceptance criteria before implementation detail" in prompt_text
    assert central_store.search_calls
    assert central_store.record_calls
    assert central_store.record_calls[-1]["owner"] == "integration_tester"
    assert central_store.record_calls[-1]["episode"].metadata["mode"] == "ask"

    local_episodes = refiner_web._assistant_memory_store("integration_tester").snapshot(source_path=scope)
    assert len(local_episodes) == 1
    assert local_episodes[0].metadata["mode"] == "ask"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_form_fill_uses_and_records_memory(monkeypatch, tmp_path):
    provider = _FormFillProvider()
    _setup_authenticated_user(monkeypatch, tmp_path)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: provider)

    scope = refiner_web._assistant_memory_scope("assistant_form_fill")
    store = refiner_web._assistant_memory_store("integration_tester")
    store.record(
        SolverEpisode(
            episode_id="ep-1",
            source_path=scope,
            iteration=1,
            created_at="2026-04-14T01:00:00Z",
            outcome="success",
            summary="Suggested a compact project_solver job form for a replay demo.",
            notes=[
                "workflow: project_solver",
                "scope: workflow",
                "fields: workflow, project_name",
            ],
            metadata={
                "workflow": "project_solver",
                "scope": "workflow",
                "field_ids": ["workflow", "project_name"],
                "suggestions": [
                    {"field_id": "workflow", "value": "project_solver", "rationale": "Needed for code execution."},
                ],
            },
        )
    )

    with refiner_web.app.test_client() as client:
        response = client.post(
            "/api/assistant/form-fill",
            json={
                "prompt": "Set up a small replay diagnostics run.",
                "workflow": "project_solver",
                "scope": "workflow",
                "fields": [
                    {"id": "workflow", "label": "Workflow", "type": "select", "options": ["project_solver", "delivery_pipeline"]},
                    {"id": "project_name", "label": "Project Name", "type": "text", "description": "Short display name."},
                ],
            },
        )

    assert response.status_code == 200
    request_payload = json.loads(provider.calls[0]["messages"][0]["content"])
    assert "reference_suggestions" in request_payload
    assert request_payload["reference_suggestions"][0]["workflow"] == "project_solver"
    assert request_payload["reference_suggestions"][0]["suggestions"][0]["field_id"] == "workflow"

    episodes = refiner_web._assistant_memory_store("integration_tester").snapshot(source_path=scope)
    assert len(episodes) == 2
    assert episodes[-1].metadata["workflow"] == "project_solver"
    assert episodes[-1].metadata["suggestions"][0]["field_id"] == "workflow"
