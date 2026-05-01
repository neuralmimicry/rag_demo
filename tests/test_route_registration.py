import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "url_map")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402

from refiner_routes.admin import register_admin_routes


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_voice_and_assistant_routes_are_registered():
    rules = {rule.rule for rule in refiner_web.app.url_map.iter_rules()}
    assert "/api/version" in rules
    assert "/api/auth/config" in rules
    assert "/api/register" in rules
    assert "/api/voice/stt" in rules
    assert "/api/voice/tokens" in rules
    assert "/api/assistant/requirements" in rules
    assert "/api/assistant/form-fill" in rules
    assert "/api/playground/plan" in rules
    assert "/api/execution/plan" in rules
    assert "/api/rag/indexes" in rules
    assert "/api/rag/index" in rules
    assert "/api/rag/index/<name>" in rules
    assert "/api/rag/query" in rules
    assert "/api/todos/<todo_id>/route" in rules
    assert "/api/todos/<todo_id>/schedule" in rules
    assert "/api/schedules" in rules
    assert "/api/schedules/<schedule_id>" in rules
    assert "/api/schedules/<schedule_id>/cancel" in rules
    assert "/api/jobs/<job_id>/tasks" in rules
    assert "/api/jobs/<job_id>/tasks/<task_id>" in rules
    assert "/api/jobs/<job_id>/tasks/<task_id>/cancel" in rules
    assert "/api/subtasks" in rules
    assert "/api/subtasks/<task_id>" in rules
    assert "/api/subtasks/<task_id>/cancel" in rules
    assert "/api/admin/llm-telemetry" in rules
    assert "/api/admin/ai-orchestration" in rules
    assert "/api/admin/assistant/conversations" in rules
    assert "/api/admin/assistant/conversations/<conversation_id>" in rules
    assert "/api/admin/assistant/traces" in rules
    assert "/api/admin/assistant/traces/<trace_id>" in rules
    assert "/api/workers/telemetry" in rules


class _FakeApp:
    def __init__(self):
        self.rules = []

    def add_url_rule(self, rule, view_func=None, methods=None):
        self.rules.append({"rule": rule, "methods": tuple(methods or ())})


def _noop(*args, **kwargs):
    return None


def test_register_admin_routes_includes_assistant_admin_debug_endpoints():
    app = _FakeApp()

    register_admin_routes(
        app,
        metrics_path="/metrics",
        index=_noop,
        playground=_noop,
        admin_dashboard=_noop,
        public_asset=_noop,
        favicon=_noop,
        metrics=_noop,
        setup=_noop,
        health=_noop,
        api_version=_noop,
        capabilities_report=_noop,
        admin_stats=_noop,
        admin_llm_telemetry=_noop,
        admin_ai_orchestration=_noop,
        workers_telemetry=_noop,
        api_audit=_noop,
        assistant_admin_conversations=_noop,
        assistant_admin_conversation_detail=_noop,
        assistant_admin_traces=_noop,
        assistant_admin_trace_detail=_noop,
    )

    rules = {entry["rule"] for entry in app.rules}
    assert "/api/admin/assistant/conversations" in rules
    assert "/api/admin/assistant/conversations/<conversation_id>" in rules
    assert "/api/admin/assistant/traces" in rules
    assert "/api/admin/assistant/traces/<trace_id>" in rules
