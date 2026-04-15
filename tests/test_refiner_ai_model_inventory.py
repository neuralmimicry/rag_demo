import json
import time
from unittest.mock import MagicMock, patch

import pytest

from llm_providers import LLMError, OllamaProvider
from refiner_ai_model_inventory import (
    AIModelInventoryMonitor,
    build_model_inventory_snapshot,
    resolve_ollama_model_for_request,
)


def _config_with_ollama(tmp_path, extra_ai=None):
    config_path = tmp_path / "config.json"
    payload = {
        "ai_orchestration": {
            "enabled": True,
            "providers": [
                {
                    "name": "LocalCoder",
                    "provider": "ollama",
                    "model": "qwen2.5-coder:7b",
                    "roles": ["planner"],
                    "specialties": ["code", "planning"],
                }
            ],
        }
    }
    if isinstance(extra_ai, dict):
        payload["ai_orchestration"].update(extra_ai)
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_model_inventory_shortlists_relevant_models_that_fit(tmp_path, monkeypatch):
    config_path = _config_with_ollama(
        tmp_path,
        extra_ai={
            "model_catalog": [
                {"model": "llava:7b", "capabilities": ["vision"], "modality": "vision"}
            ]
        },
    )
    monkeypatch.setattr(
        "refiner_ai_model_inventory.host_resource_snapshot",
        lambda _path=None: {
            "memory": {
                "total_bytes": 32 * 1024 ** 3,
                "available_bytes": 24 * 1024 ** 3,
                "budget_bytes": 20 * 1024 ** 3,
            },
            "disk": {
                "path": str(tmp_path),
                "total_bytes": 200 * 1024 ** 3,
                "free_bytes": 120 * 1024 ** 3,
                "budget_bytes": 100 * 1024 ** 3,
            },
            "gpu": {"available": False, "total_bytes": None, "free_bytes": None, "device_count": 0, "probed": False},
        },
    )
    monkeypatch.setattr(
        "refiner_ai_model_inventory.fetch_ollama_tags",
        lambda _base_url, timeout=None: {
            "ok": True,
            "status_code": 200,
            "latency_ms": 12,
            "message": "ok",
            "models": [
                {
                    "model": "llama3.2:latest",
                    "installed": True,
                    "aliases": ["llama3.2", "llama3.2:latest"],
                    "size_bytes": 2 * 1024 ** 3,
                    "parameter_billions": 3.0,
                    "capabilities": ["chat", "reasoning"],
                }
            ],
        },
    )

    snapshot = build_model_inventory_snapshot(str(config_path), base_url="http://ollama.local:11434")
    models = {entry["model"]: entry for entry in snapshot["models"]}

    assert snapshot["provider"]["reachable"] is True
    assert snapshot["counts"]["ready_models"] >= 1
    assert models["qwen2.5-coder:7b"]["download_recommended"] is True
    assert models["qwen2.5-coder:7b"]["fit_status"] == "download_candidate"
    assert models["llava:7b"]["fit_status"] == "not_relevant"
    assert "code" in snapshot["required_capabilities"]


def test_model_inventory_blocks_models_that_do_not_fit_resources(tmp_path, monkeypatch):
    config_path = _config_with_ollama(tmp_path)
    monkeypatch.setattr(
        "refiner_ai_model_inventory.host_resource_snapshot",
        lambda _path=None: {
            "memory": {
                "total_bytes": 8 * 1024 ** 3,
                "available_bytes": 4 * 1024 ** 3,
                "budget_bytes": 3 * 1024 ** 3,
            },
            "disk": {
                "path": str(tmp_path),
                "total_bytes": 100 * 1024 ** 3,
                "free_bytes": 60 * 1024 ** 3,
                "budget_bytes": 50 * 1024 ** 3,
            },
            "gpu": {"available": False, "total_bytes": None, "free_bytes": None, "device_count": 0, "probed": False},
        },
    )
    monkeypatch.setattr(
        "refiner_ai_model_inventory.fetch_ollama_tags",
        lambda _base_url, timeout=None: {
            "ok": True,
            "status_code": 200,
            "latency_ms": 8,
            "message": "ok",
            "models": [],
        },
    )

    snapshot = build_model_inventory_snapshot(str(config_path), base_url="http://ollama.local:11434")
    model = next(entry for entry in snapshot["models"] if entry["model"] == "qwen2.5-coder:7b")

    assert model["fits_memory"] is False
    assert model["download_recommended"] is False
    assert model["fit_status"] == "blocked_memory"


