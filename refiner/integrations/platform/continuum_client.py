"""Continuum client helpers extracted from the Refiner runtime.

The runtime still owns environment loading and Flask request handling, but the
Continuum-specific transport, response parsing, and workspace VM flows live
here so they can evolve without growing `refiner_web.py` further.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import requests


@dataclass(frozen=True)
class ContinuumClientConfig:
    """Resolved Continuum runtime configuration for one request context."""

    api_base: str
    auth_token: str = ""
    timeout: float = 20.0
    vm_region: str = "gb-mids"
    vm_sku: str = "standard-a2"
    vm_os: str = "ubuntu-22.04"
    vm_public_key_id: str = ""
    vm_init_script: str = ""
    ide_url_template: str = ""
    preview_url_template: str = ""


def continuum_enabled(config: ContinuumClientConfig) -> bool:
    """Return whether a Continuum API base URL is configured."""

    return bool((config.api_base or "").strip())


def continuum_ready(config: ContinuumClientConfig) -> bool:
    """Return whether Refiner has enough Continuum config to create VMs."""

    return continuum_enabled(config) and bool((config.vm_public_key_id or "").strip())


def continuum_headers(config: ContinuumClientConfig) -> Dict[str, str]:
    """Build the auth headers expected by the Continuum API."""

    headers: Dict[str, str] = {"Accept": "application/json"}
    token = str(config.auth_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-NMC-Token"] = token
    return headers


def continuum_request(
    config: ContinuumClientConfig,
    *,
    http_request: Callable[..., requests.Response],
    method: str,
    path: str,
    json_body: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[float] = None,
    retries: Optional[int] = None,
) -> requests.Response:
    """Issue one Continuum API request through the injected HTTP transport."""

    url = f"{str(config.api_base or '').rstrip('/')}{path}"
    timeout = float(config.timeout if timeout_sec is None else timeout_sec)
    return http_request(
        method=method,
        url=url,
        headers=continuum_headers(config),
        json_body=json_body,
        timeout=timeout,
        retries=retries,
    )


def continuum_response_payload(response: requests.Response, *, operation: str) -> Dict[str, Any]:
    """Validate the standard Continuum envelope and return the full payload."""

    status_code = int(getattr(response, "status_code", 500) or 500)
    ok = bool(getattr(response, "ok", 200 <= status_code < 300))
    if not ok:
        raise RuntimeError(f"{operation} returned status {status_code}.")
    try:
        payload = response.json() if getattr(response, "content", None) else {}
    except Exception as exc:
        raise RuntimeError(f"{operation} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("success"):
        detail = payload.get("message") if isinstance(payload, dict) else "request failed"
        raise RuntimeError(f"{operation} failed: {detail}")
    return payload


def continuum_json_payload(response: requests.Response, *, operation: str) -> Dict[str, Any]:
    """Return the `data` object from a validated Continuum response envelope."""

    payload = continuum_response_payload(response, operation=operation)
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def friendly_continuum_error(message: Optional[str]) -> str:
    """Normalise low-level Continuum failures into operator-facing text."""

    text = (message or "").strip()
    if not text:
        return "Continuum is temporarily unavailable. Showing last known worker state."
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "Continuum timed out. Showing last known worker state while retrying."
    if "connection refused" in lowered or "failed to establish" in lowered or "name or service not known" in lowered:
        return "Cannot reach Continuum right now. Showing last known worker state."
    if "401" in lowered or "403" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
        return "Continuum authentication failed. Showing cached worker state."
    if "invalid json" in lowered:
        return "Continuum returned an invalid response. Showing cached worker state."
    return "Continuum communication is degraded. Showing last known worker state."


def workspace_template_vars(
    job: Any,
    *,
    config: ContinuumClientConfig,
    vm_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the template variables used in workspace URLs and init scripts."""

    repo_info = job.repo_info if isinstance(getattr(job, "repo_info", None), dict) else {}
    vm_data = vm_data or {}
    return {
        "job_id": getattr(job, "job_id", "") or "",
        "project_name": getattr(job, "project_name", "") or "",
        "owner": getattr(job, "owner", "") or "",
        "repo": repo_info.get("repo") or "",
        "repo_url": repo_info.get("repo_url") or repo_info.get("clone_url") or "",
        "branch": repo_info.get("branch") or "",
        "fork_org": repo_info.get("fork_org") or "",
        "fork_repo": repo_info.get("fork_repo") or "",
        "vm_id": vm_data.get("id") or "",
        "vm_name": vm_data.get("name") or "",
        "vm_region": vm_data.get("region") or config.vm_region,
        "vm_sku": vm_data.get("sku") or config.vm_sku,
        "vm_status": vm_data.get("status") or "",
    }


