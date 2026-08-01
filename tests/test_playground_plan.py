import json
import time
from types import SimpleNamespace

import flask
import pytest

from assistant_pipeline.service import _processing_time_estimate_ms_from_response

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


def _requirements_text(count: int) -> str:
    lines = ["Overview: Build a small classroom helper."]
    lines.append("")
    lines.append("Requirements Register:")
    for idx in range(1, count + 1):
        lines.append(f"- REQ-{idx:03d}: Requirement {idx}.")
    return "\n".join(lines)


class _FakeProvider:
    def __init__(self, requirements_count: int):
        self.requirements_count = requirements_count

    def predict(self, **kwargs):
        _ = kwargs
        return SimpleNamespace(
            text=json.dumps(
                {
                    "summary": "A quick classroom helper for pupils.",
                    "steps": [
                        "Design a bright home screen.",
                        "Add a short activity flow.",
                        "Store a simple score locally.",
                    ],
                    "requirements_text": _requirements_text(self.requirements_count),
                    "project_name": "School Helper",
                }
            ),
            raw={"processing_time_estimate_ms": 4200},
            provider="fake_provider",
            model="fake_model",
        )


def _setup_authenticated_user(monkeypatch):
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
    monkeypatch.setattr(refiner_web, "_opencode_available_for_playground", lambda: False)
    monkeypatch.setattr(refiner_web, "_estimate_calibration", lambda: {})


def test_processing_time_estimate_is_extracted_from_gail_metadata():
    assert _processing_time_estimate_ms_from_response(
        SimpleNamespace(raw={"processing_time_estimate_ms": "4200.4"})
    ) == 4200
    assert _processing_time_estimate_ms_from_response(
        SimpleNamespace(raw={"gail": {"processing_time_estimate_ms": 1800}})
    ) == 1800
    assert _processing_time_estimate_ms_from_response(
        SimpleNamespace(raw={"processing_time_estimate_ms": "not-a-number"})
    ) is None


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_playground_plan_caps_quick_build_defaults(monkeypatch):
    fake_provider = _FakeProvider(requirements_count=10)

    _setup_authenticated_user(monkeypatch)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: fake_provider)
    monkeypatch.setattr(refiner_web, "_global_requirements_count", lambda: 20)

    with refiner_web.app.test_client() as client:
        response = client.post("/api/playground/plan", json={"prompt": "Build a reading quiz."})

    assert response.status_code == 200
    data = response.get_json()
    job_payload = data["job_payload"]

    assert job_payload["project_max_steps"] == refiner_web.PLAYGROUND_PROJECT_MAX_STEPS
    assert job_payload["project_iterations"] == refiner_web.PLAYGROUND_PROJECT_MAX_ITERATIONS
    assert job_payload["llm_max_tokens"] == refiner_web.PLAYGROUND_LLM_MAX_TOKENS
    assert data["token_estimate"] == refiner_web._estimate_job_tokens(job_payload)
    assert data["token_estimate"] < 1_000_100
    assert data["processing_time_estimate_ms"] == 4200


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_playground_plan_honours_minimum_iterations(monkeypatch):
    fake_provider = _FakeProvider(requirements_count=2)

    _setup_authenticated_user(monkeypatch)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: fake_provider)
    monkeypatch.setattr(refiner_web, "_global_requirements_count", lambda: 0)

    with refiner_web.app.test_client() as client:
        response = client.post("/api/playground/plan", json={"prompt": "Build a spelling game."})

    assert response.status_code == 200
    data = response.get_json()
    assert data["job_payload"]["project_iterations"] == refiner_web.PLAYGROUND_PROJECT_MIN_ITERATIONS
    assert data["token_estimate"] == refiner_web._estimate_job_tokens(data["job_payload"])


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_playground_plan_can_run_as_an_async_subtask(monkeypatch):
    fake_provider = _FakeProvider(requirements_count=2)

    _setup_authenticated_user(monkeypatch)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: fake_provider)
    monkeypatch.setattr(refiner_web, "_global_requirements_count", lambda: 0)
    local_subtasks = refiner_web.SubtaskManager(workers=1, max_queue=4, task_ttl_sec=600)
    monkeypatch.setattr(refiner_web, "subtask_manager", local_subtasks)

    with refiner_web.app.test_client() as client:
        response = client.post(
            "/api/subtasks",
            json={
                "action": "playground_plan",
                "payload": {"prompt": "Build a spelling game."},
                "scope_type": "playground",
                "timeout_sec": 600,
            },
        )
        assert response.status_code == 202
        task_id = response.get_json()["task"]["task_id"]

        final_task = None
        for _ in range(60):
            detail = client.get(f"/api/subtasks/{task_id}?include_result=1")
            assert detail.status_code == 200
            final_task = detail.get_json()["task"]
            if final_task["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)

    assert final_task["status"] == "completed"
    assert final_task["result"]["action"] == "playground_plan"
    assert final_task["result"]["response"]["job_payload"]["project_iterations"] == refiner_web.PLAYGROUND_PROJECT_MIN_ITERATIONS
