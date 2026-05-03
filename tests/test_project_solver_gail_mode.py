import pytest

from refiner import project_solver


def test_codex_preflight_disables_direct_cli_when_gail_managed(monkeypatch):
    monkeypatch.setenv("REFINER_GAIL_ENABLED", "1")

    actions_log = []
    state = project_solver._codex_preflight(
        allow_run=True,
        actions_log=actions_log,
        codingagent_primary="codex",
        codingagent_fallback=None,
    )

    assert state["requested"] is True
    assert state["cli_ready"] is False
    assert state["auth_ready"] is False
    assert state["auth_source"] == "gail_managed"
    assert "Gail-managed routing" in state["auth_message"]
    assert any("Gail-managed routing" in entry for entry in actions_log)


@pytest.mark.parametrize("agent_name", ["opencode", "codex"])
def test_query_codingagent_plan_uses_provider_path_when_gail_managed(monkeypatch, tmp_path, agent_name):
    monkeypatch.setenv("REFINER_GAIL_ENABLED", "1")
    captured = {}

    def _fake_query_codex_plan(**kwargs):
        captured["kwargs"] = kwargs
        return {"plan": [{"type": "note", "step": "via provider"}]}

    def _unexpected(**_kwargs):
        raise AssertionError("direct coding-agent path should not be used when Gail is enabled")

    monkeypatch.setattr(project_solver, "_query_codex_plan", _fake_query_codex_plan)
    monkeypatch.setattr(project_solver, "_query_opencode_plan", _unexpected)
    monkeypatch.setattr(project_solver, "_query_codex_cli_plan", _unexpected)

    actions_log = []
    payload = project_solver._query_codingagent_plan(
        agent=agent_name,
        prompt="Write the fix.",
        provider=object(),
        system_prompt="Return structured JSON.",
        llm_max_tokens=256,
        llm_temperature=0.2,
        llm_timeout=30,
        llm_reasoning_effort="medium",
        actions_log=actions_log,
        workspace=str(tmp_path),
        output_path=str(tmp_path / "plan.json"),
        allow_run=True,
        llm_provider="openai",
        llm_api_key="sk-test",
        codingagent_model="gpt-5.4",
        codingagent_reasoning_effort="high",
    )

    assert payload["plan"][0]["step"] == "via provider"
    assert captured["kwargs"]["provider"] is not None
    assert captured["kwargs"]["model_override"] == "gpt-5.4"
    assert captured["kwargs"]["llm_reasoning_effort"] == "high"
    assert any("Gail-managed routing" in entry for entry in actions_log)
