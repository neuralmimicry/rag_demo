import flask
import pytest

from assistant_pipeline.contracts import ServiceResult
from assistant_pipeline.runtime.first_arrival_gate import reset_first_arrival_claims_for_tests

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client") and hasattr(flask, "Response")
if HAS_REAL_FLASK:
    from assistant_api.assistant_handlers import build_assistant_handlers
    from assistant_api import assistant_handlers as assistant_handlers_module


class _Deps:
    def __init__(self, user="alice"):
        self._user = user

    def current_user(self):
        return self._user


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_aaron_respond_requires_wake_word_when_requested(monkeypatch):
    def _unexpected_requirements(*args, **kwargs):
        raise AssertionError("assistant_requirements should not be invoked when wake word is required and missing")

    monkeypatch.setattr(assistant_handlers_module.assistant_service, "assistant_requirements", _unexpected_requirements)

    app = flask.Flask(__name__)
    handlers = build_assistant_handlers(_Deps("alice"))
    app.add_url_rule("/api/assistant/aaron/respond", view_func=handlers["assistant_aaron_respond"], methods=["POST"])

    response = app.test_client().post(
        "/api/assistant/aaron/respond",
        json={"text": "status update", "channel": "telegram", "require_wake_word": True},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["channel"] == "telegram"
    assert payload["wake_word_detected"] is False
    assert "Say Aaron" in payload["reply"]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_aaron_respond_strips_wake_prefix_and_routes_to_requirements(monkeypatch):
    calls = []

    def _fake_requirements(deps, *, user, payload):
        calls.append({"user": user, "payload": dict(payload)})
        return ServiceResult({"reply": "Ready."})

    monkeypatch.setattr(assistant_handlers_module.assistant_service, "assistant_requirements", _fake_requirements)

    app = flask.Flask(__name__)
    handlers = build_assistant_handlers(_Deps("alice"))
    app.add_url_rule("/api/assistant/aaron/respond", view_func=handlers["assistant_aaron_respond"], methods=["POST"])

    response = app.test_client().post(
        "/api/assistant/aaron/respond",
        json={"text": "Aaron, provide a release summary", "channel": "whatsapp", "mode": "ask"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["assistant_name"] == "Aaron"
    assert payload["channel"] == "whatsapp"
    assert payload["reply"] == "Ready."
    assert calls[0]["user"] == "alice"
    assert calls[0]["payload"]["prompt"] == "provide a release summary"
    assert calls[0]["payload"]["channel_context"]["name"] == "whatsapp"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_aaron_respond_emits_google_response_for_google_home_channel(monkeypatch):
    def _fake_rag(deps, *, user, payload):
        return ServiceResult({"answer": "Deployment is healthy."})

    monkeypatch.setattr(assistant_handlers_module.assistant_service, "assistant_rag_mcp", _fake_rag)

    app = flask.Flask(__name__)
    handlers = build_assistant_handlers(_Deps("alice"))
    app.add_url_rule("/api/assistant/aaron/respond", view_func=handlers["assistant_aaron_respond"], methods=["POST"])

    response = app.test_client().post(
        "/api/assistant/aaron/respond",
        json={
            "text": "Aaron, check deployment health",
            "channel": "google home",
            "workflow": "assistant_rag_mcp",
            "rag": {"index": "ops"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["channel"] == "google_home"
    assert payload["google_response"]["fulfillmentText"] == "Deployment is healthy."


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_aaron_respond_requires_authenticated_user():
    app = flask.Flask(__name__)
    handlers = build_assistant_handlers(_Deps(None))
    app.add_url_rule("/api/assistant/aaron/respond", view_func=handlers["assistant_aaron_respond"], methods=["POST"])

    response = app.test_client().post("/api/assistant/aaron/respond", json={"text": "Aaron, hello"})

    assert response.status_code == 401
    payload = response.get_json()
    assert payload["error"] == "unauthorized"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_telegram_webhook_routes_update_into_aaron(monkeypatch):
    calls = []

    def _fake_requirements(deps, *, user, payload):
        calls.append({"user": user, "payload": dict(payload)})
        return ServiceResult({"reply": "Roger that."})

    monkeypatch.setattr(assistant_handlers_module.assistant_service, "assistant_requirements", _fake_requirements)

    app = flask.Flask(__name__)
    handlers = build_assistant_handlers(_Deps("ignored"))
    app.add_url_rule(
        "/api/assistant/channels/telegram/webhook",
        view_func=handlers["assistant_telegram_webhook"],
        methods=["POST"],
    )

    response = app.test_client().post(
        "/api/assistant/channels/telegram/webhook",
        json={
            "update_id": 1001,
            "message": {
                "chat": {"id": 778899},
                "from": {"id": 12345},
                "text": "Aaron, summarise release risks",
            },
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["provider"] == "telegram"
    assert str(payload["chat_id"]) == "778899"
    assert payload["assistant"]["channel"] == "telegram"
    assert payload["assistant"]["reply"] == "Roger that."
    assert calls[0]["user"] == "telegram:778899"
    assert calls[0]["payload"]["prompt"] == "summarise release risks"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_whatsapp_webhook_verification_roundtrip(monkeypatch):
    monkeypatch.setenv("REFINER_WHATSAPP_VERIFY_TOKEN", "verify-me")

    app = flask.Flask(__name__)
    handlers = build_assistant_handlers(_Deps("ignored"))
    app.add_url_rule(
        "/api/assistant/channels/whatsapp/webhook",
        view_func=handlers["assistant_whatsapp_webhook"],
        methods=["GET", "POST"],
    )

    response = app.test_client().get(
        "/api/assistant/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=abc123"
    )
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "abc123"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_whatsapp_webhook_routes_message_into_aaron(monkeypatch):
    calls = []

    def _fake_requirements(deps, *, user, payload):
        calls.append({"user": user, "payload": dict(payload)})
        return ServiceResult({"reply": "Done."})

    monkeypatch.setattr(assistant_handlers_module.assistant_service, "assistant_requirements", _fake_requirements)

    app = flask.Flask(__name__)
    handlers = build_assistant_handlers(_Deps("ignored"))
    app.add_url_rule(
        "/api/assistant/channels/whatsapp/webhook",
        view_func=handlers["assistant_whatsapp_webhook"],
        methods=["GET", "POST"],
    )

    response = app.test_client().post(
        "/api/assistant/channels/whatsapp/webhook",
        json={
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "1099988"},
                                "messages": [
                                    {
                                        "id": "wamid.HBgMNTU",
                                        "from": "447700900123",
                                        "text": {"body": "Aaron, list open blockers"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["provider"] == "whatsapp"
    assert payload["assistant"]["channel"] == "whatsapp"
    assert payload["assistant"]["reply"] == "Done."
    assert payload["whatsapp_response"]["to"] == "447700900123"
    assert payload["whatsapp_response"]["text"]["body"] == "Done."
    assert calls[0]["user"] == "whatsapp:447700900123"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_aaron_respond_suppresses_duplicate_cross_channel_responses(monkeypatch):
    reset_first_arrival_claims_for_tests()
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_ENABLED", "1")
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_WINDOW_SEC", "20")
    monkeypatch.setenv(
        "REFINER_AARON_FIRST_ARRIVAL_CHANNELS",
        "alexa,google_home,google_assistant,siri,whatsapp,telegram",
    )
    calls = []

    def _fake_requirements(deps, *, user, payload):
        calls.append({"user": user, "payload": dict(payload)})
        return ServiceResult({"reply": "First channel reply"})

    monkeypatch.setattr(assistant_handlers_module.assistant_service, "assistant_requirements", _fake_requirements)

    app = flask.Flask(__name__)
    handlers = build_assistant_handlers(_Deps("alice"))
    app.add_url_rule("/api/assistant/aaron/respond", view_func=handlers["assistant_aaron_respond"], methods=["POST"])

    first_response = app.test_client().post(
        "/api/assistant/aaron/respond",
        json={"text": "Aaron, check deployment health", "channel": "whatsapp"},
    )
    assert first_response.status_code == 200
    first_payload = first_response.get_json()
    assert first_payload["reply"] == "First channel reply"
    assert first_payload.get("delivery_suppressed") is not True

    duplicate_response = app.test_client().post(
        "/api/assistant/aaron/respond",
        json={"text": "Aaron, check deployment health", "channel": "alexa"},
    )
    assert duplicate_response.status_code == 200
    duplicate_payload = duplicate_response.get_json()
    assert duplicate_payload["reply"] == ""
    assert duplicate_payload["delivery_suppressed"] is True
    assert duplicate_payload["first_channel"] == "whatsapp"
    assert duplicate_payload["response"]["delivery_suppressed"] is True
    assert len(calls) == 1

    reset_first_arrival_claims_for_tests()
