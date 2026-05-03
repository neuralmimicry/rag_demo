"""LLM credential access and billing helpers shared across Refiner runtime paths."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

from refiner.runtime.env import (
    LLM_CREDENTIAL_ENV_KEYS,
    LLM_PROCESS_DEFAULT_ENV_KEYS,
    apply_managed_ollama_defaults,
    strip_llm_credential_env,
)
from refiner.runtime.settings import llm_provider_access_from_settings


SHARED_LLM_CREDENTIAL_USER = "pbisaacs"
EXTERNAL_LLM_PROVIDERS = ("openai", "gemini", "nvidia")
PROVIDER_CREDENTIAL_ENV_KEYS = {
    "openai": ("OPENAI_API_KEY",),
    "gemini": (
        "GEMINI_API_KEY",
        "GEMINI_ACCESS_TOKEN",
        "GOOGLE_API_KEY",
        "GOOGLE_GENERATIVE_AI_API_KEY",
        "GOOGLE_ACCESS_TOKEN",
    ),
    "nvidia": ("NVIDIA_API_KEY",),
}


def normalize_llm_provider(provider: Optional[str]) -> Optional[str]:
    cleaned = str(provider or "").strip().lower()
    if cleaned in {"gpt", "chatgpt"}:
        return "openai"
    if cleaned == "google":
        return "gemini"
    if cleaned in {"nim", "nvidia_nim"}:
        return "nvidia"
    return cleaned or None


def gemini_credential_from_env(env: Mapping[str, str]) -> Optional[str]:
    for key in PROVIDER_CREDENTIAL_ENV_KEYS["gemini"]:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return None


def provider_credential_from_env(
    provider_type: Optional[str],
    env: Mapping[str, str],
) -> Optional[str]:
    normalized = normalize_llm_provider(provider_type)
    if normalized == "openai":
        value = str(env.get("OPENAI_API_KEY") or "").strip()
        return value or None
    if normalized == "nvidia":
        value = str(env.get("NVIDIA_API_KEY") or "").strip()
        return value or None
    if normalized == "gemini":
        return gemini_credential_from_env(env)
    if normalized == "ollama":
        value = str(env.get("OLLAMA_BASE_URL") or "").strip()
        return value or None
    return None


def provider_has_accessible_credentials(
    provider_type: Optional[str],
    env: Mapping[str, str],
) -> bool:
    normalized = normalize_llm_provider(provider_type)
    if normalized == "ollama":
        return True
    return bool(str(provider_credential_from_env(normalized, env) or "").strip())


def provider_base_url(
    provider_type: Optional[str],
    base_url: Optional[str],
    env: Mapping[str, str],
) -> Optional[str]:
    if base_url:
        return base_url
    normalized = normalize_llm_provider(provider_type)
    if normalized == "ollama":
        value = str(env.get("OLLAMA_BASE_URL") or "").strip()
        return value or None
    if normalized == "nvidia":
        value = str(env.get("NVIDIA_BASE_URL") or "").strip()
        return value or None
    return None


def user_can_use_shared_llm_credentials(
    user: Optional[str],
    *,
    role: Optional[str] = None,
) -> bool:
    cleaned_user = str(user or "").strip().lower()
    cleaned_role = str(role or "").strip().lower()
    return cleaned_user == SHARED_LLM_CREDENTIAL_USER or cleaned_role == "service_account"


def provider_billing_metadata(
    provider_type: Optional[str],
    *,
    settings: Optional[Dict[str, Any]] = None,
    user: Optional[str] = None,
    role: Optional[str] = None,
    secret_env: Optional[Mapping[str, str]] = None,
    process_env: Optional[Mapping[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    normalized = normalize_llm_provider(provider_type)
    if not normalized:
        return None
    if normalized == "ollama":
        return {"credential_source": "local", "chargeable": False}

    secret_source = secret_env or {}
    process_source = process_env or os.environ

    if user_can_use_shared_llm_credentials(user, role=role):
        if provider_has_accessible_credentials(normalized, secret_source):
            return {"credential_source": "user_key", "chargeable": False}
        if provider_has_accessible_credentials(normalized, process_source):
            return {"credential_source": "service_key", "chargeable": False}
        return None

    access = llm_provider_access_from_settings(settings).get(
        normalized,
        {"mode": "service", "acknowledged": False},
    )
    if not bool(access.get("acknowledged")):
        return None
    mode = str(access.get("mode") or "service").strip().lower()
    if mode == "user_key":
        if provider_has_accessible_credentials(normalized, secret_source):
            return {"credential_source": "user_key", "chargeable": False}
        return None
    if provider_has_accessible_credentials(normalized, process_source):
        return {"credential_source": "service_key", "chargeable": True}
    return None


def provider_billing_map(
    *,
    settings: Optional[Dict[str, Any]] = None,
    user: Optional[str] = None,
    role: Optional[str] = None,
    secret_env: Optional[Mapping[str, str]] = None,
    process_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    billing: Dict[str, Dict[str, Any]] = {
        "ollama": {"credential_source": "local", "chargeable": False}
    }
    for provider in EXTERNAL_LLM_PROVIDERS:
        metadata = provider_billing_metadata(
            provider,
            settings=settings,
            user=user,
            role=role,
            secret_env=secret_env,
            process_env=process_env,
        )
        if metadata:
            billing[provider] = metadata
    return billing


def accessible_configured_llm_provider(
    providers: List[Any],
    env: Mapping[str, str],
    *,
    exclude_provider: Optional[str] = None,
    exclude_model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    excluded_provider = normalize_llm_provider(exclude_provider)
    excluded_model = str(exclude_model or "").strip() or None
    for item in providers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        provider_type = (
            normalize_llm_provider(item.get("type") or item.get("provider") or name)
            or str(item.get("type") or item.get("provider") or name).strip()
        )
        if not provider_type or not provider_has_accessible_credentials(provider_type, env):
            continue
        model = str(item.get("model") or "").strip() or None
        if provider_type == excluded_provider and model == excluded_model:
            continue
        return {
            "provider": provider_type,
            "model": model,
            "base_url": provider_base_url(provider_type, item.get("base_url"), env),
            "api_key": provider_credential_from_env(provider_type, env),
            "name": name or None,
        }
    return None


def build_effective_llm_env_for_user(
    secret_env: Optional[Mapping[str, str]] = None,
    *,
    process_env: Optional[Mapping[str, str]] = None,
    settings: Optional[Dict[str, Any]] = None,
    user: Optional[str] = None,
    role: Optional[str] = None,
) -> Dict[str, str]:
    process_source = dict(process_env or os.environ)
    effective = dict(secret_env or {})
    apply_managed_ollama_defaults(effective, process_env=process_source)
    for key in ("NVIDIA_BASE_URL", "NVIDIA_MODEL", "NVIDIA_DEFAULT_MODEL"):
        if not str(effective.get(key) or "").strip():
            fallback = str(process_source.get(key) or "").strip()
            if fallback:
                effective[key] = fallback
    strip_llm_credential_env(effective)

    billing = provider_billing_map(
        settings=settings,
        user=user,
        role=role,
        secret_env=secret_env,
        process_env=process_source,
    )
    for provider, metadata in billing.items():
        if provider == "ollama":
            continue
        source_env = secret_env if metadata.get("credential_source") == "user_key" else process_source
        for key in PROVIDER_CREDENTIAL_ENV_KEYS.get(provider, ()):
            value = str((source_env or {}).get(key) or "").strip()
            if value:
                effective[key] = value
    return effective


def _job_secrets_env(job_secrets: Any) -> Dict[str, str]:
    secrets_env: Dict[str, str] = {}
    if isinstance(job_secrets, list):
        for entry in job_secrets:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            value = str(entry.get("value") or "").strip()
            if name and value:
                secrets_env[name] = value
    elif isinstance(job_secrets, dict):
        for name, value in job_secrets.items():
            cleaned_name = str(name or "").strip()
            cleaned_value = str(value or "").strip()
            if cleaned_name and cleaned_value:
                secrets_env[cleaned_name] = cleaned_value
    return secrets_env


def _provider_for_credential_env_key(name: str) -> Optional[str]:
    cleaned = str(name or "").strip()
    if not cleaned:
        return None
    for provider, keys in PROVIDER_CREDENTIAL_ENV_KEYS.items():
        if cleaned in keys:
            return provider
    return None


def serialize_provider_billing_map(billing: Dict[str, Dict[str, Any]]) -> str:
    return json.dumps(billing, ensure_ascii=True, sort_keys=True)


def build_job_runtime_env(
    owner: Optional[str],
    *,
    settings: Optional[Dict[str, Any]] = None,
    role: Optional[str] = None,
    secret_env: Optional[Mapping[str, str]] = None,
    process_env: Optional[Mapping[str, str]] = None,
    use_default_secrets: bool = True,
    job_secrets: Any = None,
) -> Dict[str, str]:
    process_source = dict(process_env or os.environ)
    base_secret_env = dict(secret_env or {}) if use_default_secrets else {}
    explicit_job_secret_env = _job_secrets_env(job_secrets)
    combined_secret_env = dict(base_secret_env)
    combined_secret_env.update(explicit_job_secret_env)

    env = dict(process_source)
    strip_llm_credential_env(env)

    for key, value in base_secret_env.items():
        if key in LLM_CREDENTIAL_ENV_KEYS:
            continue
        if value is not None:
            env[str(key)] = str(value)

    effective_llm_env = build_effective_llm_env_for_user(
        combined_secret_env,
        process_env=process_source,
        settings=settings,
        user=owner,
        role=role,
    )
    for key in LLM_PROCESS_DEFAULT_ENV_KEYS + LLM_CREDENTIAL_ENV_KEYS:
        value = str(effective_llm_env.get(key) or "").strip()
        if value:
            env[key] = value
        else:
            env.pop(key, None)

    billing = provider_billing_map(
        settings=settings,
        user=owner,
        role=role,
        secret_env=combined_secret_env,
        process_env=process_source,
    )

    # Explicit per-job secrets may contain provider credentials, but those still
    # have to respect the user's acknowledged provider-access mode. Only inject
    # LLM credential keys here when the effective selection resolved to the
    # caller's own key for that provider.
    for key, value in explicit_job_secret_env.items():
        provider = _provider_for_credential_env_key(key)
        if provider:
            metadata = billing.get(provider)
            if not metadata or str(metadata.get("credential_source") or "").strip().lower() != "user_key":
                continue
        env[key] = value

    env["REFINER_LLM_PROVIDER_BILLING"] = serialize_provider_billing_map(billing)
    return env
