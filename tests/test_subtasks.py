import time

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    import refiner_web  # noqa: E402


def _set_session_user(client, username="alice"):
    with client.session_transaction() as sess:
        sess["user"] = username


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_subtask_manager_executes_assistant_requirements(monkeypatch):
    local_subtasks = refiner_web.SubtaskManager(workers=1, max_queue=4, task_ttl_sec=600)
    monkeypatch.setattr(
        refiner_web,
        "_invoke_internal_post_json",
        lambda **_kwargs: {"answer": "hello from assistant"},
    )

    task = local_subtasks.submit(
        owner="alice",
        action="assistant_requirements",
        payload={"mode": "ask", "prompt": "hello"},
        scope_type="user",
        scope_id="alice",
        timeout_sec=30,
    )

    final_task = None
    for _ in range(40):
        final_task = local_subtasks.get_task(task.task_id)
        if final_task and final_task.status == "completed":
            break
        time.sleep(0.02)

    assert final_task is not None
    assert final_task.status == "completed"
    assert final_task.result["response"]["answer"] == "hello from assistant"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_api_subtask_cancel_queued_task(monkeypatch):
    local_subtasks = refiner_web.SubtaskManager(workers=1, max_queue=4, task_ttl_sec=600)
    monkeypatch.setattr(refiner_web, "subtask_manager", local_subtasks)
    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)

    def _fake_internal(*, payload=None, **_kwargs):
        if isinstance(payload, dict) and payload.get("prompt") == "slow":
            time.sleep(0.3)
        return {"answer": payload.get("prompt") if isinstance(payload, dict) else "ok"}

    monkeypatch.setattr(refiner_web, "_invoke_internal_post_json", _fake_internal)

    with refiner_web.app.test_client() as client:
        _set_session_user(client, "alice")
        first = client.post(
            "/api/subtasks",
            json={"action": "assistant_requirements", "payload": {"mode": "ask", "prompt": "slow"}},
        )
        assert first.status_code == 202

        second = client.post(
            "/api/subtasks",
            json={"action": "assistant_requirements", "payload": {"mode": "ask", "prompt": "queued"}},
        )
        assert second.status_code == 202
        queued_task_id = second.get_json()["task"]["task_id"]

        cancel_response = client.post(f"/api/subtasks/{queued_task_id}/cancel")
        assert cancel_response.status_code == 200

        final_status = None
        for _ in range(40):
            detail = client.get(f"/api/subtasks/{queued_task_id}?include_result=1")
            assert detail.status_code == 200
            final_status = detail.get_json()["task"]["status"]
            if final_status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.02)

    assert final_status == "cancelled"
