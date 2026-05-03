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


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


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


def test_get_provider_returns_gail_nvidia_provider_when_enabled(monkeypatch):
    _enable_gail(monkeypatch)

    provider = get_provider(
        "nim",
        model="moonshotai/kimi-k2-instruct-0905",
        api_key="nvapi-test",
        base_url="https://integrate.api.nvidia.com/v1",
    )

    assert provider is not None
    assert provider.__class__.__name__ == "GailProvider"
    assert provider.gail_mode == "direct"
    assert provider.gail_source_provider == "nvidia"
    assert provider.gail_source_model == "moonshotai/kimi-k2-instruct-0905"
    assert provider.gail_source_api_key == "nvapi-test"
    assert provider.gail_source_base_url == "https://integrate.api.nvidia.com/v1"


def test_gail_direct_provider_routes_via_orchestration_by_default(monkeypatch):
    _enable_gail(monkeypatch)
    captured = {}
    from refiner import refiner_ai_gail

    def _fake_post(url, *, headers, json_payload, timeout, max_retries):
        captured["url"] = url
        captured["headers"] = headers
        captured["json_payload"] = json_payload
        captured["timeout"] = timeout
        captured["max_retries"] = max_retries
        return _FakeResponse(
            {
                "text": "ok",
                "provider": "ollama",
                "model": "llama3.2",
                "latency_ms": 12,
            }
        )

    monkeypatch.setattr(refiner_ai_gail, "_http_post", _fake_post)

    provider = get_provider(
        "openai",
        model="gpt-4o-mini",
        api_key="sk-test",
    )
    response = provider.predict(
        [{"role": "user", "content": "Plan the fix."}],
        system="You are helpful.",
        max_tokens=256,
        timeout=30,
    )

    assert response.provider == "ollama"
    assert response.model == "llama3.2"
    assert captured["url"] == "https://gail.internal.example/v1/llm/complete"
    assert captured["headers"]["Authorization"] == "Bearer bridge-secret"
    assert captured["json_payload"]["workflow"] == "direct"
    assert captured["json_payload"]["role"] == "assistant"
    assert captured["json_payload"]["preferred_provider"] == "openai"
    assert captured["json_payload"]["preferred_model"] == "gpt-4o-mini"
    assert captured["json_payload"]["preferred_api_key"] == "sk-test"
    assert captured["json_payload"]["include_configured"] is True


def test_gail_direct_provider_can_use_direct_complete_when_routing_disabled(monkeypatch):
    _enable_gail(monkeypatch)
    monkeypatch.setenv("REFINER_GAIL_ROUTE_DIRECT_REQUESTS", "0")
    captured = {}
    from refiner import refiner_ai_gail

    def _fake_post(url, *, headers, json_payload, timeout, max_retries):
        captured["url"] = url
        captured["json_payload"] = json_payload
        return _FakeResponse(
            {
                "text": "ok",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "latency_ms": 7,
            }
        )

    monkeypatch.setattr(refiner_ai_gail, "_http_post", _fake_post)

    provider = get_provider(
        "openai",
        model="gpt-4o-mini",
        api_key="sk-test",
    )
    response = provider.predict([{"role": "user", "content": "Plan the fix."}])

    assert response.provider == "openai"
    assert response.model == "gpt-4o-mini"
    assert captured["url"] == "https://gail.internal.example/v1/llm/direct-complete"
    assert captured["json_payload"]["provider"] == "openai"
    assert captured["json_payload"]["model"] == "gpt-4o-mini"
    assert captured["json_payload"]["api_key"] == "sk-test"


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
