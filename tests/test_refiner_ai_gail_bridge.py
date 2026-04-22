from refiner.llm_providers import get_provider
from refiner.refiner_ai_orchestration import (
    build_workflow_provider,
    orchestrate_provider_candidates,
    orchestration_status,
)


def _enable_gail(monkeypatch):
    monkeypatch.setenv("REFINER_GAIL_ENABLED", "1")
    monkeypatch.setenv("REFINER_GAIL_BASE_URL", "https://gail.internal.example")
    monkeypatch.setenv("REFINER_GAIL_API_TOKEN", "bridge-secret")


def test_get_provider_returns_gail_direct_provider_when_enabled(monkeypatch):
    _enable_gail(monkeypatch)

    provider = get_provider(
        "openai",
        model="gpt-4o-mini",
        api_key="sk-test",
    )

    assert provider is not None
    assert provider.__class__.__name__ == "GailProvider"
    assert provider.gail_mode == "direct"
    assert provider.gail_source_provider == "openai"
    assert provider.gail_source_model == "gpt-4o-mini"
    assert provider.gail_source_api_key == "sk-test"
    assert provider.gail_source_access_token is None
    assert provider.gail_base_url == "https://gail.internal.example"


def test_build_workflow_provider_uses_gail_and_normalizes_provider_credentials(monkeypatch):
    _enable_gail(monkeypatch)

    provider = build_workflow_provider(
        workflow="assistant_requirements",
        role="assistant",
        preferred_provider="gemini",
        preferred_model="gemini-2.5-flash",
        preferred_api_key="ya29.oauth-token",
        fallback_provider="openai",
        fallback_model="gpt-4o-mini",
        fallback_api_key="sk-fallback",
        include_configured=False,
        selection_mode="best",
        max_candidates=3,
    )

    assert provider is not None
    assert provider.__class__.__name__ == "GailProvider"
    assert provider.gail_mode == "workflow"
    assert provider.workflow == "assistant_requirements"
    assert provider.role == "assistant"
    assert provider.preferred_provider == "gemini"
    assert provider.preferred_model == "gemini-2.5-flash"
    assert provider.preferred_api_key is None
    assert provider.preferred_access_token == "ya29.oauth-token"
    assert provider.fallback_provider == "openai"
    assert provider.fallback_model == "gpt-4o-mini"
    assert provider.fallback_api_key == "sk-fallback"
    assert provider.selection_mode == "best"
    assert provider.max_candidates == 3


def test_orchestrate_provider_candidates_returns_gail_workflow_provider(monkeypatch):
    _enable_gail(monkeypatch)
    primary = get_provider("openai", model="gpt-4o-mini", api_key="sk-primary")
    fallback = get_provider(
        "gemini",
        model="gemini-2.5-flash",
        access_token="ya29.direct-token",
    )

    provider = orchestrate_provider_candidates(
        [primary, fallback],
        workflow="topic_research",
        role="researcher",
        include_configured=False,
        selection_mode="fastest",
        max_candidates=2,
    )

    assert provider is not None
    assert provider.__class__.__name__ == "GailProvider"
    assert provider.gail_mode == "workflow"
    assert provider.workflow == "topic_research"
    assert provider.role == "researcher"
    assert provider.preferred_provider == "openai"
    assert provider.preferred_model == "gpt-4o-mini"
    assert provider.preferred_api_key == "sk-primary"
    assert provider.fallback_provider == "gemini"
    assert provider.fallback_model == "gemini-2.5-flash"
    assert provider.fallback_access_token == "ya29.direct-token"
    assert provider.selection_mode == "fastest"
    assert provider.max_candidates == 2


def test_orchestration_status_proxies_to_gail(monkeypatch):
    _enable_gail(monkeypatch)
    from refiner import refiner_ai_gail
    captured = {}

    def _fake_gail_status(*, candidate_limit, probe_engines, probe_providers):
        captured["candidate_limit"] = candidate_limit
        captured["probe_engines"] = probe_engines
        captured["probe_providers"] = probe_providers
        return {
            "enabled": True,
            "provider_count": 1,
            "providers": [{"name": "OpenAIPrimary"}],
        }

    monkeypatch.setattr(refiner_ai_gail, "gail_status", _fake_gail_status)

    payload = orchestration_status(
        config_path="config.json",
        include_metrics=False,
        probe_engines=True,
        candidate_limit=7,
    )

    assert payload["enabled"] is True
    assert payload["provider_count"] == 1
    assert payload["providers"][0]["name"] == "OpenAIPrimary"
    assert captured == {
        "candidate_limit": 7,
        "probe_engines": True,
        "probe_providers": False,
    }
