import base64

import flask
import pytest
import requests

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    import refiner_web  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_run_stt_server_bytes_retries_transport_failures(monkeypatch):
    calls = {"count": 0, "data": {}}

    class _FakeSession:
        def post(self, _url, *, files, data, timeout):
            calls["count"] += 1
            calls["data"] = dict(data)
            assert timeout == 0.01
            assert "audio" in files
            if calls["count"] == 1:
                raise requests.exceptions.Timeout("simulated timeout")
            return _FakeResponse(
                200,
                {
                    "text": "Signed hello",
                    "gesture_mode": "bsl",
                    "avatar_mode": "office",
                    "avatar_motion": {
                        "duration_ms": 800,
                        "keyframes": [
                            {"t": 0, "pose": {"leftShoulderRoll": 0.0}},
                            {"t": 300, "pose": {"leftShoulderRoll": 0.2}},
                        ],
                    },
                    "gesture_summary": {"style": "bsl_signing", "token_count": 2},
                    "gesture_timeline": [
                        {"word": "Signed", "intent": "lexical", "template": "hello", "start_ms": 0, "end_ms": 300}
                    ],
                    "collaboration_mode": "1",
                },
            )

    monkeypatch.setattr(refiner_web, "_stt_server_session", lambda: _FakeSession())
    monkeypatch.setattr(refiner_web, "STT_SERVER_URL", "http://stt.local")
    monkeypatch.setattr(refiner_web, "STT_SERVER_RETRIES", 1)
    monkeypatch.setattr(refiner_web, "STT_SERVER_TIMEOUT", 0.01)
    monkeypatch.setattr(refiner_web, "STT_SERVER_BACKOFF_BASE", 0.0)
    monkeypatch.setattr(refiner_web, "STT_SERVER_BACKOFF_MAX", 0.0)
    monkeypatch.setattr(refiner_web.time, "sleep", lambda *_: None)

    text, error, payload = refiner_web._run_stt_server_bytes(
        b"audio-bytes",
        ".webm",
        "en-GB",
        gesture_mode="BSL (British Sign Language)",
        avatar_mode="office",
        office_mode=True,
        collaboration_mode=True,
    )

    assert error is None
    assert text == "Signed hello"
    assert calls["count"] == 2
    assert calls["data"]["motionStyle"] == "BSL (British Sign Language)"
    assert calls["data"]["officeMode"] == "1"
    assert calls["data"]["multiSpeaker"] == "1"
    assert isinstance(payload, dict)
    assert payload["gesture_mode"] == "bsl"
    assert payload["avatar_mode"] == "office"
    assert payload["collaboration_mode"] is True


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_resolve_stt_backend_defaults_to_server_when_url_is_configured():
    assert refiner_web._resolve_stt_backend("", "http://stt.local") == "server"
    assert refiner_web._resolve_stt_backend(None, "http://stt.local") == "server"
    assert refiner_web._resolve_stt_backend("", "") == "command"
    assert refiner_web._resolve_stt_backend("command", "http://stt.local") == "command"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_api_voice_stt_uses_direct_server_path_without_preprocess(monkeypatch):
    calls = {"direct": 0, "temp_write": 0}

    def _fake_stt_server_bytes(*args, **kwargs):
        calls["direct"] += 1
        return "hello from direct server path", None, {"gesture_mode": "bsl", "avatar_mode": "office"}

    def _fail_temp_write(*args, **kwargs):
        calls["temp_write"] += 1
        raise AssertionError("temp file writes should be skipped in direct server mode")

    monkeypatch.setattr(refiner_web, "_stt_authorized", lambda payload=None: True)
    monkeypatch.setattr(refiner_web, "_voice_user_from_request", lambda payload=None: "integration_tester")
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "integration_tester")
    monkeypatch.setattr(refiner_web, "_stt_record_learning", lambda *args, **kwargs: None)
    monkeypatch.setattr(refiner_web, "_audit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(refiner_web, "_run_stt_server_bytes", _fake_stt_server_bytes)
    monkeypatch.setattr(refiner_web, "_write_audio_temp", _fail_temp_write)
    monkeypatch.setattr(refiner_web, "STT_BACKEND", "server")
    monkeypatch.setattr(refiner_web, "STT_SERVER_URL", "http://stt.local")
    monkeypatch.setattr(refiner_web, "STT_SERVER_PREPROCESS", False)
    monkeypatch.setattr(refiner_web, "STT_PREPROCESS_COMMAND", "ffmpeg -i {input} {output}")
    monkeypatch.setattr(refiner_web, "STT_GESTURE_ENABLED", False)

    payload = {
        "audio_base64": base64.b64encode(b"binary-audio").decode("ascii"),
        "lang": "en-GB",
        "motionStyle": "BSL (British Sign Language)",
        "avatarMode": "office",
    }

    with refiner_web.app.test_client() as client:
        response = client.post("/api/voice/stt", json=payload)

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["text"] == "hello from direct server path"
    assert data["gesture_mode"] == "bsl"
    assert data["avatar_mode"] == "office"
    assert calls["direct"] == 1
    assert calls["temp_write"] == 0


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_api_voice_stt_returns_capacity_unavailable(monkeypatch):
    monkeypatch.setattr(refiner_web, "_stt_authorized", lambda payload=None: True)
    monkeypatch.setattr(refiner_web, "_voice_user_from_request", lambda payload=None: "integration_tester")
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "integration_tester")
    monkeypatch.setattr(refiner_web, "_acquire_request_capacity", lambda *args, **kwargs: False)

    payload = {
        "audio_base64": base64.b64encode(b"binary-audio").decode("ascii"),
        "lang": "en-GB",
    }
    with refiner_web.app.test_client() as client:
        response = client.post("/api/voice/stt", json=payload)
    assert response.status_code == 503
    assert response.get_json().get("error") == "stt_capacity_unavailable"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_run_nmstt_gesture_plan_uses_dedicated_endpoint(monkeypatch):
    calls = {"url": "", "payload": {}}

    class _FakeSession:
        def post(self, url, *, json, timeout):
            calls["url"] = url
            calls["payload"] = dict(json)
            assert timeout == 0.2
            return _FakeResponse(
                200,
                {
                    "status": "ok",
                    "text": "hello",
                    "gesture_mode": "bsl",
                    "avatar_mode": "office",
                    "avatar_motion": {"duration_ms": 700, "keyframes": [{"t": 0, "pose": {}}]},
                    "gesture_summary": {"style": "bsl_signing", "token_count": 1},
                    "gesture_timeline": [{"word": "hello", "intent": "greeting", "template": "greeting", "start_ms": 0, "end_ms": 400}],
                },
            )

    monkeypatch.setattr(refiner_web, "_stt_server_session", lambda: _FakeSession())
    monkeypatch.setattr(refiner_web, "STT_SERVER_URL", "http://stt.local")
    monkeypatch.setattr(refiner_web, "STT_GESTURE_NMSTT_FALLBACK", True)
    monkeypatch.setattr(refiner_web, "STT_SERVER_RETRIES", 0)
    monkeypatch.setattr(refiner_web, "STT_GESTURE_NMSTT_TIMEOUT", 0.2)

    payload = refiner_web._run_nmstt_gesture_plan(
        "hello",
        gesture_mode="bsl",
        avatar_mode="office",
        office_mode=True,
    )
    assert calls["url"] == "http://stt.local/gesture-plan"
    assert calls["payload"]["motion_style"] == "bsl"
    assert isinstance(payload, dict)
    assert payload["gesture_mode"] == "bsl"
