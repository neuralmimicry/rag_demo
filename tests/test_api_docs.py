from __future__ import annotations

import builtins

import flask
import pytest
import requests

from refiner import api_docs
HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")


class _Response:
    ok = True


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_public_health_does_not_reimport_refiner_web(monkeypatch) -> None:
    app = flask.Flask(__name__)
    api_docs.add_api_documentation_support(
        app,
        stt_server_url="http://127.0.0.1:7079",
        redis_enabled=lambda: False,
        continuum_enabled=lambda: False,
    )

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Response())

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"refiner_web", "refiner.refiner_web"}:
            raise AssertionError("health route should not import refiner_web")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    resp = app.test_client().get("/health")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "healthy"
    assert payload["services"]["stt"] == "available"
    assert payload["services"]["redis"] == "disabled"
    assert payload["services"]["continuum"] == "disabled"
