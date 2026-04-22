"""Gail middleware bridge for Refiner provider and orchestration calls.

The bridge keeps the Refiner-side provider interface stable while delegating LLM,
neuromorphic, and orchestration decisions to Gail over HTTP.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

import requests

from refiner.llm_providers import (
    LLMError,
    LLMProvider,
    LLMQuotaError,
    LLMResponse,
    _env_bool,
    _env_int,
    _http_get,
    _http_post,
)


logger = logging.getLogger(__name__)


def gail_base_url() -> Optional[str]:
    value = os.getenv("REFINER_GAIL_BASE_URL") or os.getenv("GAIL_BASE_URL")
    if not value:
        return None
    cleaned = str(value).strip().rstrip("/")
    return cleaned or None


def gail_api_token() -> Optional[str]:
    value = os.getenv("REFINER_GAIL_API_TOKEN") or os.getenv("GAIL_API_TOKEN")
    cleaned = str(value or "").strip()
    return cleaned or None


def gail_enabled() -> bool:
    raw = os.getenv("REFINER_GAIL_ENABLED")
    if raw is not None:
        return _env_bool("REFINER_GAIL_ENABLED", False)
    return bool(gail_base_url())


def gail_status(*, candidate_limit: int = 20, probe_engines: bool = False, probe_providers: bool = False) -> Dict[str, Any]:
    client = _require_gail_client()
    query = f"limit={max(1, int(candidate_limit))}&probe_engines={str(bool(probe_engines)).lower()}&probe_providers={str(bool(probe_providers)).lower()}"
    response = _http_get(
        f"{client['base_url']}/v1/status/orchestration?{query}",
        headers=_headers(client["token"]),
        timeout=_env_int("LLM_TIMEOUT_SECONDS", 30),
        max_retries=1,
    )
    return _decode_json_response(response, provider="gail")


class GailProvider(LLMProvider):
    """LLMProvider-compatible proxy that targets the Gail middleware service."""

    def __init__(
        self,
        *,
        mode: str,
        direct_provider: Optional[str] = None,
        direct_model: Optional[str] = None,
        direct_api_key: Optional[str] = None,
        direct_access_token: Optional[str] = None,
        provider_base_url: Optional[str] = None,
        workflow: Optional[str] = None,
        role: Optional[str] = None,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        preferred_api_key: Optional[str] = None,
        preferred_access_token: Optional[str] = None,
        fallback_provider: Optional[str] = None,
        fallback_model: Optional[str] = None,
        fallback_api_key: Optional[str] = None,
        fallback_access_token: Optional[str] = None,
        include_configured: Optional[bool] = None,
        selection_mode: Optional[str] = None,
        max_candidates: Optional[int] = None,
        inter_request_gap: float = 0.0,
    ) -> None:
        super().__init__(inter_request_gap=inter_request_gap)
        client = _require_gail_client()
        self.gail_base_url = client["base_url"]
        self.gail_api_token = client["token"]
        self.gail_mode = str(mode or "direct").strip().lower() or "direct"
        self.gail_source_provider = str(direct_provider or preferred_provider or "gail").strip().lower() or "gail"
        self.gail_source_model = str(direct_model or preferred_model or "").strip() or None
        self.gail_source_api_key = str(direct_api_key or preferred_api_key or "").strip() or None
        self.gail_source_access_token = str(direct_access_token or preferred_access_token or "").strip() or None
        self.gail_source_base_url = str(provider_base_url or "").strip() or None
        self.workflow = str(workflow or "").strip().lower() or None
        self.role = str(role or "").strip().lower() or None
        self.preferred_provider = str(preferred_provider or "").strip().lower() or None
        self.preferred_model = str(preferred_model or "").strip() or None
        self.preferred_api_key = str(preferred_api_key or "").strip() or None
        self.preferred_access_token = str(preferred_access_token or "").strip() or None
        self.fallback_provider = str(fallback_provider or "").strip().lower() or None
        self.fallback_model = str(fallback_model or "").strip() or None
        self.fallback_api_key = str(fallback_api_key or "").strip() or None
        self.fallback_access_token = str(fallback_access_token or "").strip() or None
        self.include_configured = include_configured
        self.selection_mode = str(selection_mode or "").strip().lower() or None
        self.max_candidates = max_candidates
        self.name = (
            f"refiner_ai:{self.workflow}:{self.role}"
            if self.gail_mode == "workflow" and self.workflow and self.role
            else self.gail_source_provider
        )
        self.model = self.gail_source_model or "gail"
        self.gail_passthrough = True

    def _predict_impl(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        system: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResponse:
        if self.gail_mode == "workflow":
            payload = {
                "workflow": self.workflow,
                "role": self.role,
                "preferred_provider": self.preferred_provider,
                "preferred_model": self.preferred_model,
                "preferred_api_key": self.preferred_api_key,
                "preferred_access_token": self.preferred_access_token,
                "fallback_provider": self.fallback_provider,
                "fallback_model": self.fallback_model,
                "fallback_api_key": self.fallback_api_key,
                "fallback_access_token": self.fallback_access_token,
                "base_url": self.gail_source_base_url,
                "include_configured": self.include_configured,
                "selection_mode": self.selection_mode,
                "max_candidates": self.max_candidates,
                "messages": list(messages or []),
                "system": system,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout_seconds": timeout,
                "reasoning_effort": reasoning_effort,
            }
            data = self._post_json("/v1/llm/complete", payload, timeout=timeout)
        else:
            payload = {
                "provider": self.gail_source_provider,
                "model": self.gail_source_model,
                "api_key": self.gail_source_api_key,
                "access_token": self.gail_source_access_token,
                "base_url": self.gail_source_base_url,
                "messages": list(messages or []),
                "system": system,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout_seconds": timeout,
                "reasoning_effort": reasoning_effort,
            }
            data = self._post_json("/v1/llm/direct-complete", payload, timeout=timeout)
        response = LLMResponse(
            text=str(data.get("text") or ""),
            raw=data if isinstance(data, dict) else {"raw": data},
            latency_ms=_optional_int(data.get("latency_ms")),
            provider=str(data.get("provider") or self.gail_source_provider),
            model=str(data.get("model") or self.gail_source_model or self.model),
        )
        self.model = response.model or self.model
        return response

    def transcribe(self, file_path: str, timeout: Optional[int] = None) -> str:
        with open(file_path, "rb") as handle:
            files = {
                "file": (
                    os.path.basename(file_path),
                    handle,
                    "application/octet-stream",
                )
            }
            data = {
                "provider": self.gail_source_provider,
                "model": self.gail_source_model or "",
            }
            if self.gail_source_api_key:
                data["api_key"] = self.gail_source_api_key
            if self.gail_source_access_token:
                data["access_token"] = self.gail_source_access_token
            if self.gail_source_base_url:
                data["base_url"] = self.gail_source_base_url
            if timeout is not None:
                data["timeout_seconds"] = str(timeout)
            headers = _headers(self.gail_api_token)
            headers.pop("Content-Type", None)
            resp = requests.post(
                f"{self.gail_base_url}/v1/llm/transcribe",
                headers=headers,
                files=files,
                data=data,
                timeout=timeout or _env_int("LLM_TIMEOUT_SECONDS", 180),
            )
        payload = _decode_json_response(resp, provider="gail")
        return str(payload.get("text") or "")

    def health_check(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        resp = _http_get(
            f"{self.gail_base_url}/healthz",
            headers=_headers(self.gail_api_token),
            timeout=timeout or _env_int("LLM_TIMEOUT_SECONDS", 30),
            max_retries=1,
        )
        try:
            data = resp.json()
        except Exception:
            data = {}
        return {
            "ok": resp.status_code < 300,
            "status_code": resp.status_code,
            "latency_ms": None,
            "message": str(data.get("service") or data.get("message") or resp.text[:200] or "gail"),
        }

    def get_context_window(self) -> int:
        return max(4096, _env_int("GAIL_CONTEXT_WINDOW", 128000))

    def _post_json(self, path: str, payload: Dict[str, Any], *, timeout: Optional[int]) -> Dict[str, Any]:
        response = _http_post(
            f"{self.gail_base_url}{path}",
            headers=_headers(self.gail_api_token),
            json_payload=payload,
            timeout=timeout or _env_int("LLM_TIMEOUT_SECONDS", 180),
            max_retries=_env_int("LLM_MAX_RETRIES", 2),
        )
        return _decode_json_response(response, provider="gail")


def build_direct_provider(
    name: str,
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    inter_request_gap: float = 0.0,
    api_key: Optional[str] = None,
    access_token: Optional[str] = None,
) -> GailProvider:
    return GailProvider(
        mode="direct",
        direct_provider=name,
        direct_model=model,
        direct_api_key=api_key,
        direct_access_token=access_token,
        provider_base_url=base_url,
        inter_request_gap=inter_request_gap,
    )


def build_workflow_provider(
    *,
    workflow: str,
    role: str,
    preferred_provider: Optional[str],
    preferred_model: Optional[str],
    preferred_api_key: Optional[str] = None,
    preferred_access_token: Optional[str] = None,
    fallback_provider: Optional[str] = None,
    fallback_model: Optional[str] = None,
    fallback_api_key: Optional[str] = None,
    fallback_access_token: Optional[str] = None,
    base_url: Optional[str] = None,
    include_configured: Optional[bool] = None,
    inter_request_gap: float = 0.0,
    selection_mode: Optional[str] = None,
    max_candidates: Optional[int] = None,
) -> GailProvider:
    return GailProvider(
        mode="workflow",
        workflow=workflow,
        role=role,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        preferred_api_key=preferred_api_key,
        preferred_access_token=preferred_access_token,
        fallback_provider=fallback_provider,
        fallback_model=fallback_model,
        fallback_api_key=fallback_api_key,
        fallback_access_token=fallback_access_token,
        provider_base_url=base_url,
        include_configured=include_configured,
        selection_mode=selection_mode,
        max_candidates=max_candidates,
        inter_request_gap=inter_request_gap,
    )


def build_workflow_provider_from_candidates(
    candidates: Sequence[Optional[LLMProvider]],
    *,
    workflow: str,
    role: str,
    include_configured: Optional[bool] = None,
    base_url: Optional[str] = None,
    inter_request_gap: float = 0.0,
    selection_mode: Optional[str] = None,
    max_candidates: Optional[int] = None,
) -> GailProvider:
    existing = [provider for provider in candidates if provider is not None]
    primary = _provider_spec_from_instance(existing[0]) if existing else {}
    fallback = _provider_spec_from_instance(existing[1]) if len(existing) > 1 else {}
    return build_workflow_provider(
        workflow=workflow,
        role=role,
        preferred_provider=primary.get("provider"),
        preferred_model=primary.get("model"),
        preferred_api_key=primary.get("api_key"),
        preferred_access_token=primary.get("access_token"),
        fallback_provider=fallback.get("provider"),
        fallback_model=fallback.get("model"),
        fallback_api_key=fallback.get("api_key"),
        fallback_access_token=fallback.get("access_token"),
        base_url=base_url or primary.get("base_url"),
        include_configured=include_configured,
        inter_request_gap=inter_request_gap,
        selection_mode=selection_mode,
        max_candidates=max_candidates,
    )


def _provider_spec_from_instance(provider: LLMProvider) -> Dict[str, Optional[str]]:
    if getattr(provider, "gail_passthrough", False):
        return {
            "provider": getattr(provider, "gail_source_provider", None),
            "model": getattr(provider, "gail_source_model", None),
            "api_key": getattr(provider, "gail_source_api_key", None),
            "access_token": getattr(provider, "gail_source_access_token", None),
            "base_url": getattr(provider, "gail_source_base_url", None),
        }
    return {
        "provider": str(getattr(provider, "name", "") or "").strip().lower() or None,
        "model": str(getattr(provider, "model", "") or "").strip() or None,
        "api_key": str(getattr(provider, "api_key", "") or "").strip() or None,
        "access_token": str(getattr(provider, "access_token", "") or "").strip() or None,
        "base_url": str(getattr(provider, "base_url", "") or "").strip() or None,
    }


def _headers(token: Optional[str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _require_gail_client() -> Dict[str, Optional[str]]:
    base_url = gail_base_url()
    if not base_url:
        raise LLMError("Gail is enabled but REFINER_GAIL_BASE_URL/GAIL_BASE_URL is not configured")
    return {"base_url": base_url, "token": gail_api_token()}


def _decode_json_response(resp: requests.Response, *, provider: str) -> Dict[str, Any]:
    try:
        payload = resp.json()
    except Exception:
        payload = {"message": resp.text[:500]}
    if resp.status_code == 429:
        raise LLMQuotaError(str(payload.get("message") or payload.get("error") or "Gail quota exceeded"))
    if resp.status_code >= 400:
        raise LLMError(str(payload.get("message") or payload.get("error") or f"{provider} request failed: {resp.status_code}"))
    if not isinstance(payload, dict):
        raise LLMError(f"{provider} returned a non-object JSON payload")
    return payload


def _optional_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None