def workspace_action_refresh(
    job: Any,
    *,
    config: ContinuumClientConfig,
    now_iso: Callable[[], str],
    request: Callable[..., requests.Response],
    format_workspace_template: Callable[[str, Dict[str, Any]], str],
    error_factory: Callable[[str, str, int], Exception],
    timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Refresh a job workspace from Continuum or no-op for non-Continuum jobs."""

    now = now_iso()
    workspace_env = job.workspace_env if isinstance(getattr(job, "workspace_env", None), dict) else {}
    if workspace_env.get("provider") != "continuum" or not workspace_env.get("vm_id"):
        workspace_env["updated_at"] = now
        job.workspace_env = workspace_env
        job.persist(force=True)
        return {"status": "refreshed", "workspace": workspace_env}
    if not continuum_enabled(config):
        raise error_factory(
            "continuum_unavailable",
            "Continuum API base is not configured.",
            400,
        )

    vm_id = str(workspace_env.get("vm_id") or "").strip()
    request_timeout = max(1.0, min(float(config.timeout), float(timeout_sec or config.timeout)))
    try:
        response = request("GET", f"/vm/get/{vm_id}", timeout_sec=request_timeout)
        payload = continuum_response_payload(response, operation="Continuum VM refresh")
    except Exception as exc:
        raise error_factory("continuum_error", str(exc), 502) from exc

    vm_data = payload.get("data") or {}
    if isinstance(vm_data, dict):
        vm_data = dict(vm_data)
        vm_data.pop("initScript", None)
    template_vars = workspace_template_vars(job, config=config, vm_data=vm_data if isinstance(vm_data, dict) else {})
    ide_url = format_workspace_template(config.ide_url_template, template_vars) or workspace_env.get("ide_url")
    preview_url = (
        format_workspace_template(config.preview_url_template, template_vars) or workspace_env.get("preview_url")
    )
    workspace_env.update(
        {
            "status": vm_data.get("status") if isinstance(vm_data, dict) else workspace_env.get("status") or "provisioning",
            "ide_url": ide_url or "",
            "preview_url": preview_url or "",
            "updated_at": now,
            "details": payload.get("message") or workspace_env.get("details"),
            "vm": vm_data if isinstance(vm_data, dict) else {},
        }
    )
    job.workspace_env = workspace_env
    job.persist(force=True)
    return {"status": "refreshed", "workspace": workspace_env}


def workspace_action_create(
    job: Any,
    payload: Dict[str, Any],
    *,
    config: ContinuumClientConfig,
    now_iso: Callable[[], str],
    is_truthy: Callable[[Any], bool],
    request: Callable[..., requests.Response],
    format_workspace_template: Callable[[str, Dict[str, Any]], str],
    error_factory: Callable[[str, str, int], Exception],
    timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Create or reuse a Continuum-backed workspace for a Refiner job."""

    if not continuum_ready(config):
        missing = []
        if not str(config.api_base or "").strip():
            missing.append("CONTINUUM_API_BASE")
        if not str(config.vm_public_key_id or "").strip():
            missing.append("CONTINUUM_VM_PUBLIC_KEY_ID")
        detail = "Continuum not configured." if not missing else f"Missing: {', '.join(missing)}"
        raise error_factory("continuum_unavailable", detail, 400)

    now = now_iso()
    workspace_env = job.workspace_env if isinstance(getattr(job, "workspace_env", None), dict) else {}
    force_create = is_truthy(payload.get("force"))
    if workspace_env.get("provider") == "continuum" and workspace_env.get("vm_id") and not force_create:
        return {"status": "exists", "workspace": workspace_env}

    vm_name = str(payload.get("name") or f"refiner-{str(getattr(job, 'job_id', ''))[:8]}").strip()
    init_script = (payload.get("init_script") or config.vm_init_script or "").strip()
    template_vars = workspace_template_vars(job, config=config, vm_data={})
    init_script = format_workspace_template(init_script, template_vars) if init_script else ""
    vm_request = {
        "name": vm_name,
        "sku": str(payload.get("sku") or config.vm_sku).strip(),
        "region": str(payload.get("region") or config.vm_region).strip(),
        "osImage": str(payload.get("os_image") or config.vm_os).strip(),
        "publicKeyId": str(payload.get("public_key_id") or config.vm_public_key_id).strip(),
        "initScript": init_script,
    }
    job.append_log(f"Requesting Continuum workspace: {vm_request['name']} ({vm_request['region']})")
    request_timeout = max(1.0, min(float(config.timeout), float(timeout_sec or config.timeout)))
    try:
        response = request(
            "POST",
            "/vm/create",
            json_body=vm_request,
            timeout_sec=request_timeout,
        )
        envelope = continuum_response_payload(response, operation="Continuum VM create")
    except Exception as exc:
        raise error_factory("continuum_error", str(exc), 502) from exc

    vm_data = envelope.get("data") or {}
    if isinstance(vm_data, dict):
        vm_data = dict(vm_data)
        vm_data.pop("initScript", None)
    template_vars = workspace_template_vars(job, config=config, vm_data=vm_data if isinstance(vm_data, dict) else {})
    ide_url = format_workspace_template(config.ide_url_template, template_vars)
    preview_url = format_workspace_template(config.preview_url_template, template_vars)
    workspace_env = {
        "provider": "continuum",
        "status": vm_data.get("status") if isinstance(vm_data, dict) else "provisioning",
        "vm_id": vm_data.get("id") if isinstance(vm_data, dict) else "",
        "ide_url": ide_url,
        "preview_url": preview_url,
        "requested_at": now,
        "updated_at": now,
        "details": envelope.get("message") or "Continuum workspace created",
        "vm": vm_data if isinstance(vm_data, dict) else {},
    }
    job.workspace_env = workspace_env
    job.persist(force=True)
    job.append_log(f"Continuum workspace created: {workspace_env.get('vm_id') or '--'}.")
    return {"status": "created", "workspace": workspace_env}
