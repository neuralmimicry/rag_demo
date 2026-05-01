"""Continuum autoscaling helpers extracted from the Refiner runtime."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional
from urllib.parse import urlencode


class ContinuumQueueAutoscaler:
    """Scale Refiner workers through Continuum when queue pressure changes."""

    def __init__(
        self,
        manager: Any,
        *,
        enabled: bool,
        poll_sec: float,
        min_replicas: int,
        max_replicas: int,
        backlog_per_replica: int,
        scale_up_step: int,
        scale_down_step: int,
        idle_sec: float,
        cooldown_sec: float,
        timeout_sec: float,
        namespace: str,
        deployment: str,
        history_max: int,
        continuum_enabled: Callable[[], bool],
        continuum_request: Callable[..., Any],
        continuum_json_payload: Callable[..., Dict[str, Any]],
        friendly_continuum_error: Callable[[Optional[str]], str],
        now_iso: Callable[[], str],
        logger: Any,
        job_queue_snapshot: Callable[..., Dict[str, Any]],
    ):
        self.manager = manager
        self.enabled = bool(enabled)
        self.poll_sec = max(1.0, float(poll_sec))
        self.min_replicas = max(0, int(min_replicas))
        self.max_replicas = max(self.min_replicas, int(max_replicas))
        self.backlog_per_replica = max(1, int(backlog_per_replica))
        self.scale_up_step = max(1, int(scale_up_step))
        self.scale_down_step = max(1, int(scale_down_step))
        self.idle_sec = max(0.0, float(idle_sec))
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.namespace = (namespace or "refiner").strip() or "refiner"
        self.deployment = (deployment or "refiner").strip() or "refiner"
        self._continuum_enabled = continuum_enabled
        self._continuum_request = continuum_request
        self._continuum_json_payload = continuum_json_payload
        self._friendly_continuum_error = friendly_continuum_error
        self._now_iso = now_iso
        self._logger = logger
        self._job_queue_snapshot = job_queue_snapshot
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._idle_since: Optional[float] = None
        self._last_scale_at: Optional[str] = None
        self._last_scale_ts: float = 0.0
        self._last_decision: str = "init"
        self._last_error: Optional[str] = None
        self._last_snapshot: Dict[str, Any] = {"queue_depth": 0, "queued": 0, "running": 0, "paused": 0, "workers": 0}
        self._last_remote: Dict[str, Any] = {}
        self._history: Deque[Dict[str, Any]] = deque(maxlen=max(120, int(history_max)))
        self._continuum_failures: int = 0
        self._continuum_last_success_at: Optional[str] = None
        self._continuum_last_failure_at: Optional[str] = None

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _record_continuum_success(self) -> None:
        with self._lock:
            self._continuum_failures = 0
            self._continuum_last_success_at = self._now_iso()

    def _record_continuum_failure(self) -> None:
        with self._lock:
            self._continuum_failures += 1
            self._continuum_last_failure_at = self._now_iso()

    def start(self) -> None:
        """Start the background polling loop when the feature is enabled."""

        if not self.enabled or not self._continuum_enabled():
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="continuum-queue-autoscaler", daemon=True)
        self._thread.start()
        self.notify_queue_change()

    def stop(self, timeout: float = 1.0) -> None:
        """Stop the background loop and join briefly."""

        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, float(timeout)))

    def notify_queue_change(self) -> None:
        """Wake the autoscaler after queue state changes."""

        self._wake_event.set()

    def _worker_metrics(self, snapshot: Dict[str, Any], remote: Dict[str, Any], decision: str) -> Dict[str, Any]:
        workers_per_replica = max(1, self._safe_int(snapshot.get("workers"), 1))
        running_jobs = max(0, self._safe_int(snapshot.get("running"), 0))
        queued_jobs = max(0, self._safe_int(snapshot.get("queued"), 0))
        queue_depth = max(0, self._safe_int(snapshot.get("queue_depth"), queued_jobs))
        queued_owner_count = max(0, self._safe_int(snapshot.get("queued_owner_count"), 0))
        running_owner_count = max(0, self._safe_int(snapshot.get("running_owner_count"), 0))
        paused_owner_count = max(0, self._safe_int(snapshot.get("paused_owner_count"), 0))
        active_owner_count = max(0, self._safe_int(snapshot.get("active_owner_count"), 0))
        desired_replicas = max(self.min_replicas, self._safe_int(remote.get("desired_replicas"), self.min_replicas))
        ready_replicas = max(0, self._safe_int(remote.get("ready_replicas"), 0))
        available_replicas = max(0, self._safe_int(remote.get("available_replicas"), 0))
        max_workers_capacity = max(self.min_replicas, self.max_replicas) * workers_per_replica
        target_workers = desired_replicas * workers_per_replica
        online_workers = ready_replicas * workers_per_replica
        available_workers = max(0, online_workers - running_jobs)
        in_use_workers = min(running_jobs, online_workers)
        scaling_gap = max(0, target_workers - online_workers)
        status_raw = str(remote.get("status") or "Unknown")
        status_normalized = status_raw.strip().lower()
        degraded = status_normalized in {"degraded", "unavailable", "failed", "error"}
        scale_up_active = decision.startswith("scale_up")
        failed_workers = scaling_gap if degraded and not scale_up_active else 0
        coming_online_workers = max(0, scaling_gap - failed_workers)
        queue_pressure = 0.0
        if online_workers > 0:
            queue_pressure = min(500.0, round((queue_depth / float(online_workers)) * 100.0, 2))
        utilization_pct = 0.0
        if max_workers_capacity > 0:
            utilization_pct = min(100.0, round((in_use_workers / float(max_workers_capacity)) * 100.0, 2))
        queued_owner_skew_ratio = min(1.0, max(0.0, round(self._safe_float(snapshot.get("queued_owner_skew_ratio"), 0.0), 3)))
        running_owner_skew_ratio = min(
            1.0,
            max(0.0, round(self._safe_float(snapshot.get("running_owner_skew_ratio"), 0.0), 3)),
        )
        active_owner_skew_ratio = min(
            1.0,
            max(0.0, round(self._safe_float(snapshot.get("active_owner_skew_ratio"), 0.0), 3)),
        )
        return {
            "workers_per_replica": workers_per_replica,
            "max_workers_capacity": max_workers_capacity,
            "target_workers": target_workers,
            "online_workers": online_workers,
            "available_workers": available_workers,
            "in_use_workers": in_use_workers,
            "coming_online_workers": coming_online_workers,
            "failed_workers": failed_workers,
            "queue_depth": queue_depth,
            "queued_jobs": queued_jobs,
            "running_jobs": running_jobs,
            "desired_replicas": desired_replicas,
            "ready_replicas": ready_replicas,
            "available_replicas": available_replicas,
            "status": status_raw or "Unknown",
            "utilization_pct": utilization_pct,
            "queue_pressure_pct": queue_pressure,
            "queued_owner_count": queued_owner_count,
            "running_owner_count": running_owner_count,
            "paused_owner_count": paused_owner_count,
            "active_owner_count": active_owner_count,
            "queued_owner_skew_ratio": queued_owner_skew_ratio,
            "running_owner_skew_ratio": running_owner_skew_ratio,
            "active_owner_skew_ratio": active_owner_skew_ratio,
            "single_owner_queue_pressure": bool(snapshot.get("single_owner_queue_pressure")),
        }

    def history(self, limit: int = 120) -> List[Dict[str, Any]]:
        """Return the most recent autoscaler decisions."""

        with self._lock:
            items = list(self._history)
        if limit <= 0:
            return []
        return items[-limit:]

    def status(self) -> Dict[str, Any]:
        """Return the latest autoscaler state snapshot."""

        with self._lock:
            snapshot = dict(self._last_snapshot)
            remote = dict(self._last_remote)
            decision = self._last_decision
            error = self._last_error
            last_scale_at = self._last_scale_at
            continuum_failures = int(self._continuum_failures or 0)
            continuum_last_success_at = self._continuum_last_success_at
            continuum_last_failure_at = self._continuum_last_failure_at
        worker_metrics = self._worker_metrics(snapshot, remote, decision)
        degraded = continuum_failures > 0 or (decision.endswith("_error") if isinstance(decision, str) else False)
        message = self._friendly_continuum_error(error) if degraded else ""
        return {
            "enabled": self.enabled,
            "continuum_configured": self._continuum_enabled(),
            "running": bool(self._thread and self._thread.is_alive()),
            "poll_sec": self.poll_sec,
            "cooldown_sec": self.cooldown_sec,
            "idle_sec": self.idle_sec,
            "min_replicas": self.min_replicas,
            "max_replicas": self.max_replicas,
            "backlog_per_replica": self.backlog_per_replica,
            "namespace": self.namespace,
            "deployment": self.deployment,
            "last_decision": decision,
            "last_scale_at": last_scale_at,
            "last_error": error,
            "snapshot": snapshot,
            "remote": remote,
            "workers": worker_metrics,
            "continuum": {
                "degraded": degraded,
                "consecutive_failures": continuum_failures,
                "last_success_at": continuum_last_success_at,
                "last_failure_at": continuum_last_failure_at,
                "message": message,
            },
        }

    def evaluate_once(self) -> None:
        """Evaluate current queue pressure and optionally request a scale change."""

        if not self.enabled:
            self._update_state(decision="disabled", error=None)
            return
        if not self._continuum_enabled():
            self._update_state(decision="continuum_unconfigured", error=None)
            return
        snapshot = self._queue_snapshot()
        now_ts = time.time()
        active = snapshot["queue_depth"] > 0 or snapshot["running"] > 0 or snapshot["paused"] > 0
        if active:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = now_ts

        try:
            remote = self._fetch_remote_status()
        except Exception as exc:
            self._record_continuum_failure()
            message = str(exc)
            self._logger.warning("Continuum autoscaler status fetch failed: %s", message)
            self._update_state(snapshot=snapshot, decision="status_error", error=message)
            return
        self._record_continuum_success()

        current_replicas = max(0, self._safe_int(remote.get("desired_replicas"), self.min_replicas))

        if current_replicas < self.min_replicas:
            target = self.min_replicas
            if target > current_replicas and not self._cooldown_active(now_ts):
                if self._apply_scale(target, reason="enforce_min_replicas", snapshot=snapshot):
                    return

        should_scale_up = snapshot["queue_depth"] > 0 and snapshot["running"] >= snapshot["workers"]
        if should_scale_up and current_replicas < self.max_replicas:
            if self._cooldown_active(now_ts):
                self._update_state(snapshot=snapshot, remote=remote, decision="cooldown", error=None)
                return
            queued_units = int(math.ceil(snapshot["queue_depth"] / float(self.backlog_per_replica)))
            increment = max(self.scale_up_step, queued_units)
            target = min(self.max_replicas, current_replicas + increment)
            if target > current_replicas and self._apply_scale(target, reason="scale_up_queue_backlog", snapshot=snapshot):
                return

        idle_for = (now_ts - self._idle_since) if self._idle_since is not None else 0.0
        should_scale_down = (
            self._idle_since is not None
            and idle_for >= self.idle_sec
            and current_replicas > self.min_replicas
        )
        if should_scale_down:
            if self._cooldown_active(now_ts):
                self._update_state(snapshot=snapshot, remote=remote, decision="cooldown", error=None)
                return
            target = max(self.min_replicas, current_replicas - self.scale_down_step)
            if target < current_replicas and self._apply_scale(target, reason="scale_down_idle", snapshot=snapshot):
                return

        decision = "steady"
        if should_scale_up and current_replicas >= self.max_replicas:
            decision = "at_max_replicas"
        elif should_scale_down and current_replicas <= self.min_replicas:
            decision = "at_min_replicas"
        self._update_state(snapshot=snapshot, remote=remote, decision=decision, error=None)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.evaluate_once()
            except Exception as exc:
                self._logger.warning("Continuum autoscaler loop error: %s", exc)
                self._update_state(decision="loop_error", error=str(exc))
            self._wake_event.wait(timeout=self.poll_sec)
            self._wake_event.clear()

    def _queue_snapshot(self) -> Dict[str, Any]:
        return self._job_queue_snapshot(self.manager, top_limit=3, include_owner_lists=False)

    def _fetch_remote_status(self) -> Dict[str, Any]:
        params = urlencode({"namespace": self.namespace, "deployment": self.deployment})
        path = "/k8s/refiner/status"
        if params:
            path = f"{path}?{params}"
        response = self._continuum_request("GET", path, timeout_sec=self.timeout_sec)
        data = self._continuum_json_payload(response, operation="Continuum refiner status")
        return {
            "namespace": data.get("namespace") or self.namespace,
            "deployment": data.get("deployment") or self.deployment,
            "observed": bool(data.get("observed")),
            "healthy": bool(data.get("healthy")),
            "desired_replicas": max(0, self._safe_int(data.get("desired_replicas"), self.min_replicas)),
            "ready_replicas": max(0, self._safe_int(data.get("ready_replicas"), 0)),
            "available_replicas": max(0, self._safe_int(data.get("available_replicas"), 0)),
            "status": (data.get("status") or "").strip() or "Unknown",
        }

    def _request_scale(self, replicas: int) -> Dict[str, Any]:
        target = max(self.min_replicas, min(self.max_replicas, int(replicas)))
        response = self._continuum_request(
            "POST",
            "/k8s/refiner/scale",
            json_body={"namespace": self.namespace, "deployment": self.deployment, "replicas": target},
            timeout_sec=self.timeout_sec,
        )
        data = self._continuum_json_payload(response, operation="Continuum refiner scale")
        return {
            "namespace": data.get("namespace") or self.namespace,
            "deployment": data.get("deployment") or self.deployment,
            "observed": bool(data.get("observed", True)),
            "healthy": bool(data.get("healthy", False)),
            "desired_replicas": max(0, self._safe_int(data.get("desired_replicas"), target)),
            "ready_replicas": max(0, self._safe_int(data.get("ready_replicas"), 0)),
            "available_replicas": max(0, self._safe_int(data.get("available_replicas"), 0)),
            "status": (data.get("status") or "").strip() or "Unknown",
        }

    def _cooldown_active(self, now_ts: float) -> bool:
        if self.cooldown_sec <= 0:
            return False
        return self._last_scale_ts > 0 and (now_ts - self._last_scale_ts) < self.cooldown_sec

    def _apply_scale(self, target: int, *, reason: str, snapshot: Dict[str, Any]) -> bool:
        try:
            remote = self._request_scale(target)
        except Exception as exc:
            self._record_continuum_failure()
            message = str(exc)
            self._logger.warning("Continuum autoscaler scale request failed: %s", message)
            self._update_state(snapshot=snapshot, decision=f"{reason}_error", error=message)
            return False
        self._record_continuum_success()
        self._last_scale_ts = time.time()
        self._last_scale_at = self._now_iso()
        self._update_state(snapshot=snapshot, remote=remote, decision=reason, error=None)
        return True

    def _update_state(
        self,
        *,
        snapshot: Optional[Dict[str, Any]] = None,
        remote: Optional[Dict[str, Any]] = None,
        decision: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            if snapshot is not None:
                self._last_snapshot = dict(snapshot)
            if remote is not None:
                self._last_remote = dict(remote)
            if decision:
                self._last_decision = decision
            self._last_error = error
            metrics = self._worker_metrics(self._last_snapshot, self._last_remote, self._last_decision)
            self._history.append(
                {
                    "timestamp_ms": int(time.time() * 1000),
                    "captured_at": self._now_iso(),
                    "decision": self._last_decision,
                    "error": self._last_error,
                    "workers": metrics,
                }
            )


def continuum_cluster_snapshot(
    *,
    timeout_sec: float,
    continuum_enabled: Callable[[], bool],
    continuum_request: Callable[..., Any],
    continuum_json_payload: Callable[..., Dict[str, Any]],
    safe_int: Callable[[Any, int], int],
) -> Optional[Dict[str, Any]]:
    """Return the current Continuum cluster view used in worker telemetry."""

    if not continuum_enabled():
        return None
    try:
        response = continuum_request("GET", "/k8s/list", timeout_sec=timeout_sec, retries=1)
        data = continuum_json_payload(response, operation="Continuum k8s list")
    except Exception as exc:
        return {"error": str(exc)}
    clusters = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(clusters, list):
        return {"error": "missing cluster data"}
    selected: Optional[Dict[str, Any]] = None
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("id") or "").strip().lower()
        cluster_name = str(cluster.get("name") or "").strip().lower()
        if cluster_id == "k8s-local-refiner" or cluster_name == "refiner-local":
            selected = cluster
            break
    if not selected:
        for cluster in clusters:
            if isinstance(cluster, dict):
                selected = cluster
                break
    if not selected:
        return {"error": "no clusters reported"}
    payload: Dict[str, Any] = {
        "id": selected.get("id"),
        "name": selected.get("name"),
        "region": selected.get("region"),
        "status": selected.get("status"),
        "total_nodes": safe_int(selected.get("total_nodes"), 0),
        "ready_nodes": safe_int(selected.get("ready_nodes"), 0),
    }
    refiner = selected.get("refiner")
    if isinstance(refiner, dict):
        payload["refiner"] = {
            "observed": bool(refiner.get("observed")),
            "healthy": bool(refiner.get("healthy")),
            "namespace": refiner.get("namespace"),
            "deployment": refiner.get("deployment"),
            "desired_replicas": safe_int(refiner.get("desired_replicas"), 0),
            "ready_replicas": safe_int(refiner.get("ready_replicas"), 0),
            "available_replicas": safe_int(refiner.get("available_replicas"), 0),
        }
    return payload


def workers_telemetry_payload(
    *,
    autoscaler: Optional[ContinuumQueueAutoscaler],
    limit: int = 180,
    refresh: bool = False,
    include_cluster: bool = False,
    continuum_enabled: Callable[[], bool],
    friendly_continuum_error: Callable[[Optional[str]], str],
    continuum_cluster_snapshot: Callable[[float], Optional[Dict[str, Any]]],
    now_iso: Callable[[], str],
    logger: Any,
    serialise_job_queue_snapshot: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the operator-facing worker telemetry payload."""

    if not autoscaler:
        return {
            "ok": True,
            "degraded": False,
            "message": "",
            "warnings": [],
            "autoscaler": {"enabled": False, "running": False, "continuum_configured": continuum_enabled()},
            "summary": {},
            "job_queue": {},
            "history": [],
        }

    warnings: List[str] = []
    if refresh:
        try:
            autoscaler.evaluate_once()
        except Exception as exc:
            logger.warning("Workers telemetry refresh failed: %s", exc)
            warnings.append(friendly_continuum_error(str(exc)))
    try:
        status = autoscaler.status()
    except Exception as exc:
        logger.warning("Workers telemetry status snapshot failed: %s", exc)
        status = {
            "enabled": bool(getattr(autoscaler, "enabled", False)),
            "continuum_configured": continuum_enabled(),
            "running": bool(getattr(autoscaler, "_thread", None)),
            "continuum": {
                "degraded": True,
                "consecutive_failures": 0,
                "last_success_at": None,
                "last_failure_at": now_iso(),
                "message": friendly_continuum_error(str(exc)),
            },
        }
        warnings.append(friendly_continuum_error(str(exc)))
    try:
        timeline = autoscaler.history(limit=max(1, limit))
    except Exception as exc:
        logger.warning("Workers telemetry history snapshot failed: %s", exc)
        timeline = []
        warnings.append("Workers timeline is temporarily unavailable.")

    continuum_state = status.get("continuum") if isinstance(status, dict) else {}
    degraded = bool(continuum_state.get("degraded")) if isinstance(continuum_state, dict) else False
    continuum_message = ""
    if isinstance(continuum_state, dict):
        continuum_message = str(continuum_state.get("message") or "").strip()
    if continuum_message and continuum_message not in warnings:
        warnings.append(continuum_message)

    payload: Dict[str, Any] = {
        "ok": not degraded,
        "degraded": degraded,
        "message": continuum_message,
        "warnings": warnings,
        "autoscaler": status,
        "summary": status.get("workers") or {},
        "job_queue": serialise_job_queue_snapshot(status.get("snapshot"), include_owner_lists=False),
        "history": timeline,
    }
    if include_cluster:
        cluster = continuum_cluster_snapshot(autoscaler.timeout_sec)
        payload["continuum_cluster"] = cluster
        if isinstance(cluster, dict) and cluster.get("error"):
            degraded = True
            cluster_warning = friendly_continuum_error(str(cluster.get("error")))
            if cluster_warning not in warnings:
                warnings.append(cluster_warning)
    payload["degraded"] = degraded
    payload["ok"] = not degraded
    payload["warnings"] = warnings
    if degraded and not payload.get("message"):
        payload["message"] = warnings[0] if warnings else friendly_continuum_error(None)
    return payload
