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


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_customers_auth_config_is_proxied(monkeypatch):
    calls = []

    monkeypatch.setattr(refiner_web, "_customers_enabled", lambda: True)

    def fake_proxy(base_url, timeout, *, path=None):
        calls.append(
            {
                "base_url": base_url,
                "timeout": timeout,
                "path": path,
                "method": flask.request.method,
                "request_path": flask.request.path,
            }
        )
        return refiner_web.jsonify({"status": "proxied", "request_path": flask.request.path})

    monkeypatch.setattr(refiner_web, "_proxy_service_request", fake_proxy)

    with refiner_web.app.test_client() as client:
        response = client.get("/api/auth/config")

    assert response.status_code == 200
    assert response.get_json() == {"status": "proxied", "request_path": "/api/auth/config"}
    assert calls == [
        {
            "base_url": refiner_web.CUSTOMERS_API_BASE,
            "timeout": refiner_web.CUSTOMERS_TIMEOUT,
            "path": None,
            "method": "GET",
            "request_path": "/api/auth/config",
        }
    ]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_customers_register_is_proxied(monkeypatch):
    calls = []

    monkeypatch.setattr(refiner_web, "_customers_enabled", lambda: True)

    def fake_proxy(base_url, timeout, *, path=None):
        calls.append(
            {
                "base_url": base_url,
                "timeout": timeout,
                "path": path,
                "method": flask.request.method,
                "request_path": flask.request.path,
                "payload": flask.request.get_json(silent=True),
            }
        )
        return refiner_web.jsonify({"status": "proxied", "request_path": flask.request.path}), 201

    monkeypatch.setattr(refiner_web, "_proxy_service_request", fake_proxy)

    with refiner_web.app.test_client() as client:
        response = client.post(
            "/api/register",
            json={
                "username": "bob",
                "email": "bob@example.com",
                "password": "bob password 123",
                "confirm": "bob password 123",
                "create_team": True,
                "workspace_name": "Bob Workspace",
            },
        )

    assert response.status_code == 201
    assert response.get_json() == {"status": "proxied", "request_path": "/api/register"}
    assert calls == [
        {
            "base_url": refiner_web.CUSTOMERS_API_BASE,
            "timeout": refiner_web.CUSTOMERS_TIMEOUT,
            "path": None,
            "method": "POST",
            "request_path": "/api/register",
            "payload": {
                "username": "bob",
                "email": "bob@example.com",
                "password": "bob password 123",
                "confirm": "bob password 123",
                "create_team": True,
                "workspace_name": "Bob Workspace",
            },
        }
    ]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_customers_register_page_is_proxied(monkeypatch):
    calls = []

    monkeypatch.setattr(refiner_web, "_customers_enabled", lambda: True)

    def fake_proxy(base_url, timeout, *, path=None):
        calls.append(
            {
                "base_url": base_url,
                "timeout": timeout,
                "path": path,
                "method": flask.request.method,
                "request_path": flask.request.path,
            }
        )
        return refiner_web.Response("proxied", mimetype="text/plain")

    monkeypatch.setattr(refiner_web, "_proxy_service_request", fake_proxy)

    with refiner_web.app.test_client() as client:
        response = client.get("/register")

    assert response.status_code == 200
    assert response.data == b"proxied"
    assert calls == [
        {
            "base_url": refiner_web.CUSTOMERS_API_BASE,
            "timeout": refiner_web.CUSTOMERS_TIMEOUT,
            "path": None,
            "method": "GET",
            "request_path": "/register",
        }
    ]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/login/mfa/totp", {"code": "123456"}),
        ("POST", "/api/profile/mfa/totp/start", {}),
        ("POST", "/api/profile/mfa/totp/verify", {"code": "123456"}),
        ("POST", "/api/profile/mfa/totp/disable", {}),
        ("POST", "/api/profile/passkeys/register/options", {}),
        ("POST", "/api/profile/passkeys/register/verify", {"credential": {"id": "cred-1"}}),
        ("DELETE", "/api/profile/passkeys/cred-1", None),
        ("POST", "/api/passkeys/authenticate/options", {"username": "bob"}),
        ("POST", "/api/passkeys/authenticate/verify", {"credential": {"id": "cred-1"}}),
    ],
)
@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_customers_advanced_auth_routes_are_proxied(monkeypatch, method, path, payload):
    calls = []

    monkeypatch.setattr(refiner_web, "_customers_enabled", lambda: True)

    def fake_proxy(base_url, timeout, *, path=None):
        calls.append(
            {
                "base_url": base_url,
                "timeout": timeout,
                "path": path,
                "method": flask.request.method,
                "request_path": flask.request.path,
                "payload": flask.request.get_json(silent=True),
            }
        )
        return refiner_web.jsonify({"status": "proxied", "request_path": flask.request.path})

    monkeypatch.setattr(refiner_web, "_proxy_service_request", fake_proxy)

    with refiner_web.app.test_client() as client:
        response = client.open(path, method=method, json=payload)

    assert response.status_code == 200
    assert response.get_json() == {"status": "proxied", "request_path": path}
    assert calls == [
        {
            "base_url": refiner_web.CUSTOMERS_API_BASE,
            "timeout": refiner_web.CUSTOMERS_TIMEOUT,
            "path": None,
            "method": method,
            "request_path": path,
            "payload": payload,
        }
    ]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_customers_identity_normalizes_service_access_contract():
    identity = refiner_web._normalize_auth_identity(
        {
            "authenticated": True,
            "user": "bob",
            "role": "user",
            "groups": ["user"],
            "visible_groups": ["user"],
            "manageable_groups": [],
            "service_access": {
                "refiner": {
                    "access_level": "use",
                    "public_access_level": "request",
                },
                "continuum": {
                    "access_level": "none",
                    "public_access_level": "observe",
                },
            },
            "visible_services": ["refiner", "continuum"],
        }
    )

    assert identity is not None
    assert identity["visible_groups"] == ["user"]
    assert identity["can_manage_access"] is False
    assert set(identity["visible_services"]) >= {"refiner", "continuum"}
    assert identity["service_access"]["refiner"]["can_use"] is True
    assert identity["service_access"]["continuum"]["access_level"] == "none"
    assert identity["service_access"]["continuum"]["can_observe"] is True


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_service_account_identity_does_not_receive_refiner_use_by_default():
    identity = refiner_web._normalize_auth_identity(
        {
            "authenticated": True,
            "identity_type": "service_account",
            "user": "tracey-sync",
            "role": "service_account",
            "groups": ["ops"],
            "service_access": {},
        }
    )

    assert identity is not None
    assert identity["groups"] == ["ops"]
    assert identity["service_access"]["refiner"]["access_level"] == "none"
    assert identity["service_access"]["refiner"]["can_request"] is True
    assert identity["service_access"]["refiner"]["can_use"] is False


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_refiner_control_access_is_treated_as_admin(monkeypatch):
    identity = refiner_web._normalize_auth_identity(
        {
            "authenticated": True,
            "user": "bob",
            "role": "user",
            "groups": ["user"],
            "service_access": {
                "refiner": {
                    "access_level": "control",
                    "public_access_level": "request",
                }
            },
        }
    )

    with refiner_web.app.test_request_context("/api/admin/stats"):
        monkeypatch.setattr(refiner_web, "_current_auth_profile", lambda: identity)
        monkeypatch.setattr(refiner_web, "_current_auth_identity", lambda: None)
        assert refiner_web._is_admin_user("bob") is True


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_refiner_requires_service_use_access(monkeypatch):
    identity = refiner_web._normalize_auth_identity(
        {
            "authenticated": True,
            "user": "bob",
            "role": "user",
            "groups": ["user"],
            "service_access": {
                "refiner": {
                    "access_level": "none",
                    "public_access_level": "request",
                }
            },
        }
    )

    with refiner_web.app.test_request_context("/api/jobs"):
        monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)
        monkeypatch.setattr(refiner_web, "_current_user", lambda: "bob")
        monkeypatch.setattr(refiner_web, "_current_auth_profile", lambda: identity)
        monkeypatch.setattr(refiner_web, "_current_auth_identity", lambda: None)

        block = refiner_web._require_login()

    assert isinstance(block, tuple)
    response, status_code = block
    assert status_code == 403
    assert response.get_json()["error"] == "forbidden"
    assert response.get_json()["service"] == "refiner"
