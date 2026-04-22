import json
from types import SimpleNamespace

import flask
import pytest

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
