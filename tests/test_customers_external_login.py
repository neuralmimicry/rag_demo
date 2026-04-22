import flask
import pytest


HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_customers_external_login_bypasses_refiner_login_gate(monkeypatch):
    calls = []

    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)
    monkeypatch.setattr(refiner_web, "_current_user", lambda: None)
    monkeypatch.setattr(refiner_web, "_customers_enabled", lambda: True)

    def fake_proxy(base_url, timeout, *, path=None):
        calls.append({"base_url": base_url, "timeout": timeout, "path": path})
        return refiner_web.Response("proxied", mimetype="text/plain")

    monkeypatch.setattr(refiner_web, "_proxy_service_request", fake_proxy)

    with refiner_web.app.test_client() as client:
        response = client.get(
            "/auth/external-login",
            query_string={"rd": "https://octobot.neuralmimicry.ai/"},
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert response.data == b"proxied"
    assert calls == [{"base_url": refiner_web.CUSTOMERS_API_BASE, "timeout": refiner_web.CUSTOMERS_TIMEOUT, "path": None}]
