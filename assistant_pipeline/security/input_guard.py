"""Structured input checks for assistant and RAG requests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from security_utils import url_allowed

from assistant_pipeline.security.policies import AssistantSecurityPolicy

_PROMPT_LEAK_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|override|bypass)\b.{0,48}\b(previous|prior|system|developer|hidden)\b.{0,48}\b(prompt|instruction)s?\b",
            flags=re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "hidden_prompt_request",
        re.compile(
            r"\b(reveal|show|print|display|dump|repeat|leak|expose|tell me)\b.{0,48}\b(system prompt|developer prompt|hidden prompt|hidden instructions?|internal instructions?)\b",
            flags=re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"\b(act as|pretend to be|simulate|you are now)\b.{0,24}\b(system|developer)\b",
            flags=re.IGNORECASE | re.DOTALL,
        ),
    ),
)


@dataclass(frozen=True)
class InputGuardResult:
    """Normalised request data and any blocking outcome from input checks."""

    payload: Dict[str, Any]
    messages: List[Dict[str, str]] = field(default_factory=list)
    blocked_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceGuardResult:
    """Result of validating RAG ingestion sources."""

    blocked_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _normalise_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalise_messages(
    raw_messages: Any,
    *,
    allowed_roles: Sequence[str],
) -> Tuple[List[Dict[str, str]], List[str]]:
    cleaned: List[Dict[str, str]] = []
    invalid_roles: List[str] = []
    if not isinstance(raw_messages, list):
        return cleaned, invalid_roles
    allowed = {str(role).strip().lower() for role in allowed_roles if str(role).strip()}
    for entry in raw_messages:
        if not isinstance(entry, Mapping):
            continue
        role = _normalise_text(entry.get("role")).lower()
        content = entry.get("content")
        if not role or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        if role in allowed:
            cleaned.append({"role": role, "content": content})
            continue
        invalid_roles.append(role)
    return cleaned, invalid_roles


def _detect_prompt_leak_request(text: str) -> Optional[str]:
    if not text:
        return None
    for label, pattern in _PROMPT_LEAK_PATTERNS:
        if pattern.search(text):
            if label == "instruction_override":
                return "Detected instruction-override language targeting hidden prompts or instructions."
            if label == "hidden_prompt_request":
                return "Detected a request to reveal hidden prompts or internal instructions."
            return "Detected role-hijacking language targeting privileged assistant roles."
    return None


def apply_input_guard(
    *,
    route: str,
    payload: Mapping[str, Any],
    policy: AssistantSecurityPolicy,
    text_fields: Sequence[str] = (),
    message_field: str = "",
    guardrail_scan: Optional[Callable[[str], Optional[str]]] = None,
    use_legacy_guardrail: bool = True,
) -> InputGuardResult:
    """Normalise user input and apply compatible blocking checks."""

    cleaned_payload = dict(payload or {})
    collected_texts: List[str] = []
    for field_name in text_fields:
        cleaned_value = _normalise_text(cleaned_payload.get(field_name))
        cleaned_payload[field_name] = cleaned_value
        if cleaned_value:
            collected_texts.append(cleaned_value)

    messages: List[Dict[str, str]] = []
    invalid_roles: List[str] = []
    if message_field:
        messages, invalid_roles = _normalise_messages(
            cleaned_payload.get(message_field),
            allowed_roles=sorted(policy.input.allowed_message_roles),
        )
        cleaned_payload[message_field] = messages
        collected_texts.extend(message.get("content", "") for message in messages if message.get("content"))

    blocked_reason: Optional[str] = None
    if policy.input.policy_enabled and policy.input.strict_message_roles and invalid_roles:
        first_role = invalid_roles[0]
        if first_role in policy.input.blocked_impersonation_roles:
            blocked_reason = f"User-supplied message role is not allowed: {first_role}."
        else:
            blocked_reason = f"Unsupported message role is not allowed: {first_role}."

    combined_text = "\n\n".join(text for text in collected_texts if text).strip()
    if blocked_reason is None and policy.input.policy_enabled and policy.input.block_prompt_leak_requests:
        blocked_reason = _detect_prompt_leak_request(combined_text)

    if blocked_reason is None and use_legacy_guardrail and guardrail_scan is not None:
        blocked_reason = guardrail_scan(combined_text) if combined_text else None

    return InputGuardResult(
        payload=cleaned_payload,
        messages=messages,
        blocked_reason=blocked_reason,
        metadata={
            "route": route,
            "policy_enabled": policy.input.policy_enabled,
            "checked_text_fields": len(tuple(text_fields)),
            "message_count": len(messages),
            "invalid_message_role_count": len(invalid_roles),
            "used_legacy_guardrail": bool(use_legacy_guardrail and guardrail_scan is not None),
            "prompt_leak_checks": bool(policy.input.policy_enabled and policy.input.block_prompt_leak_requests),
        },
    )


def apply_rag_source_guard(
    *,
    route: str,
    sources: Sequence[Mapping[str, Any]],
    policy: AssistantSecurityPolicy,
) -> SourceGuardResult:
    """Validate remote RAG sources before extraction or fetch work begins."""

    source_urls: List[str] = []
    for entry in sources:
        if not isinstance(entry, Mapping):
            continue
        candidate = _normalise_text(entry.get("url"))
        if candidate:
            source_urls.append(candidate)

    blocked_reason: Optional[str] = None
    if policy.input.policy_enabled and policy.input.validate_rag_source_urls:
        for candidate in source_urls:
            if not url_allowed(candidate):
                blocked_reason = f"Remote RAG source URL is not allowed: {candidate}"
                break

    return SourceGuardResult(
        blocked_reason=blocked_reason,
        metadata={
            "route": route,
            "policy_enabled": policy.input.policy_enabled,
            "validate_rag_source_urls": bool(policy.input.validate_rag_source_urls),
            "source_url_count": len(source_urls),
        },
    )
