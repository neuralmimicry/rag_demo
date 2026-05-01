from types import SimpleNamespace

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_resolve_llm_settings_prefers_nvidia_when_only_nvidia_key_is_present(monkeypatch):
    monkeypatch.setattr(
        refiner_web,
        "_get_secret_store",
        lambda user: SimpleNamespace(
            get_env=lambda: {
                "NVIDIA_API_KEY": "nvapi-test",
                "NVIDIA_BASE_URL": "https://integrate.api.nvidia.com/v1",
            }
        ),
    )
    monkeypatch.setattr(refiner_web, "_load_llm_config", lambda: {"llm_providers": []})
    monkeypatch.setattr(refiner_web, "_settings_defaults_for_user", lambda user: {})
    monkeypatch.setattr(refiner_web, "_user_settings", lambda user: {})

    settings = refiner_web._resolve_llm_settings(user="integration_tester")

    assert settings["provider"] == "nvidia"
    assert settings["api_key"] == "nvapi-test"
    assert settings["base_url"] == "https://integrate.api.nvidia.com/v1"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner web tests require a real Flask runtime")
def test_api_key_for_provider_type_supports_nvidia_aliases():
    env = {"NVIDIA_API_KEY": "nvapi-test"}

    assert refiner_web._api_key_for_provider_type("nvidia", env) == "nvapi-test"
    assert refiner_web._api_key_for_provider_type("nim", env) == "nvapi-test"
