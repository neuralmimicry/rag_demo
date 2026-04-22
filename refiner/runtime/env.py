"""Runtime environment helpers for managed service defaults."""

from __future__ import annotations

import ipaddress
import os
from typing import Mapping, MutableMapping, Optional
from urllib.parse import urlparse


OLLAMA_ENV_KEYS = (
    "OLLAMA_BASE_URL",
    "OLLAMA_DEFAULT_MODEL",
    "OLLAMA_MODEL",
    "SOLVER_OLLAMA_MODEL",
)

LLM_ENV_KEYS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
) + OLLAMA_ENV_KEYS


def _normalized_hostname(raw: Optional[str]) -> str:
    host = str(raw or "").strip().lower().strip("[]")
    if not host:
        return ""
    return host


def is_loopback_url(raw: Optional[str]) -> bool:
    """Return True when a URL or host points at a local-only endpoint."""
    value = str(raw or "").strip()
    if not value:
        return False
    parsed = urlparse(value if "://" in value else f"http://{value}")
    host = _normalized_hostname(parsed.hostname or parsed.path)
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_unspecified)


def is_cluster_local_url(raw: Optional[str]) -> bool:
    """Return True when a URL or host targets a Kubernetes-internal service name."""
    value = str(raw or "").strip()
    if not value:
        return False
    parsed = urlparse(value if "://" in value else f"http://{value}")
    host = _normalized_hostname(parsed.hostname or parsed.path)
    if not host:
        return False
    return bool(
        host.endswith(".cluster.local")
        or ".svc." in host
        or host.endswith(".svc")
    )


def apply_managed_ollama_defaults(
    env: MutableMapping[str, str],
    *,
    process_env: Optional[Mapping[str, str]] = None,
) -> MutableMapping[str, str]:
    """Prefer deployment-managed Ollama service settings over stale loopback overrides."""
    source = process_env or os.environ
    managed_base_url = str(source.get("OLLAMA_BASE_URL") or "").strip()
    current_base_url = str(env.get("OLLAMA_BASE_URL") or "").strip()
    if managed_base_url and (
        not current_base_url
        or is_loopback_url(current_base_url)
        or is_cluster_local_url(current_base_url)
    ):
        env["OLLAMA_BASE_URL"] = managed_base_url
    for key in ("OLLAMA_DEFAULT_MODEL", "OLLAMA_MODEL", "SOLVER_OLLAMA_MODEL"):
        managed_value = str(source.get(key) or "").strip()
        if managed_value and not str(env.get(key) or "").strip():
            env[key] = managed_value
    return env


def build_effective_llm_env(
    secret_env: Optional[Mapping[str, str]] = None,
    *,
    process_env: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Compose the effective LLM env visible to Refiner request-time workflows."""
    source = process_env or os.environ
    env = dict(secret_env or {})
    apply_managed_ollama_defaults(env, process_env=source)
    for key in LLM_ENV_KEYS:
        if not str(env.get(key) or "").strip():
            fallback = str(source.get(key) or "").strip()
            if fallback:
                env[key] = fallback
    return env
