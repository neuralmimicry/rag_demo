from __future__ import annotations

import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from refiner.runtime.continuum_autoscaler import (
    ContinuumQueueAutoscaler,
    continuum_cluster_snapshot,
    workers_telemetry_payload,
)


class _FakeQueue:
    def __init__(self, depth: int):
        self._depth = max(0, int(depth))

    def qsize(self) -> int:
        return self._depth


@dataclass
class _FakeLogger:
    warnings: List[str]

    def warning(self, message: str, *args: Any) -> None:
        if args:
            message = message % args
        self.warnings.append(message)


class _FakeManager:
    def __init__(self, *, queue_depth: int, workers: int, statuses: List[str]):
        self.queue = _FakeQueue(queue_depth)
        self.lock = threading.Lock()
        self.workers = [object() for _ in range(max(1, workers))]
        self.jobs = {
            f"job-{index}": SimpleNamespace(status=status, owner=f"owner-{index}")
            for index, status in enumerate(statuses)
        }

    def list_jobs(self) -> List[Any]:
        return list(self.jobs.values())


def _queue_snapshot_factory(snapshot: Dict[str, Any]):
    def _snapshot(manager: Any, *, top_limit: int = 3, include_owner_lists: bool = False) -> Dict[str, Any]:
        return dict(snapshot)

    return _snapshot


def _status_data(desired: int, *, ready: Optional[int] = None, available: Optional[int] = None, status: str = "Running") -> Dict[str, Any]:
    return {
        "namespace": "refiner",
        "deployment": "refiner",
        "observed": True,
        "healthy": True,
        "desired_replicas": desired,
        "ready_replicas": desired if ready is None else ready,
        "available_replicas": desired if available is None else available,
        "status": status,
    }


def _build_autoscaler(
    snapshot: Dict[str, Any],
    continuum_request,
    *,
    enabled: bool = True,
    min_replicas: int = 1,
    max_replicas: int = 8,
    idle_sec: float = 120.0,
    cooldown_sec: float = 0.0,
) -> ContinuumQueueAutoscaler:
    return ContinuumQueueAutoscaler(
        _FakeManager(queue_depth=snapshot.get("queue_depth", 0), workers=snapshot.get("workers", 1), statuses=[]),
        enabled=enabled,
        poll_sec=60.0,
        min_replicas=min_replicas,
        max_replicas=max_replicas,
        backlog_per_replica=1,
        scale_up_step=1,
        scale_down_step=1,
        idle_sec=idle_sec,
        cooldown_sec=cooldown_sec,
        timeout_sec=5.0,
        namespace="refiner",
        deployment="refiner",
        history_max=20,
        continuum_enabled=lambda: True,
        continuum_request=continuum_request,
        continuum_json_payload=lambda response, *, operation: response,
        friendly_continuum_error=lambda message: f"friendly:{message}" if message else "friendly:none",
        now_iso=lambda: "2026-04-30T12:00:00Z",
        logger=_FakeLogger([]),
        job_queue_snapshot=_queue_snapshot_factory(snapshot),
    )


