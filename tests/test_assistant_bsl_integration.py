from types import SimpleNamespace

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


class _FakeProvider:
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text="Here is a clear BSL-friendly explanation with signed avatar motion.",
            provider="fake_provider",
            model="fake_model",
        )


def _setup_authenticated_user(monkeypatch):
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "integration_tester")
    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_requirements_returns_bsl_motion_payload(monkeypatch):
    fake_provider = _FakeProvider()

    _setup_authenticated_user(monkeypatch)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: fake_provider)
    monkeypatch.setattr(refiner_web, "stt_learning_store", None)
    monkeypatch.setattr(refiner_web, "STT_GESTURE_ENABLED", True)
    monkeypatch.setattr(refiner_web, "STT_BSL_ENABLED", True)

    payload = {
        "mode": "ask",
        "prompt": "Sign British Sign Language example to me.",
        "requirements_text": (
            "You are the NeuralMimicry marketing assistant. "
            "Answer questions about NeuralMimicry, products, and services."
        ),
        "motionStyle": "BSL (British Sign Language)",
        "avatarMode": "office",
        "officeMode": "1",
        "messages": [],
    }

    with refiner_web.app.test_client() as client:
        response = client.post("/api/assistant/requirements", json=payload)

    assert response.status_code == 200
    data = response.get_json()

    assert data["reply"] == "Here is a clear BSL-friendly explanation with signed avatar motion."
    assert data["gesture_mode"] == "bsl"
    assert data["avatar_mode"] == "office"
    assert "avatar_motion" in data
    assert isinstance(data["avatar_motion"], dict)
    assert isinstance(data["avatar_motion"].get("keyframes"), list)
    assert len(data["avatar_motion"]["keyframes"]) >= 2
    assert data.get("gesture_summary", {}).get("style") == "bsl_signing"

    assert fake_provider.calls
    system_prompt = str(fake_provider.calls[0].get("system", "")).lower()
    assert "do not claim that you cannot sign" in system_prompt


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_requirements_returns_capacity_unavailable(monkeypatch):
    fake_provider = _FakeProvider()
    _setup_authenticated_user(monkeypatch)
    monkeypatch.setattr(refiner_web, "get_provider", lambda *args, **kwargs: fake_provider)
    monkeypatch.setattr(refiner_web, "_acquire_request_capacity", lambda *args, **kwargs: False)

    payload = {
        "mode": "ask",
        "prompt": "How do you sign hello in BSL?",
        "requirements_text": "NeuralMimicry marketing assistant context.",
        "messages": [],
    }
    with refiner_web.app.test_client() as client:
        response = client.post("/api/assistant/requirements", json=payload)
    assert response.status_code == 503
    assert response.get_json().get("error") == "assistant_capacity_unavailable"
    assert fake_provider.calls == []


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_reply_payload_prefers_nmstt_gesture_planner(monkeypatch):
    monkeypatch.setattr(refiner_web, "STT_GESTURE_ENABLED", True)
    monkeypatch.setattr(refiner_web, "STT_BSL_ENABLED", True)
    monkeypatch.setattr(refiner_web, "STT_GESTURE_NMSTT_FALLBACK", True)
    monkeypatch.setattr(refiner_web, "STT_BACKEND", "server")
    monkeypatch.setattr(refiner_web, "STT_SERVER_URL", "http://stt.local")
    monkeypatch.setattr(
        refiner_web,
        "_run_nmstt_gesture_plan",
        lambda *args, **kwargs: {
            "gesture_mode": "bsl",
            "avatar_mode": "office",
            "avatar_motion": {"duration_ms": 700, "keyframes": [{"t": 0, "pose": {}}]},
            "gesture_summary": {"style": "bsl_signing", "token_count": 1},
            "gesture_timeline": [{"word": "hello", "intent": "greeting", "template": "greeting", "start_ms": 0, "end_ms": 400}],
        },
    )
    monkeypatch.setattr(refiner_web, "plan_stt_avatar_motion", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    with refiner_web.app.test_request_context("/api/assistant/requirements", method="POST"):
        payload = refiner_web._assistant_reply_payload(
            "Hello in BSL.",
            provider="fake",
            model="fake",
            payload={"motionStyle": "BSL (British Sign Language)", "avatarMode": "office"},
        )
    assert payload["gesture_mode"] == "bsl"
    assert payload["avatar_mode"] == "office"
    assert isinstance(payload.get("avatar_motion"), dict)
