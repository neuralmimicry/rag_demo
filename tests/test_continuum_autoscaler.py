import threading

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    import refiner_web  # noqa: E402


class _FakeQueue:
    def __init__(self, depth: int):
        self._depth = max(0, int(depth))

    def qsize(self) -> int:
        return self._depth


class _FakeManager:
    def __init__(self, *, queue_depth: int, workers: int, statuses: list[str]):
        self.queue = _FakeQueue(queue_depth)
        self.lock = threading.Lock()
        self.workers = [object() for _ in range(max(1, workers))]
        self.jobs = {f"job-{idx}": type("J", (), {"status": status}) for idx, status in enumerate(statuses)}


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        return self._payload


def _status_payload(desired: int) -> dict:
    return {
        "success": True,
        "message": "ok",
        "data": {
            "namespace": "refiner",
            "deployment": "refiner",
            "observed": True,
            "healthy": True,
            "status": "Running",
            "desired_replicas": desired,
            "ready_replicas": desired,
            "available_replicas": desired,
        },
    }


def _build_autoscaler(manager: "_FakeManager", **kwargs):
    return refiner_web.ContinuumQueueAutoscaler(
        manager,
        enabled=True,
        poll_sec=60.0,
        min_replicas=1,
        max_replicas=8,
        backlog_per_replica=1,
        scale_up_step=1,
        scale_down_step=1,
        idle_sec=120.0,
        cooldown_sec=0.0,
        timeout_sec=2.0,
        **kwargs,
    )


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Autoscaler tests require real Flask runtime")
def test_autoscaler_scales_up_when_queue_is_blocked(monkeypatch):
    manager = _FakeManager(queue_depth=3, workers=1, statuses=["running"])
    autoscaler = _build_autoscaler(manager, max_replicas=6)
    calls: list[int] = []
    state = {"desired": 1}

    monkeypatch.setattr(refiner_web, "_continuum_enabled", lambda: True)

    def _continuum_request(method, path, **kwargs):
        if method == "GET":
            return _FakeResponse(200, _status_payload(state["desired"]))
        if method == "POST":
            target = int(kwargs.get("json_body", {}).get("replicas", 0))
            calls.append(target)
            state["desired"] = target
            return _FakeResponse(200, _status_payload(state["desired"]))
        raise AssertionError("unexpected method")

    monkeypatch.setattr(refiner_web, "_continuum_request", _continuum_request)
    autoscaler.evaluate_once()

    assert calls == [4]
    assert autoscaler.status()["last_decision"] == "scale_up_queue_backlog"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Autoscaler tests require real Flask runtime")
def test_autoscaler_does_not_scale_when_capacity_is_available(monkeypatch):
    manager = _FakeManager(queue_depth=1, workers=4, statuses=["running"])
    autoscaler = _build_autoscaler(manager)
    post_calls: list[int] = []

    monkeypatch.setattr(refiner_web, "_continuum_enabled", lambda: True)

    def _continuum_request(method, path, **kwargs):
        if method == "GET":
            return _FakeResponse(200, _status_payload(1))
        if method == "POST":
            post_calls.append(int(kwargs.get("json_body", {}).get("replicas", 0)))
            return _FakeResponse(200, _status_payload(1))
        raise AssertionError("unexpected method")

    monkeypatch.setattr(refiner_web, "_continuum_request", _continuum_request)
    autoscaler.evaluate_once()

    assert post_calls == []
    assert autoscaler.status()["last_decision"] == "steady"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Autoscaler tests require real Flask runtime")
def test_autoscaler_respects_cooldown(monkeypatch):
    manager = _FakeManager(queue_depth=2, workers=1, statuses=["running"])
    autoscaler = _build_autoscaler(manager, cooldown_sec=3600.0)
    post_calls: list[int] = []
    state = {"desired": 1}

    monkeypatch.setattr(refiner_web, "_continuum_enabled", lambda: True)

    def _continuum_request(method, path, **kwargs):
        if method == "GET":
            return _FakeResponse(200, _status_payload(state["desired"]))
        if method == "POST":
            target = int(kwargs.get("json_body", {}).get("replicas", 0))
            post_calls.append(target)
            state["desired"] = target
            return _FakeResponse(200, _status_payload(target))
        raise AssertionError("unexpected method")

    monkeypatch.setattr(refiner_web, "_continuum_request", _continuum_request)
    autoscaler.evaluate_once()
    autoscaler.evaluate_once()

    assert len(post_calls) == 1
    assert autoscaler.status()["last_decision"] == "cooldown"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Autoscaler tests require real Flask runtime")
def test_autoscaler_scales_down_after_idle(monkeypatch):
    manager = _FakeManager(queue_depth=0, workers=2, statuses=[])
    autoscaler = _build_autoscaler(manager, idle_sec=0.0, cooldown_sec=0.0)
    post_calls: list[int] = []
    state = {"desired": 3}

    monkeypatch.setattr(refiner_web, "_continuum_enabled", lambda: True)

    def _continuum_request(method, path, **kwargs):
        if method == "GET":
            return _FakeResponse(200, _status_payload(state["desired"]))
        if method == "POST":
            target = int(kwargs.get("json_body", {}).get("replicas", 0))
            post_calls.append(target)
            state["desired"] = target
            return _FakeResponse(200, _status_payload(target))
        raise AssertionError("unexpected method")

    monkeypatch.setattr(refiner_web, "_continuum_request", _continuum_request)
    autoscaler.evaluate_once()

    assert post_calls == [2]
    assert autoscaler.status()["last_decision"] == "scale_down_idle"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Autoscaler tests require real Flask runtime")
def test_autoscaler_handles_continuum_status_errors(monkeypatch):
    manager = _FakeManager(queue_depth=2, workers=1, statuses=["running"])
    autoscaler = _build_autoscaler(manager)

    monkeypatch.setattr(refiner_web, "_continuum_enabled", lambda: True)
    monkeypatch.setattr(refiner_web, "_continuum_request", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    autoscaler.evaluate_once()
    status = autoscaler.status()

    assert status["last_decision"] == "status_error"
    assert "boom" in (status["last_error"] or "")
