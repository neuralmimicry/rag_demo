from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from refiner.integrations.platform.continuum_client import (
    ContinuumClientConfig,
    continuum_headers,
    continuum_request,
    continuum_response_payload,
    friendly_continuum_error,
    workspace_action_create,
    workspace_action_refresh,
)


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Optional[Dict[str, Any]] = None,
        content: bytes = b"{}",
        json_error: Optional[Exception] = None,
    ):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = content
        self._json_error = json_error

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Dict[str, Any]:
        if self._json_error is not None:
            raise self._json_error
        return dict(self._payload)


class _WorkspaceError(RuntimeError):
    def __init__(self, code: str, detail: str, status_code: int):
        super().__init__(f"{code}:{status_code}:{detail}")
        self.code = code
        self.detail = detail
        self.status_code = status_code


@dataclass
class _FakeJob:
    job_id: str = "job-12345678"
    project_name: str = "Refiner"
    owner: str = "alice"
    repo_info: Dict[str, Any] = field(
        default_factory=lambda: {
            "repo": "rag_demo",
            "repo_url": "https://github.com/example/rag_demo.git",
            "branch": "main",
            "fork_org": "alice",
            "fork_repo": "rag-demo-fork",
        }
    )
    workspace_env: Dict[str, Any] = field(default_factory=dict)
    persist_calls: List[bool] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)

    def persist(self, *, force: bool = False) -> None:
        self.persist_calls.append(force)

    def append_log(self, message: str) -> None:
        self.logs.append(message)


def _config(**overrides: Any) -> ContinuumClientConfig:
    base = {
        "api_base": "https://continuum.example/api",
        "auth_token": "top-secret",
        "timeout": 30.0,
        "vm_region": "gb-mids",
        "vm_sku": "standard-a2",
        "vm_os": "ubuntu-22.04",
        "vm_public_key_id": "pk-123",
        "vm_init_script": "clone {repo_url}",
        "ide_url_template": "https://ide.example/{vm_id}",
        "preview_url_template": "https://preview.example/{vm_name}",
    }
    base.update(overrides)
    return ContinuumClientConfig(**base)


def _error_factory(code: str, detail: str, status_code: int) -> Exception:
    return _WorkspaceError(code, detail, status_code)


def _format_workspace_template(template: str, variables: Dict[str, Any]) -> str:
    if not template:
        return ""
    return template.format(**variables)


def test_continuum_headers_and_request_forward_resolved_transport_arguments():
    config = _config()
    calls: List[Dict[str, Any]] = []

    def _http_request(**kwargs: Any) -> _FakeResponse:
        calls.append(kwargs)
        return _FakeResponse(payload={"success": True, "data": {}})

    response = continuum_request(
        config,
        http_request=_http_request,
        method="POST",
        path="/vm/create",
        json_body={"name": "workspace"},
        timeout_sec=12.5,
        retries=3,
    )

    assert isinstance(response, _FakeResponse)
    assert continuum_headers(config) == {
        "Accept": "application/json",
        "Authorization": "Bearer top-secret",
        "X-NMC-Token": "top-secret",
    }
    assert calls == [
        {
            "method": "POST",
            "url": "https://continuum.example/api/vm/create",
            "headers": continuum_headers(config),
            "json_body": {"name": "workspace"},
            "timeout": 12.5,
            "retries": 3,
        }
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_FakeResponse(status_code=503, payload={"success": False, "message": "down"}), "returned status 503"),
        (_FakeResponse(payload={"success": False, "message": "denied"}), "failed: denied"),
        (_FakeResponse(json_error=ValueError("bad json")), "invalid JSON: bad json"),
    ],
)
def test_continuum_response_payload_rejects_invalid_envelopes(response: _FakeResponse, message: str):
    with pytest.raises(RuntimeError, match=message):
        continuum_response_payload(response, operation="Continuum VM create")


def test_workspace_action_refresh_updates_workspace_from_continuum():
    config = _config()
    job = _FakeJob(
        workspace_env={
            "provider": "continuum",
            "vm_id": "vm-123",
            "ide_url": "https://old.example/ide",
            "preview_url": "https://old.example/preview",
        }
    )

    def _request(method: str, path: str, **kwargs: Any) -> _FakeResponse:
        assert method == "GET"
        assert path == "/vm/get/vm-123"
        assert kwargs["timeout_sec"] == 15.0
        return _FakeResponse(
            payload={
                "success": True,
                "message": "workspace refreshed",
                "data": {
                    "id": "vm-123",
                    "name": "workspace-123",
                    "region": "gb-mids",
                    "sku": "standard-a2",
                    "status": "running",
                    "initScript": "hidden",
                },
            }
        )

    result = workspace_action_refresh(
        job,
        config=config,
        now_iso=lambda: "2026-04-30T12:00:00Z",
        request=_request,
        format_workspace_template=_format_workspace_template,
        error_factory=_error_factory,
        timeout_sec=15.0,
    )

    assert result["status"] == "refreshed"
    assert result["workspace"]["status"] == "running"
    assert result["workspace"]["ide_url"] == "https://ide.example/vm-123"
    assert result["workspace"]["preview_url"] == "https://preview.example/workspace-123"
    assert result["workspace"]["details"] == "workspace refreshed"
    assert result["workspace"]["vm"] == {
        "id": "vm-123",
        "name": "workspace-123",
        "region": "gb-mids",
        "sku": "standard-a2",
        "status": "running",
    }
    assert job.persist_calls == [True]


