import queue
import threading
import time

import flask
import pytest
from assistant_pipeline.contracts import ServiceResult

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


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


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_subtask_manager_executes_rag_collection_build(monkeypatch):
    local_subtasks = refiner_web.SubtaskManager(workers=1, max_queue=4, task_ttl_sec=600)
    monkeypatch.setattr(
        refiner_web.assistant_service,
        "rag_collection_build",
        lambda deps, *, user, payload: ServiceResult(
            {
                "status": "ready",
                "name": str(payload.get("name") or ""),
                "version_id": str(payload.get("_rag_version_id") or ""),
            }
        ),
    )

    task = local_subtasks.submit(
        owner="alice",
        action="rag_collection_build",
        payload={"name": "docs", "_rag_version_id": "version-1"},
        scope_type="rag_collection",
        scope_id="docs",
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
    assert final_task.result["response"]["status"] == "ready"
    assert final_task.result["version_id"] == "version-1"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_subtask_manager_limits_owner_outstanding_tasks_without_blocking_other_users(monkeypatch):
    local_subtasks = refiner_web.SubtaskManager(
        workers=1,
        max_queue=4,
        task_ttl_sec=600,
        max_outstanding_per_owner=1,
    )
    started = threading.Event()
    release = threading.Event()

    def _fake_execute(task):
        started.set()
        release.wait(timeout=1.0)
        return {"response": {"owner": task.owner}}

    monkeypatch.setattr(refiner_web, "_execute_subtask", _fake_execute)

    first = local_subtasks.submit(
        owner="alice",
        action="assistant_requirements",
        payload={"mode": "ask", "prompt": "first"},
        scope_type="user",
        scope_id="alice",
        timeout_sec=30,
    )
    assert started.wait(timeout=1.0)

    with pytest.raises(queue.Full):
        local_subtasks.submit(
            owner="alice",
            action="assistant_requirements",
            payload={"mode": "ask", "prompt": "second"},
            scope_type="user",
            scope_id="alice",
            timeout_sec=30,
        )

    other = local_subtasks.submit(
        owner="bob",
        action="assistant_requirements",
        payload={"mode": "ask", "prompt": "other"},
        scope_type="user",
        scope_id="bob",
        timeout_sec=30,
    )

    release.set()
    completed = set()
    for _ in range(80):
        for task_id in (first.task_id, other.task_id):
            task = local_subtasks.get_task(task_id)
            if task and task.status == "completed":
                completed.add(task_id)
        if completed == {first.task_id, other.task_id}:
            break
        time.sleep(0.02)

    assert completed == {first.task_id, other.task_id}


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_subtask_manager_schedules_other_owner_while_first_owner_is_running(monkeypatch):
    local_subtasks = refiner_web.SubtaskManager(
        workers=1,
        max_queue=6,
        task_ttl_sec=600,
        max_outstanding_per_owner=4,
        max_inflight_per_owner=1,
    )
    first_started = threading.Event()
    two_started = threading.Event()
    release = threading.Event()
    start_lock = threading.Lock()
    started_owners = []

    def _fake_execute(task):
        with start_lock:
            started_owners.append(task.owner)
            if len(started_owners) == 1:
                first_started.set()
            if len(started_owners) >= 2:
                two_started.set()
        release.wait(timeout=1.0)
        return {"response": {"owner": task.owner}}

    monkeypatch.setattr(refiner_web, "_execute_subtask", _fake_execute)

    first = local_subtasks.submit(
        owner="alice",
        action="assistant_requirements",
        payload={"mode": "ask", "prompt": "first"},
        scope_type="user",
        scope_id="alice",
        timeout_sec=30,
    )
    assert first_started.wait(timeout=1.0)

    second = local_subtasks.submit(
        owner="alice",
        action="assistant_requirements",
        payload={"mode": "ask", "prompt": "second"},
        scope_type="user",
        scope_id="alice",
        timeout_sec=30,
    )
    third = local_subtasks.submit(
        owner="bob",
        action="assistant_requirements",
        payload={"mode": "ask", "prompt": "third"},
        scope_type="user",
        scope_id="bob",
        timeout_sec=30,
    )

    worker = threading.Thread(target=local_subtasks._worker_loop, args=(1,), daemon=True)
    worker.start()
    local_subtasks.workers.append(worker)

    assert two_started.wait(timeout=1.0)
    with start_lock:
        assert set(started_owners[:2]) == {"alice", "bob"}

    release.set()
    completed = set()
    for _ in range(80):
        for task_id in (first.task_id, second.task_id, third.task_id):
            task = local_subtasks.get_task(task_id)
            if task and task.status == "completed":
                completed.add(task_id)
        if completed == {first.task_id, second.task_id, third.task_id}:
            break
        time.sleep(0.02)

    assert completed == {first.task_id, second.task_id, third.task_id}
