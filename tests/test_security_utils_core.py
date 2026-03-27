import json

import security_utils as su


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
