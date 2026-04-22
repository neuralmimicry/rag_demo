import queue
import threading
import time

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


def _setup_workspace_auth(monkeypatch, job):
    monkeypatch.setattr(refiner_web, "_current_user", lambda: "integration_tester")
    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)
    monkeypatch.setattr(refiner_web.manager, "get_job", lambda *_args, **_kwargs: job)
    monkeypatch.setattr(refiner_web, "_can_view_job", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(refiner_web, "_can_manage_job", lambda *_args, **_kwargs: True)


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_workspace_create_enqueues_background_task(monkeypatch):
    job = refiner_web.Job(job_id="job-task-create", payload={}, owner="integration_tester")
    job.workspace_env = {}

    _setup_workspace_auth(monkeypatch, job)
    monkeypatch.setattr(refiner_web, "_continuum_ready", lambda: True)
    monkeypatch.setattr(refiner_web, "_continuum_enabled", lambda: True)
    monkeypatch.setattr(refiner_web, "CONTINUUM_IDE_URL_TEMPLATE", "https://ide.local/{vm_id}")
    monkeypatch.setattr(refiner_web, "CONTINUUM_PREVIEW_URL_TEMPLATE", "https://preview.local/{vm_id}")
    monkeypatch.setattr(
        refiner_web,
        "_continuum_request",
        lambda method, path, **_kwargs: _FakeResponse(
            200,
            {
                "success": True,
                "message": "created",
                "data": {
                    "id": "vm-123",
                    "status": "ready",
                    "name": "job-task-create",
                    "region": "gb-mids",
                    "sku": "standard-a2",
                },
            },
        ),
    )

    action_manager = refiner_web.JobActionManager(workers=1, max_queue=4, task_ttl_sec=600)
    monkeypatch.setattr(refiner_web, "job_action_manager", action_manager)

    with refiner_web.app.test_client() as client:
        response = client.post(f"/api/jobs/{job.job_id}/workspace", json={"action": "create"})
        assert response.status_code == 202
        data = response.get_json()
        task_id = data.get("task", {}).get("task_id")
        assert task_id

        final_status = None
        for _ in range(60):
            detail = client.get(f"/api/jobs/{job.job_id}/tasks/{task_id}")
            assert detail.status_code == 200
            final_status = detail.get_json().get("task", {}).get("status")
            if final_status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)

    assert final_status == "completed"
    assert job.workspace_env.get("provider") == "continuum"
    assert job.workspace_env.get("vm_id") == "vm-123"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_workspace_create_returns_capacity_error_when_queue_full(monkeypatch):
    job = refiner_web.Job(job_id="job-task-full", payload={}, owner="integration_tester")
    job.workspace_env = {}

    _setup_workspace_auth(monkeypatch, job)
    monkeypatch.setattr(refiner_web, "_continuum_ready", lambda: True)
    monkeypatch.setattr(refiner_web, "_continuum_enabled", lambda: True)
    monkeypatch.setattr(
        refiner_web.job_action_manager,
        "submit",
        lambda **_kwargs: (_ for _ in ()).throw(queue.Full()),
    )

    with refiner_web.app.test_client() as client:
        response = client.post(f"/api/jobs/{job.job_id}/workspace", json={"action": "create"})

    assert response.status_code == 503
    data = response.get_json()
    assert data.get("error") == "job_action_capacity_unavailable"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_workspace_task_cancel_endpoint(monkeypatch):
    job = refiner_web.Job(job_id="job-task-cancel", payload={}, owner="integration_tester")
    job.workspace_env = {}

    _setup_workspace_auth(monkeypatch, job)
    monkeypatch.setattr(refiner_web, "_continuum_ready", lambda: True)
    monkeypatch.setattr(refiner_web, "_continuum_enabled", lambda: True)

    def _slow_create(method, path, **_kwargs):
        time.sleep(0.2)
        return _FakeResponse(
            200,
            {
                "success": True,
                "message": "created",
                "data": {"id": "vm-999", "status": "ready"},
            },
        )

    monkeypatch.setattr(refiner_web, "_continuum_request", _slow_create)
    action_manager = refiner_web.JobActionManager(workers=1, max_queue=4, task_ttl_sec=600)
    monkeypatch.setattr(refiner_web, "job_action_manager", action_manager)

    with refiner_web.app.test_client() as client:
        create_response = client.post(f"/api/jobs/{job.job_id}/workspace", json={"action": "create"})
        assert create_response.status_code == 202
        task_id = create_response.get_json().get("task", {}).get("task_id")
        assert task_id

        cancel_response = client.post(f"/api/jobs/{job.job_id}/tasks/{task_id}/cancel")
        assert cancel_response.status_code == 200

        final_status = None
        for _ in range(60):
            detail = client.get(f"/api/jobs/{job.job_id}/tasks/{task_id}")
            assert detail.status_code == 200
            final_status = detail.get_json().get("task", {}).get("status")
            if final_status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)

    assert final_status == "cancelled"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_action_manager_limits_owner_outstanding_tasks_without_blocking_other_users(monkeypatch):
    action_manager = refiner_web.JobActionManager(
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
        return {"owner": task.owner, "action": task.action}

    monkeypatch.setattr(refiner_web, "_execute_job_action_task", _fake_execute)

    first = action_manager.submit(job_id="job-1", owner="alice", action="workspace_refresh", payload={})
    assert started.wait(timeout=1.0)

    with pytest.raises(queue.Full):
        action_manager.submit(job_id="job-1", owner="alice", action="workspace_create", payload={})

    other = action_manager.submit(job_id="job-2", owner="bob", action="workspace_refresh", payload={})

    release.set()
    completed = set()
    for _ in range(80):
        for task_id in (first.task_id, other.task_id):
            task = action_manager.get_task(task_id)
            if task and task.status == "completed":
                completed.add(task_id)
        if completed == {first.task_id, other.task_id}:
            break
        time.sleep(0.02)

    assert completed == {first.task_id, other.task_id}


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_action_manager_schedules_other_owner_while_first_owner_is_running(monkeypatch):
    action_manager = refiner_web.JobActionManager(
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
        return {"owner": task.owner, "action": task.action}

    monkeypatch.setattr(refiner_web, "_execute_job_action_task", _fake_execute)

    first = action_manager.submit(job_id="job-1", owner="alice", action="workspace_refresh", payload={})
    assert first_started.wait(timeout=1.0)

    second = action_manager.submit(job_id="job-2", owner="alice", action="workspace_create", payload={})
    third = action_manager.submit(job_id="job-3", owner="bob", action="workspace_refresh", payload={})

    worker = threading.Thread(target=action_manager._worker_loop, args=(1,), daemon=True)
    worker.start()
    action_manager.workers.append(worker)

    assert two_started.wait(timeout=1.0)
    with start_lock:
        assert set(started_owners[:2]) == {"alice", "bob"}

    release.set()
    completed = set()
    for _ in range(80):
        for task_id in (first.task_id, second.task_id, third.task_id):
            task = action_manager.get_task(task_id)
            if task and task.status == "completed":
                completed.add(task_id)
        if completed == {first.task_id, second.task_id, third.task_id}:
            break
        time.sleep(0.02)

    assert completed == {first.task_id, second.task_id, third.task_id}
