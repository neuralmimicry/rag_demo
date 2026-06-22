import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client") and hasattr(flask, "Response")
if HAS_REAL_FLASK:
    from assistant_api.admin_handlers import build_assistant_admin_handlers


class _TraceStore:
    def __init__(self):
        self.calls = []

    def analytics_summary(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "window_hours": kwargs.get("since_hours"),
            "total_traces": 2,
            "success_rate": 1.0,
            "cache_hit_rate": 0.5,
            "handoff_rate": 0.0,
            "conversion_rate": 0.5,
            "avg_duration_ms": 1200.0,
            "breakdowns": {
                "route": [{"label": "assistant_rag_mcp", "count": 2}],
                "channel": [{"label": "whatsapp", "count": 2}],
                "assistant_profile": [{"label": "support", "count": 2}],
                "sentiment": [{"label": "neutral", "count": 2}],
                "provider": [{"label": "openai/gpt-5.1", "count": 2}],
                "error_code": [],
            },
        }


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_assistant_admin_analytics_route_returns_trace_store_summary():
    store = _TraceStore()
    app = flask.Flask(__name__)
    handlers = build_assistant_admin_handlers(
        current_user=lambda: "admin",
        is_admin_user=lambda user: user == "admin",
        get_assistant_conversation_store=lambda: None,
        get_assistant_trace_store=lambda: store,
        safe_int=_safe_int,
    )
    app.add_url_rule(
        "/api/admin/assistant/analytics",
        view_func=handlers["assistant_admin_analytics"],
    )

    response = app.test_client().get(
        "/api/admin/assistant/analytics"
        "?owner=alice&route=assistant_rag_mcp&channel=whatsapp&assistant_profile=support&since_hours=12&limit=500"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["analytics"]["total_traces"] == 2
    assert payload["filters"]["channel"] == "whatsapp"
    assert store.calls[0]["owner"] == "alice"
    assert store.calls[0]["route"] == "assistant_rag_mcp"
    assert store.calls[0]["assistant_profile"] == "support"
    assert store.calls[0]["since_hours"] == 12
    assert store.calls[0]["limit"] == 500
