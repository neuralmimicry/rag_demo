import json
from types import SimpleNamespace

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


class _DummyUserStore:
    def __init__(self):
        self.role = "admin"
        self.email = "alice@example.com"
        self.metadata = {}

    def count_users(self):
        return 1

    def has_users(self):
        return True

    def get_role(self, _username):
        return self.role

    def get_email(self, _username):
        return self.email

    def set_email(self, _username, value):
        self.email = value
        return True

    def get_metadata(self, _username):
        return dict(self.metadata)

    def set_metadata(self, _username, metadata):
        self.metadata = dict(metadata or {})
        return True

    def ensure_user(self, _username, **_kwargs):
        return None


class _DummyLLMTelemetryStore:
    def __init__(self, *, summary_response=None, prune_result=0):
        self.records = []
        self.summary_calls = []
        self.prune_calls = []
        self.summary_response = summary_response or {
            "enabled": True,
            "groups": [],
            "subjects": [],
            "totals": {"requests": len(self.records)},
        }
        self.prune_result = prune_result

    def record(self, scope, subject, event):
        self.records.append(
            {
                "scope": scope,
                "subject": subject,
                "event": dict(event or {}),
            }
        )

    def summary(self, **kwargs):
        self.summary_calls.append(dict(kwargs))
        payload = self.summary_response
        if callable(payload):
            payload = payload(kwargs)
        return json.loads(json.dumps(payload))

    def prune_older_than(self, hours):
        self.prune_calls.append(hours)
        return self.prune_result


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_api_profile_settings_roundtrip_preserves_email(monkeypatch):
    store = _DummyUserStore()
    monkeypatch.setattr(refiner_web, "user_store", store)
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "alice")
    monkeypatch.setattr(refiner_web, "_record_identity_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(refiner_web.access_store, "tree_for_user", lambda _user: [])
    monkeypatch.setattr(refiner_web.access_store, "tree_all", lambda: [])

    with refiner_web.app.test_client() as client:
        response = client.get("/api/profile")
        assert response.status_code == 200
        data = response.get_json()
        assert data["email"] == "alice@example.com"
        assert data["settings"]["assistant"]["use_memory"] is True

        response = client.post(
            "/api/profile",
            json={
                "settings": {
                    "assistant": {"use_memory": False},
                    "solver": {"command_policy_mode": "strict"},
                }
            },
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["email"] == "alice@example.com"
        assert data["settings"]["assistant"]["use_memory"] is False
        assert data["settings"]["solver"]["command_policy_mode"] == "strict"
        assert store.get_email("alice") == "alice@example.com"

        response = client.post(
            "/api/profile",
            json={"settings": {"solver": {"command_policy_mode": "invalid"}}},
        )
        assert response.status_code == 400
        assert response.get_json()["error"] == "invalid_settings"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_manager_merges_structured_token_usage_events(monkeypatch, tmp_path):
    job = refiner_web.Job(
        job_id="job-usage-1",
        payload={"workflow": "project_solver"},
        project_name="Usage Demo",
        owner="alice",
        meta_path=str(tmp_path / "job.json"),
    )
    monkeypatch.setattr(job, "persist", lambda *args, **kwargs: None)

    first = {
        "type": "token_usage",
        "provider": "openai",
        "model": "gpt-5.4",
        "category": "llm",
        "usage": {"prompt": 12, "completion": 8, "total": 20, "cached": 4},
        "cost": {"amount": 0.12, "currency": "USD"},
        "at": "2026-04-14T12:00:00Z",
    }
    second = {
        "type": "token_usage",
        "provider": "openai",
        "model": "gpt-5.4",
        "category": "codingagent",
        "usage": {"prompt": 5, "completion": 10, "total": 15},
        "cost": {"amount": 0.08, "currency": "USD"},
        "at": "2026-04-14T12:00:05Z",
    }

    assert refiner_web.manager._handle_event_line(job, "__RAG_EVENT__ " + json.dumps(first)) is True
    assert refiner_web.manager._handle_event_line(job, "__RAG_EVENT__ " + json.dumps(second)) is True

    usage = job.metrics["token_usage"]
    assert usage["total"] == 35
    assert usage["prompt"] == 17
    assert usage["completion"] == 18
    assert usage["cached"] == 4
    assert usage["events"] == 2
    assert usage["cost"]["amount"] == 0.2
    assert usage["by_category"]["llm"]["total"] == 20
    assert usage["by_category"]["codingagent"]["total"] == 15
    assert usage["by_model"]["openai/gpt-5.4"]["events"] == 2
    assert job.token_actual == 35


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_manager_persists_llm_request_events(monkeypatch, tmp_path):
    telemetry = _DummyLLMTelemetryStore()
    monkeypatch.setattr(refiner_web, "CENTRAL_STORE", SimpleNamespace(llm_request_telemetry=telemetry))
    job = refiner_web.Job(
        job_id="job-usage-2",
        payload={"workflow": "project_solver"},
        project_name="Usage Demo",
        owner="alice",
        meta_path=str(tmp_path / "job.json"),
    )

    event = {
        "type": "llm_request",
        "provider": "openai",
        "model": "gpt-5.4",
        "category": "codingagent",
        "outcome": "success",
        "latency_ms": 4321,
        "input_chars": 2048,
        "estimated_input_tokens": 512,
        "at": "2026-04-14T12:05:00Z",
    }

    assert refiner_web.manager._handle_event_line(job, "__RAG_EVENT__ " + json.dumps(event)) is True
    assert telemetry.records == [
        {
            "scope": "user",
            "subject": "alice",
            "event": event,
        }
    ]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_manager_persists_llm_request_events_for_team_scope(monkeypatch, tmp_path):
    telemetry = _DummyLLMTelemetryStore()
    monkeypatch.setattr(refiner_web, "CENTRAL_STORE", SimpleNamespace(llm_request_telemetry=telemetry))
    job = refiner_web.Job(
        job_id="job-usage-team",
        payload={"workflow": "project_solver", "team_id": "team-7"},
        project_name="Usage Demo",
        owner="alice",
        meta_path=str(tmp_path / "job.json"),
    )

    event = {
        "type": "llm_request",
        "provider": "openai",
        "model": "gpt-5.4",
        "category": "codingagent",
        "outcome": "success",
        "latency_ms": 321,
        "at": "2026-04-14T12:06:00Z",
    }

    assert refiner_web.manager._handle_event_line(job, "__RAG_EVENT__ " + json.dumps(event)) is True
    assert telemetry.records == [
        {
            "scope": "user",
            "subject": "alice",
            "event": event,
        },
        {
            "scope": "team",
            "subject": "team-7",
            "event": event,
        },
    ]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_inprocess_llm_request_callback_uses_current_request_user(monkeypatch):
    telemetry = _DummyLLMTelemetryStore()
    monkeypatch.setattr(refiner_web, "CENTRAL_STORE", SimpleNamespace(llm_request_telemetry=telemetry))
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "alice")

    with refiner_web.app.test_request_context("/api/assistant/requirements"):
        refiner_web._handle_inprocess_llm_provider_event(
            {
                "type": "llm_request",
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "category": "llm",
                "outcome": "quota_error",
                "latency_ms": 987,
            }
        )

    assert telemetry.records[0]["scope"] == "user"
    assert telemetry.records[0]["subject"] == "alice"
    assert telemetry.records[0]["event"]["provider"] == "gemini"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_admin_llm_telemetry_endpoint_passes_filters(monkeypatch):
    telemetry = _DummyLLMTelemetryStore(
        summary_response={
            "enabled": True,
            "scope": "team",
            "subject": "team-1",
            "provider": "openai",
            "model": "gpt-5.4",
            "category": "codingagent",
            "window_hours": 24,
            "totals": {"requests": 7, "success_rate": 0.8571, "avg_latency_ms": 321},
            "groups": [{"provider": "openai", "model": "gpt-5.4", "category": "codingagent", "requests": 7}],
            "subjects": [{"scope": "team", "subject": "team-1", "requests": 7}],
        }
    )
    monkeypatch.setattr(refiner_web, "CENTRAL_STORE", SimpleNamespace(llm_request_telemetry=telemetry))
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "alice")
    monkeypatch.setattr(refiner_web, "_is_admin_user", lambda _user: True)
    monkeypatch.setattr(
        refiner_web,
        "llm_telemetry_janitor",
        SimpleNamespace(
            status=lambda: {
                "enabled": True,
                "available": True,
                "running": True,
                "retention_hours": 72,
                "poll_sec": 3600,
                "last_run_at": "2026-04-14T13:00:00Z",
                "last_removed": 4,
                "last_error": None,
                "aggregate_store": "postgres",
                "raw_event_stream": "job_events_jsonl",
            }
        ),
    )
    monkeypatch.setattr(refiner_web, "user_store", _DummyUserStore())
    monkeypatch.setattr(refiner_web.manager, "list_jobs", lambda: [])
    monkeypatch.setattr(refiner_web, "_active_users_snapshot", lambda: [])
    monkeypatch.setattr(
        refiner_web,
        "orchestration_status",
        lambda **_kwargs: {
            "enabled": True,
            "provider_count": 2,
            "engine_count": 1,
            "metrics": {"candidate_count": 3},
            "engines": [{"type": "aarnn", "available": True}],
        },
    )

    with refiner_web.app.test_client() as client:
        response = client.get(
            "/api/admin/llm-telemetry"
            "?scope=team&subject=team-1&provider=openai&model=gpt-5.4"
            "&category=codingagent&hours=24&limit=5&include_subjects=1&subject_limit=9"
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["enabled"] is True
        assert data["retention"]["aggregate_store"] == "postgres"
        assert telemetry.summary_calls[0] == {
            "scope": "team",
            "subject": "team-1",
            "hours": 24,
            "limit": 5,
            "provider": "openai",
            "model": "gpt-5.4",
            "category": "codingagent",
            "include_subjects": True,
            "subject_limit": 9,
        }

        stats_response = client.get("/api/admin/stats")
        assert stats_response.status_code == 200
        stats = stats_response.get_json()
        assert stats["llm_request_telemetry"]["retention"]["retention_hours"] == 72
        assert stats["ai_orchestration"]["provider_count"] == 2
        assert stats["ai_orchestration"]["engines"][0]["type"] == "aarnn"
        assert telemetry.summary_calls[1]["hours"] == 72
        assert telemetry.summary_calls[1]["limit"] == 12


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_admin_ai_orchestration_endpoint_supports_probe_and_limit(monkeypatch):
    calls = []

    def _fake_orchestration_status(**kwargs):
        calls.append(dict(kwargs))
        return {
            "enabled": True,
            "selection_mode": "best",
            "provider_count": 2,
            "providers": [{"name": "OpenAIPrimary", "provider": "openai", "model": "gpt-4o-mini"}],
            "engine_count": 1,
            "engines": [{"name": "AARNNNeuromorphic", "type": "aarnn", "available": True}],
            "metrics": {
                "path": "job_data/ai/provider_metrics.json",
                "exists": True,
                "candidate_count": 1,
                "healthy_candidates": 1,
                "degraded_candidates": 0,
                "candidates": [],
            },
            "model_inventory": {
                "generated_at": "2026-04-15T17:00:00Z",
                "counts": {
                    "ready_models": 2,
                    "download_candidates": 1,
                },
                "models": [
                    {
                        "model": "llama3.2:latest",
                        "installed": True,
                        "runtime_ready": True,
                        "fit_status": "ready",
                    }
                ],
            },
        }

    monkeypatch.setattr(refiner_web, "_current_user", lambda: "alice")
    monkeypatch.setattr(refiner_web, "_is_admin_user", lambda _user: True)
    monkeypatch.setattr(refiner_web, "orchestration_status", _fake_orchestration_status)
    monkeypatch.setattr(
        refiner_web,
        "ai_model_inventory_monitor",
        SimpleNamespace(
            status=lambda: {
                "enabled": True,
                "running": True,
                "poll_sec": 900,
                "path": "job_data/ai/model_inventory.json",
                "last_run_at": "2026-04-15T17:05:00Z",
                "last_error": None,
                "last_summary": {"ready_models": 2},
            }
        ),
    )

    with refiner_web.app.test_client() as client:
        response = client.get("/api/admin/ai-orchestration?probe_engines=1&limit=7")

    assert response.status_code == 200
    data = response.get_json()
    assert data["probe_engines"] is True
    assert data["limit"] == 7
    assert data["provider_count"] == 2
    assert data["engines"][0]["type"] == "aarnn"
    assert data["model_inventory"]["counts"]["ready_models"] == 2
    assert data["model_inventory"]["monitor"]["running"] is True
    assert data["fetched_at"]
    assert calls == [
        {
            "config_path": f"{refiner_web.BASE_DIR}/config.json",
            "include_metrics": True,
            "probe_engines": True,
            "candidate_limit": 7,
        }
    ]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_llm_telemetry_janitor_prunes_old_buckets():
    telemetry = _DummyLLMTelemetryStore(prune_result=6)
    janitor = refiner_web.LLMTelemetryJanitor(
        telemetry_store_factory=lambda: telemetry,
        retention_hours=48,
        poll_sec=600,
    )

    removed = janitor.run_once()

    assert removed == 6
    assert telemetry.prune_calls == [48]
    status = janitor.status()
    assert status["available"] is True
    assert status["retention_hours"] == 48
    assert status["last_removed"] == 6
    assert status["last_error"] is None


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_mcp_server_save_uses_secret_refs(monkeypatch, tmp_path):
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "alice")
    monkeypatch.setattr(refiner_web, "_is_admin_user", lambda _user: True)
    monkeypatch.setattr(refiner_web, "user_store", _DummyUserStore())
    monkeypatch.setattr(refiner_web, "mcp_store", refiner_web.MCPServerStore(str(tmp_path / "mcp")))
    monkeypatch.setattr(refiner_web, "SECRET_STORE_ROOT", str(tmp_path / "secrets"))
    monkeypatch.setattr(refiner_web, "_secret_stores", {})
    monkeypatch.setattr(refiner_web, "url_allowed", lambda _url: True)
    monkeypatch.setattr(refiner_web, "_audit_event", lambda *args, **kwargs: None)

    with refiner_web.app.test_client() as client:
        response = client.post(
            "/api/mcp/servers",
            json={
                "name": "jira",
                "base_url": "https://mcp.example/rpc",
                "auth_type": "bearer",
                "auth_token": "secret-token",
                "headers": {"X-App": "refiner", "X-Team": "ops"},
            },
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["server"]["has_token"] is True
    assert sorted(data["server"]["headers"].keys()) == ["X-App", "X-Team"]

    stored = refiner_web.mcp_store.get_server("alice", "jira")
    assert stored is not None
    assert stored.auth_secret_ref
    assert stored.headers_secret_ref
    assert stored.auth_token is None
    assert stored.headers in (None, {})

    secret_store = refiner_web._get_secret_store("alice")
    assert secret_store.get(stored.auth_secret_ref) == "secret-token"
    header_payload = json.loads(secret_store.get(stored.headers_secret_ref))
    assert header_payload == {"X-App": "refiner", "X-Team": "ops"}
