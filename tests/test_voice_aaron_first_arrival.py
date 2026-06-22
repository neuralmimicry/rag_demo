import flask
import pytest

from assistant_pipeline.contracts import ServiceResult
from assistant_pipeline.runtime.first_arrival_gate import reset_first_arrival_claims_for_tests

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client") and hasattr(flask, "Response")


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Voice route tests require a real Flask runtime")
def test_voice_aaron_assist_suppresses_duplicate_cross_channel(monkeypatch):
    from refiner import refiner_web

    reset_first_arrival_claims_for_tests()
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_ENABLED", "1")
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_WINDOW_SEC", "20")
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_CHANNELS", "siri,alexa")

    calls = []

    def _fake_requirements(deps, *, user, payload):
        calls.append({"user": user, "payload": dict(payload)})
        return ServiceResult({"reply": "Ready."})

    monkeypatch.setattr(refiner_web.assistant_service, "assistant_requirements", _fake_requirements)
    monkeypatch.setattr(refiner_web, "_assistant_pipeline_dependencies", lambda: object())

    first = refiner_web._voice_aaron_assist("alice", text="Aaron check deployment", channel="siri")
    duplicate = refiner_web._voice_aaron_assist("alice", text="Aaron check deployment", channel="alexa")

    assert first["wake_detected"] is True
    assert first.get("suppressed_duplicate") is not True
    assert first["reply"] == "Ready."

    assert duplicate["wake_detected"] is True
    assert duplicate["suppressed_duplicate"] is True
    assert duplicate["winner_channel"] == "siri"
    assert duplicate["reply"] == ""
    assert len(calls) == 1

    reset_first_arrival_claims_for_tests()
