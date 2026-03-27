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
    cfg = MCPServerConfig(name="jira", base_url="https://mcp.example/rpc", auth_token="tok")
    store.save_server("alice", cfg)

    loaded = store.get_server("alice", "jira")
    assert loaded is not None
    assert loaded.base_url == "https://mcp.example/rpc"

    listed = store.list_servers("alice")
    assert len(listed) == 1

    path = tmp_path / "alice.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["servers"][0]["name"] == "jira"
    assert store.delete_server("alice", "jira")