def test_autoscaler_core_scales_up_when_queue_backlog_exceeds_capacity():
    snapshot = {"queue_depth": 3, "queued": 3, "running": 1, "paused": 0, "workers": 1}
    calls: List[int] = []
    state = {"desired": 1}

    def _continuum_request(method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        if method == "GET":
            assert path.startswith("/k8s/refiner/status")
            return _status_data(state["desired"])
        if method == "POST":
            target = int(kwargs["json_body"]["replicas"])
            calls.append(target)
            state["desired"] = target
            return _status_data(target)
        raise AssertionError("unexpected method")

    autoscaler = _build_autoscaler(snapshot, _continuum_request, max_replicas=6)
    autoscaler.evaluate_once()

    assert calls == [4]
    status = autoscaler.status()
    assert status["last_decision"] == "scale_up_queue_backlog"
    assert status["remote"]["desired_replicas"] == 4
    assert status["workers"]["coming_online_workers"] == 0


def test_autoscaler_core_scales_down_after_idle_period():
    snapshot = {"queue_depth": 0, "queued": 0, "running": 0, "paused": 0, "workers": 2}
    calls: List[int] = []
    state = {"desired": 3}

    def _continuum_request(method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        if method == "GET":
            return _status_data(state["desired"], ready=3, available=3)
        if method == "POST":
            target = int(kwargs["json_body"]["replicas"])
            calls.append(target)
            state["desired"] = target
            return _status_data(target, ready=target, available=target)
        raise AssertionError("unexpected method")

    autoscaler = _build_autoscaler(snapshot, _continuum_request, idle_sec=0.0)
    autoscaler.evaluate_once()

    assert calls == [2]
    assert autoscaler.status()["last_decision"] == "scale_down_idle"


def test_autoscaler_core_marks_status_errors_as_degraded():
    snapshot = {"queue_depth": 2, "queued": 2, "running": 1, "paused": 0, "workers": 1}

    def _continuum_request(method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("boom")

    autoscaler = _build_autoscaler(snapshot, _continuum_request)
    autoscaler.evaluate_once()

    status = autoscaler.status()
    assert status["last_decision"] == "status_error"
    assert status["continuum"]["degraded"] is True
    assert status["continuum"]["consecutive_failures"] == 1
    assert status["continuum"]["message"] == "friendly:boom"


def test_continuum_cluster_snapshot_prefers_refiner_local_cluster():
    clusters = {
        "clusters": [
            {
                "id": "k8s-eu",
                "name": "shared-eu",
                "region": "eu-west",
                "status": "Running",
                "total_nodes": "5",
                "ready_nodes": "5",
            },
            {
                "id": "k8s-local-refiner",
                "name": "refiner-local",
                "region": "gb-mids",
                "status": "Running",
                "total_nodes": "3",
                "ready_nodes": "2",
                "refiner": {
                    "observed": True,
                    "healthy": False,
                    "namespace": "refiner",
                    "deployment": "refiner",
                    "desired_replicas": "4",
                    "ready_replicas": "3",
                    "available_replicas": "2",
                },
            },
        ]
    }

    snapshot = continuum_cluster_snapshot(
        timeout_sec=5.0,
        continuum_enabled=lambda: True,
        continuum_request=lambda method, path, **kwargs: clusters,
        continuum_json_payload=lambda response, *, operation: response,
        safe_int=lambda value, default=0: int(value),
    )

    assert snapshot == {
        "id": "k8s-local-refiner",
        "name": "refiner-local",
        "region": "gb-mids",
        "status": "Running",
        "total_nodes": 3,
        "ready_nodes": 2,
        "refiner": {
            "observed": True,
            "healthy": False,
            "namespace": "refiner",
            "deployment": "refiner",
            "desired_replicas": 4,
            "ready_replicas": 3,
            "available_replicas": 2,
        },
    }


def test_workers_telemetry_payload_surfaces_cluster_errors():
    autoscaler = _build_autoscaler(
        {"queue_depth": 1, "queued": 1, "running": 1, "paused": 0, "workers": 1},
        lambda method, path, **kwargs: _status_data(1),
    )
    autoscaler.evaluate_once()
    warnings: List[str] = []
    logger = _FakeLogger(warnings)

    payload = workers_telemetry_payload(
        autoscaler=autoscaler,
        limit=5,
        refresh=False,
        include_cluster=True,
        continuum_enabled=lambda: True,
        friendly_continuum_error=lambda message: f"friendly:{message}" if message else "friendly:none",
        continuum_cluster_snapshot=lambda timeout: {"error": "cluster unavailable"},
        now_iso=lambda: "2026-04-30T12:00:00Z",
        logger=logger,
        serialise_job_queue_snapshot=lambda snapshot, include_owner_lists=False: dict(snapshot or {}),
    )

    assert payload["degraded"] is True
    assert payload["ok"] is False
    assert payload["warnings"] == ["friendly:cluster unavailable"]
    assert payload["message"] == "friendly:cluster unavailable"
    assert payload["continuum_cluster"] == {"error": "cluster unavailable"}
