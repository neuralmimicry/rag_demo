import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "url_map")
if HAS_REAL_FLASK:
    import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_voice_and_assistant_routes_are_registered():
    rules = {rule.rule for rule in refiner_web.app.url_map.iter_rules()}
    assert "/api/voice/stt" in rules
    assert "/api/voice/tokens" in rules
    assert "/api/assistant/requirements" in rules
    assert "/api/assistant/form-fill" in rules
    assert "/api/playground/plan" in rules
    assert "/api/jobs/<job_id>/tasks" in rules
    assert "/api/jobs/<job_id>/tasks/<task_id>" in rules
    assert "/api/jobs/<job_id>/tasks/<task_id>/cancel" in rules
    assert "/api/workers/telemetry" in rules
