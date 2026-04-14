import json

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    import refiner_web  # noqa: E402


class _DummyUserStore:
    def __init__(self):
        self.role = "admin"
        self.email = "alice@example.com"
        self.metadata = {}

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
