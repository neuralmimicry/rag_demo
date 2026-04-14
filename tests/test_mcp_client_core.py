import json

import pytest

from mcp_client import MCPClient, MCPServerConfig, MCPServerStore


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"result": {}}
        self.text = text

    def json(self):
        return self._payload


def test_mcp_client_builds_bearer_headers(monkeypatch):
    cfg = MCPServerConfig(name="demo", base_url="https://mcp.example/rpc", auth_token="secret", headers={"X-App": "refiner"})
    client = MCPClient(cfg)
    monkeypatch.setattr(client._session, "post", lambda *a, **k: _Resp(payload={"result": {"ok": True}}))
    out = client.list_tools()
    assert out["result"]["ok"] is True

    headers = client._headers()
    assert headers["Authorization"] == "Bearer secret"
    assert headers["X-App"] == "refiner"


def test_mcp_client_raises_on_http_or_rpc_error(monkeypatch):
    cfg = MCPServerConfig(name="demo", base_url="https://mcp.example/rpc")
    client = MCPClient(cfg)

    monkeypatch.setattr(client._session, "post", lambda *a, **k: _Resp(status_code=500, text="boom"))
    with pytest.raises(RuntimeError):
        client.list_tools()

    monkeypatch.setattr(client._session, "post", lambda *a, **k: _Resp(payload={"error": {"code": -32000}}))
    with pytest.raises(RuntimeError):
        client.list_resources()


def test_mcp_server_store_roundtrip(tmp_path):
    store = MCPServerStore(str(tmp_path))
    cfg = MCPServerConfig(
        name="jira",
        base_url="https://mcp.example/rpc",
        auth_secret_ref="MCP_JIRA_AUTH",
        headers_secret_ref="MCP_JIRA_HEADERS",
        metadata={"team": "ops"},
        runtime={"last_status": "success"},
    )
    store.save_server("alice", cfg)

    loaded = store.get_server("alice", "jira")
    assert loaded is not None
    assert loaded.base_url == "https://mcp.example/rpc"
    assert loaded.auth_secret_ref == "MCP_JIRA_AUTH"
    assert loaded.headers_secret_ref == "MCP_JIRA_HEADERS"
    assert loaded.runtime["last_status"] == "success"

    listed = store.list_servers("alice")
    assert len(listed) == 1

    path = tmp_path / "alice.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["servers"][0]["name"] == "jira"
    assert "auth_token" not in payload["servers"][0]
    assert payload["servers"][0]["auth_secret_ref"] == "MCP_JIRA_AUTH"
    assert store.update_runtime("alice", "jira", {"last_status": "failed", "last_error": "timeout"})
    reloaded = store.get_server("alice", "jira")
    assert reloaded is not None
    assert reloaded.runtime["last_status"] == "failed"
    assert reloaded.runtime["last_error"] == "timeout"
    assert store.delete_server("alice", "jira")
