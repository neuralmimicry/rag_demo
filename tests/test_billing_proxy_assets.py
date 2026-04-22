import flask
import pytest


HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_billing_assets_bypass_login_redirect(monkeypatch):
    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)
    monkeypatch.setattr(refiner_web, "_current_user", lambda: None)
    monkeypatch.setattr(refiner_web, "_billing_enabled", lambda: True)
    monkeypatch.setattr(
        refiner_web,
        "_proxy_service_request",
        lambda *args, **kwargs: refiner_web.Response("body { color: #123; }", mimetype="text/css"),
    )

    with refiner_web.app.test_client() as client:
        response = client.get("/billing/assets/dashboard.css")

    assert response.status_code == 200
    assert response.mimetype == "text/css"
    assert b"color" in response.data


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_billing_dashboard_still_requires_login(monkeypatch):
    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)
    monkeypatch.setattr(refiner_web, "_current_user", lambda: None)
    monkeypatch.setattr(refiner_web, "_billing_enabled", lambda: True)

    with refiner_web.app.test_client() as client:
        response = client.get("/billing", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_proxy_service_request_preserves_upstream_content_type(monkeypatch):
    class DummyUpstream:
        status_code = 200
        content = b"body { color: #123; }"
        headers = {
            "Content-Type": "text/css; charset=utf-8",
            "Cache-Control": "public, max-age=3600",
        }

    monkeypatch.setattr(refiner_web.requests, "request", lambda *args, **kwargs: DummyUpstream())

    with refiner_web.app.test_request_context("/billing/assets/dashboard.css"):
        response = refiner_web._proxy_service_request("http://billing.example", 10)

    assert response.status_code == 200
    assert response.mimetype == "text/css"
    assert response.headers["Cache-Control"] == "public, max-age=3600"
    assert b"color" in response.data
