import json

from refiner import security_utils as su
def test_redact_text_masks_known_secret_shapes():
    text = "API_KEY=abc123 sk-abcdefabcdefabcdef token=ghp_abcdefghijklmnopqrstuvwxyz"
    redacted = su.redact_text(text)
    assert "API_KEY=***" in redacted
    assert "sk-abcdefabcdefabcdef" not in redacted
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in redacted


def test_url_allowed_respects_allowlist_and_private_guard(monkeypatch):
    monkeypatch.setenv("REFINER_URL_ALLOWLIST", "example.com")
    monkeypatch.delenv("REFINER_ALLOW_PRIVATE_URLS", raising=False)
    assert su.url_allowed("https://api.example.com/path")
    assert not su.url_allowed("https://evil.com/path")

    monkeypatch.delenv("REFINER_URL_ALLOWLIST", raising=False)
    monkeypatch.setattr(su, "is_private_host", lambda _host: True)
    assert not su.url_allowed("https://internal.service.local")

    monkeypatch.setenv("REFINER_ALLOW_PRIVATE_URLS", "1")
    assert su.url_allowed("https://internal.service.local")


def test_audit_logger_writes_jsonl_record(tmp_path):
    audit_path = tmp_path / "audit.log"
    logger = su.AuditLogger(str(audit_path))
    logger.log("login", actor="alice", status="success", details={"ip_hash": "abc"})
    payload = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert payload["action"] == "login"
    assert payload["actor"] == "alice"
    assert payload["status"] == "success"
    assert payload["details"]["ip_hash"] == "abc"


def test_private_mcp_approval_is_exact_and_does_not_change_general_url_policy(monkeypatch):
    approved = "http://192.168.1.2:18180/mcp"
    monkeypatch.delenv("REFINER_URL_ALLOWLIST", raising=False)
    monkeypatch.delenv("REFINER_URL_BLOCKLIST", raising=False)
    monkeypatch.delenv("REFINER_ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(su, "is_private_host", lambda _host: True)
    monkeypatch.setenv("REFINER_MCP_PRIVATE_URLS", approved)
    assert su.mcp_url_allowed(approved)
    assert not su.url_allowed(approved)
    for denied in [approved + "/other", approved + "?target=admin", approved + "#fragment",
                   "http://192.168.1.2:18181/mcp", "http://192.168.1.3:18180/mcp",
                   "http://user@192.168.1.2:18180/mcp"]:
        assert not su.mcp_url_allowed(denied)
    monkeypatch.setenv("REFINER_URL_BLOCKLIST", "192.168.1.2")
    assert not su.mcp_url_allowed(approved)
    monkeypatch.delenv("REFINER_URL_BLOCKLIST")
    monkeypatch.setenv("REFINER_URL_ALLOWLIST", "example.com")
    assert not su.mcp_url_allowed(approved)
    monkeypatch.delenv("REFINER_URL_ALLOWLIST")
    monkeypatch.delenv("REFINER_MCP_PRIVATE_URLS")
    assert not su.mcp_url_allowed(approved)
