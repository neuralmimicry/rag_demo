import time

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


def _set_session_user(client, username="alice"):
    with client.session_transaction() as sess:
        sess["user"] = username


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_todo_scheduler_executes_due_research_schedule_once(monkeypatch, tmp_path):
    local_todos = refiner_web.TodoStore(str(tmp_path / "todos"), claim_ttl_sec=300, retention_days=0)
    local_schedules = refiner_web.ScheduleStore(str(tmp_path / "schedules"), claim_ttl_sec=120)
    local_subtasks = refiner_web.SubtaskManager(workers=1, max_queue=4, task_ttl_sec=600)
    scheduler = refiner_web.TodoScheduler(
        schedule_store=local_schedules,
        todo_store=local_todos,
        subtask_manager=local_subtasks,
        poll_sec=60,
        execution_timeout_sec=30,
        orphan_ttl_sec=120,
    )

    monkeypatch.setattr(refiner_web, "todo_store", local_todos)
    calls = []

    def _fake_internal(*, user, path, handler, payload=None):
        calls.append((user, path, payload))
        assert user == "alice"
        assert path == "/api/jobs"
        return {"job_id": "job-123", "workflow": "topic_research", "status": "queued"}

    monkeypatch.setattr(refiner_web, "_invoke_internal_post_json", _fake_internal)

    item = local_todos.add_item(
        "alice",
        "Investigate the Jira pagination fallback and compare candidate fixes",
        source="manual",
        device="web",
        defer_until_idle=False,
    )
    schedule = local_schedules.create(user="alice", todo_id=item["id"], run_at=refiner_web._now_iso())

    for _ in range(40):
        scheduler.run_once()
        updated = local_schedules.get_item(schedule["id"], user="alice")
        if updated and updated["status"] == "completed":
            break
        time.sleep(0.02)

    assert updated["status"] == "completed"
    stored = local_todos.get_item("alice", item["id"])
    assert stored["status"] == "done"
    assert stored["last_result"]["job_id"] == "job-123"
    assert stored["links"]["jobs"] == ["job-123"]

    for _ in range(5):
        scheduler.run_once()

    assert len(calls) == 1


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_todo_scheduler_executes_due_assistant_schedule(monkeypatch, tmp_path):
    local_todos = refiner_web.TodoStore(str(tmp_path / "todos"), claim_ttl_sec=300, retention_days=0)
    local_schedules = refiner_web.ScheduleStore(str(tmp_path / "schedules"), claim_ttl_sec=120)
    local_subtasks = refiner_web.SubtaskManager(workers=1, max_queue=4, task_ttl_sec=600)
    scheduler = refiner_web.TodoScheduler(
        schedule_store=local_schedules,
        todo_store=local_todos,
        subtask_manager=local_subtasks,
        poll_sec=60,
        execution_timeout_sec=30,
        orphan_ttl_sec=120,
    )

    monkeypatch.setattr(refiner_web, "todo_store", local_todos)
    monkeypatch.setattr(
        refiner_web,
        "_invoke_internal_post_json",
        lambda **_kwargs: {"answer": "Requirements drafted"},
    )

    item = local_todos.add_item(
        "alice",
        "Draft requirements for a retry dashboard with acceptance criteria",
        source="manual",
        device="web",
        defer_until_idle=False,
    )
    schedule = local_schedules.create(user="alice", todo_id=item["id"], run_at=refiner_web._now_iso())

    for _ in range(40):
        scheduler.run_once()
        updated = local_schedules.get_item(schedule["id"], user="alice")
        if updated and updated["status"] == "completed":
            break
        time.sleep(0.02)

    assert updated["status"] == "completed"
    stored = local_todos.get_item("alice", item["id"])
    assert stored["status"] == "done"
    assert stored["last_result"]["response"]["answer"] == "Requirements drafted"
    assert stored["links"]["schedules"] == [schedule["id"]]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_api_todo_schedule_create_and_cancel(monkeypatch, tmp_path):
    local_todos = refiner_web.TodoStore(str(tmp_path / "todos"), claim_ttl_sec=300, retention_days=0)
    local_schedules = refiner_web.ScheduleStore(str(tmp_path / "schedules"), claim_ttl_sec=120)
    local_subtasks = refiner_web.SubtaskManager(workers=1, max_queue=4, task_ttl_sec=600)

    monkeypatch.setattr(refiner_web, "todo_store", local_todos)
    monkeypatch.setattr(refiner_web, "schedule_store", local_schedules)
    monkeypatch.setattr(refiner_web, "subtask_manager", local_subtasks)
    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)

    item = local_todos.add_item(
        "alice",
        "Investigate scheduler cancellation handling",
        source="manual",
        device="web",
        defer_until_idle=False,
    )

    with refiner_web.app.test_client() as client:
        _set_session_user(client, "alice")
        create_response = client.post(f"/api/todos/{item['id']}/schedule", json={"delay_sec": 300})
        assert create_response.status_code == 201
        schedule_id = create_response.get_json()["schedule"]["id"]

        cancel_response = client.post(f"/api/schedules/{schedule_id}/cancel")
        assert cancel_response.status_code == 200
        assert cancel_response.get_json()["schedule"]["status"] == "cancelled"