def test_resolve_ollama_model_for_request_prefers_installed_relevant_fallback(monkeypatch):
    snapshot = {
        "generated_ts": time.time(),
        "poll_sec": 1800,
        "provider": {"base_url": "http://ollama.local:11434"},
        "models": [
            {
                "model": "qwen2.5-coder:7b",
                "installed": False,
                "fit_status": "download_candidate",
                "matched_capabilities": ["code", "reasoning"],
                "download_recommended": True,
                "runtime_ready": False,
                "relevance_score": 8.0,
            },
            {
                "model": "codellama:7b",
                "installed": True,
                "fit_status": "ready",
                "matched_capabilities": ["code", "reasoning"],
                "download_recommended": False,
                "runtime_ready": True,
                "relevance_score": 9.5,
            },
            {
                "model": "llama3.2:latest",
                "installed": True,
                "fit_status": "ready",
                "matched_capabilities": ["chat"],
                "download_recommended": False,
                "runtime_ready": True,
                "relevance_score": 5.0,
            },
        ],
    }
    monkeypatch.setattr("refiner_ai_model_inventory.load_model_inventory_snapshot", lambda _config=None: snapshot)
    monkeypatch.setattr("refiner_ai_model_inventory._snapshot_stale", lambda _snapshot, _poll: False)

    resolution = resolve_ollama_model_for_request(
        "qwen2.5-coder:7b",
        prompt_text="Refactor this Python API and add pytest coverage.",
        base_url="http://ollama.local:11434",
    )

    assert resolution["selected_model"] == "codellama:7b"
    assert resolution["reason"] == "requested_model_missing"


def test_model_inventory_monitor_run_once_records_summary(tmp_path, monkeypatch):
    config_path = _config_with_ollama(tmp_path)
    monitor = AIModelInventoryMonitor(config_path=str(config_path), poll_sec=60)
    monkeypatch.setattr(
        "refiner_ai_model_inventory.refresh_model_inventory_cache",
        lambda _config=None, base_url=None: {
            "provider": {"reachable": True, "base_url": base_url or "http://ollama.local:11434"},
            "counts": {
                "ready_models": 2,
                "download_candidates": 1,
                "blocked_memory": 0,
                "blocked_disk": 0,
            },
        },
    )

    summary = monitor.run_once()
    status = monitor.status()

    assert summary["reachable"] is True
    assert status["last_summary"]["ready_models"] == 2
    assert status["last_error"] is None


@patch("llm_providers._http_post")
@patch("llm_providers.resolve_ollama_model_for_request")
def test_ollama_provider_uses_safe_local_fallback_before_generate(mock_resolve, mock_post):
    mock_resolve.return_value = {
        "selected_model": "codellama:7b",
        "requested_model": "qwen2.5-coder:7b",
        "reason": "requested_model_missing",
        "recommended_downloads": [],
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "ok"}
    mock_post.return_value = mock_response

    provider = OllamaProvider(model="qwen2.5-coder:7b", base_url="http://ollama.local:11434")
    response = provider.predict(
        [{"role": "user", "content": "Refactor this API."}],
        system="Return concise code notes.",
    )

    payload = mock_post.call_args.kwargs["json_payload"]
    assert payload["model"] == "codellama:7b"
    assert provider.model == "codellama:7b"
    assert response.model == "codellama:7b"


@patch("llm_providers._http_post")
@patch("llm_providers.resolve_ollama_model_for_request")
def test_ollama_provider_refuses_missing_model_without_safe_local_match(mock_resolve, mock_post):
    mock_resolve.return_value = {
        "selected_model": None,
        "requested_model": "qwen2.5-coder:7b",
        "reason": "no_safe_local_model",
        "recommended_downloads": ["qwen2.5-coder:7b"],
    }

    provider = OllamaProvider(model="qwen2.5-coder:7b", base_url="http://ollama.local:11434")
    with pytest.raises(LLMError) as exc:
        provider.predict(
            [{"role": "user", "content": "Refactor this API."}],
            system="Return concise code notes.",
        )

    assert "Recommended downloads: qwen2.5-coder:7b." in str(exc.value)
    mock_post.assert_not_called()
