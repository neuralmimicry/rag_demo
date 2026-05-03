import json
from types import SimpleNamespace

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


LLM_TEST_ENV_KEYS = (
    "OPENAI_API_KEY",
    "NVIDIA_API_KEY",
    "NVIDIA_BASE_URL",
    "NVIDIA_MODEL",
    "NVIDIA_DEFAULT_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_ACCESS_TOKEN",
    "GOOGLE_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "GOOGLE_ACCESS_TOKEN",
    "OLLAMA_BASE_URL",
    "OLLAMA_DEFAULT_MODEL",
    "OLLAMA_MODEL",
    "SOLVER_OLLAMA_MODEL",
)


def _clear_llm_env(monkeypatch):
    for key in LLM_TEST_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _patch_llm_state(monkeypatch, *, secret_env, role="user", config=None, defaults=None, settings=None):
    monkeypatch.setattr(
        refiner_web,
        "_get_secret_store",
        lambda user: SimpleNamespace(get_env=lambda: dict(secret_env)),
    )
    monkeypatch.setattr(refiner_web, "_load_llm_config", lambda: config or {"llm_providers": []})
    monkeypatch.setattr(refiner_web, "_settings_defaults_for_user", lambda user: defaults or {})
    monkeypatch.setattr(refiner_web, "_user_settings", lambda user: settings or {})
    monkeypatch.setattr(refiner_web, "_user_role", lambda user: role)


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_resolve_llm_settings_prefers_nvidia_when_only_nvidia_key_is_present(monkeypatch):
    _clear_llm_env(monkeypatch)
    _patch_llm_state(
        monkeypatch,
        secret_env={
            "NVIDIA_API_KEY": "nvapi-test",
            "NVIDIA_BASE_URL": "https://integrate.api.nvidia.com/v1",
        },
        settings={
            "llm": {
                "provider_access": {
                    "nvidia": {"mode": "user_key", "acknowledged": True},
                }
            }
        },
    )

    settings = refiner_web._resolve_llm_settings(user="integration_tester")

    assert settings["provider"] == "nvidia"
    assert settings["api_key"] == "nvapi-test"
    assert settings["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert settings["credential_source"] == "user_key"
    assert settings["chargeable"] is False


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_api_key_for_provider_type_supports_nvidia_aliases():
    env = {"NVIDIA_API_KEY": "nvapi-test"}

    assert refiner_web._api_key_for_provider_type("nvidia", env) == "nvapi-test"
    assert refiner_web._api_key_for_provider_type("nim", env) == "nvapi-test"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_resolve_llm_settings_ignores_shared_process_keys_for_regular_users(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shared")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.neuralmimicry.ai")
    _patch_llm_state(monkeypatch, secret_env={}, role="user")

    settings = refiner_web._resolve_llm_settings(user="alice")

    assert settings["provider"] == "ollama"
    assert settings["api_key"] is None
    assert settings["base_url"] == "http://ollama.neuralmimicry.ai"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_resolve_llm_settings_allows_shared_process_keys_for_service_accounts(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shared")
    _patch_llm_state(monkeypatch, secret_env={}, role="service_account")

    settings = refiner_web._resolve_llm_settings(user="svc-refiner")

    assert settings["provider"] == "openai"
    assert settings["api_key"] == "sk-shared"
    assert settings["credential_source"] == "service_key"
    assert settings["chargeable"] is False


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_resolve_llm_settings_allows_shared_process_keys_for_pbisaacs(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shared")
    _patch_llm_state(monkeypatch, secret_env={}, role="user")

    settings = refiner_web._resolve_llm_settings(user="pbisaacs")

    assert settings["provider"] == "openai"
    assert settings["api_key"] == "sk-shared"
    assert settings["credential_source"] == "service_key"
    assert settings["chargeable"] is False


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_resolve_llm_settings_allows_acknowledged_service_mode_for_regular_users(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shared")
    _patch_llm_state(
        monkeypatch,
        secret_env={},
        role="user",
        settings={
            "llm": {
                "provider_access": {
                    "openai": {"mode": "service", "acknowledged": True},
                }
            }
        },
    )

    settings = refiner_web._resolve_llm_settings(user="alice")

    assert settings["provider"] == "openai"
    assert settings["api_key"] == "sk-shared"
    assert settings["credential_source"] == "service_key"
    assert settings["chargeable"] is True


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_resolve_llm_settings_supports_gemini_access_tokens(monkeypatch):
    _clear_llm_env(monkeypatch)
    _patch_llm_state(
        monkeypatch,
        secret_env={"GEMINI_ACCESS_TOKEN": "ya29.user-token"},
        role="user",
        settings={
            "llm": {
                "provider_access": {
                    "gemini": {"mode": "user_key", "acknowledged": True},
                }
            }
        },
    )

    settings = refiner_web._resolve_llm_settings(user="alice")

    assert settings["provider"] == "gemini"
    assert settings["api_key"] == "ya29.user-token"
    assert settings["credential_source"] == "user_key"
    assert settings["chargeable"] is False


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_build_job_runtime_env_strips_shared_credentials_for_regular_users(monkeypatch):
    _patch_llm_state(
        monkeypatch,
        secret_env={"GEMINI_API_KEY": "gem-user"},
        role="user",
        settings={
            "llm": {
                "provider_access": {
                    "gemini": {"mode": "user_key", "acknowledged": True},
                }
            }
        },
    )

    env = refiner_web._build_job_runtime_env(
        "alice",
        process_env={
            "OPENAI_API_KEY": "sk-shared",
            "OLLAMA_BASE_URL": "http://ollama.neuralmimicry.ai",
        },
    )

    assert "OPENAI_API_KEY" not in env
    assert env["GEMINI_API_KEY"] == "gem-user"
    assert env["OLLAMA_BASE_URL"] == "http://ollama.neuralmimicry.ai"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_build_job_runtime_env_rejects_unacknowledged_job_secret_provider_keys(monkeypatch):
    _patch_llm_state(monkeypatch, secret_env={}, role="user", settings={})

    env = refiner_web._build_job_runtime_env(
        "alice",
        process_env={"OLLAMA_BASE_URL": "http://ollama.neuralmimicry.ai"},
        job_secrets={
            "OPENAI_API_KEY": "sk-job",
            "CUSTOM_SECRET": "present",
        },
    )

    assert "OPENAI_API_KEY" not in env
    assert env["CUSTOM_SECRET"] == "present"
    assert env["OLLAMA_BASE_URL"] == "http://ollama.neuralmimicry.ai"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_build_job_runtime_env_allows_acknowledged_user_key_job_secret(monkeypatch):
    _patch_llm_state(
        monkeypatch,
        secret_env={},
        role="user",
        settings={
            "llm": {
                "provider_access": {
                    "openai": {"mode": "user_key", "acknowledged": True},
                }
            }
        },
    )

    env = refiner_web._build_job_runtime_env(
        "alice",
        process_env={"OLLAMA_BASE_URL": "http://ollama.neuralmimicry.ai"},
        job_secrets={"OPENAI_API_KEY": "sk-job"},
    )

    assert env["OPENAI_API_KEY"] == "sk-job"
    billing = json.loads(env["REFINER_LLM_PROVIDER_BILLING"])
    assert billing["openai"]["credential_source"] == "user_key"
    assert billing["openai"]["chargeable"] is False


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_build_request_llm_provider_disables_configured_pool_for_regular_users(monkeypatch):
    _patch_llm_state(monkeypatch, secret_env={}, role="user")
    monkeypatch.setattr(
        refiner_web,
        "_fallback_llm_settings",
        lambda user, settings: {"provider": None, "model": None, "base_url": None, "api_key": None},
    )
    captured = {}

    def _fake_build_workflow_provider(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(refiner_web, "build_workflow_provider", _fake_build_workflow_provider)

    refiner_web._build_request_llm_provider(
        "alice",
        {"provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-user"},
        workflow="project_solver",
    )

    assert captured["include_configured"] is False