def test_workspace_action_create_creates_workspace_with_formatted_templates():
    config = _config()
    job = _FakeJob()
    requests: List[Dict[str, Any]] = []

    def _request(method: str, path: str, **kwargs: Any) -> _FakeResponse:
        requests.append({"method": method, "path": path, **kwargs})
        return _FakeResponse(
            payload={
                "success": True,
                "message": "created",
                "data": {
                    "id": "vm-999",
                    "name": "refiner-devbox",
                    "region": "us-east",
                    "sku": "gpu-a10",
                    "status": "provisioning",
                    "initScript": "internal-only",
                },
            }
        )

    result = workspace_action_create(
        job,
        {"name": "refiner-devbox", "region": "us-east", "sku": "gpu-a10"},
        config=config,
        now_iso=lambda: "2026-04-30T12:00:00Z",
        is_truthy=lambda value: str(value).lower() in {"1", "true", "yes", "on"},
        request=_request,
        format_workspace_template=_format_workspace_template,
        error_factory=_error_factory,
        timeout_sec=10.0,
    )

    assert result["status"] == "created"
    assert requests == [
        {
            "method": "POST",
            "path": "/vm/create",
            "json_body": {
                "name": "refiner-devbox",
                "sku": "gpu-a10",
                "region": "us-east",
                "osImage": "ubuntu-22.04",
                "publicKeyId": "pk-123",
                "initScript": "clone https://github.com/example/rag_demo.git",
            },
            "timeout_sec": 10.0,
        }
    ]
    assert result["workspace"]["provider"] == "continuum"
    assert result["workspace"]["vm_id"] == "vm-999"
    assert result["workspace"]["ide_url"] == "https://ide.example/vm-999"
    assert result["workspace"]["preview_url"] == "https://preview.example/refiner-devbox"
    assert result["workspace"]["vm"] == {
        "id": "vm-999",
        "name": "refiner-devbox",
        "region": "us-east",
        "sku": "gpu-a10",
        "status": "provisioning",
    }
    assert job.persist_calls == [True]
    assert job.logs[0] == "Requesting Continuum workspace: refiner-devbox (us-east)"
    assert job.logs[1] == "Continuum workspace created: vm-999."


def test_workspace_action_create_reuses_existing_workspace_without_request():
    config = _config()
    job = _FakeJob(workspace_env={"provider": "continuum", "vm_id": "vm-123", "status": "running"})

    def _request(method: str, path: str, **kwargs: Any) -> _FakeResponse:
        raise AssertionError("workspace creation request should not be issued")

    result = workspace_action_create(
        job,
        {},
        config=config,
        now_iso=lambda: "2026-04-30T12:00:00Z",
        is_truthy=lambda value: bool(value),
        request=_request,
        format_workspace_template=_format_workspace_template,
        error_factory=_error_factory,
    )

    assert result == {"status": "exists", "workspace": {"provider": "continuum", "vm_id": "vm-123", "status": "running"}}


def test_workspace_action_create_reports_missing_continuum_configuration():
    job = _FakeJob()

    with pytest.raises(_WorkspaceError) as excinfo:
        workspace_action_create(
            job,
            {},
            config=_config(api_base="", vm_public_key_id=""),
            now_iso=lambda: "2026-04-30T12:00:00Z",
            is_truthy=lambda value: bool(value),
            request=lambda *args, **kwargs: _FakeResponse(),
            format_workspace_template=_format_workspace_template,
            error_factory=_error_factory,
        )

    assert excinfo.value.code == "continuum_unavailable"
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Missing: CONTINUUM_API_BASE, CONTINUUM_VM_PUBLIC_KEY_ID"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("", "Continuum is temporarily unavailable. Showing last known worker state."),
        ("request timed out", "Continuum timed out. Showing last known worker state while retrying."),
        ("401 unauthorized", "Continuum authentication failed. Showing cached worker state."),
        ("unexpected failure", "Continuum communication is degraded. Showing last known worker state."),
    ],
)
def test_friendly_continuum_error_normalises_operator_messages(message: str, expected: str):
    assert friendly_continuum_error(message) == expected
