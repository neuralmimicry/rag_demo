"""Structured output checks for assistant and RAG responses."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Tuple

from refiner.security_utils import redact_text

from assistant_pipeline.contracts import ServiceError
from assistant_pipeline.security.policies import AssistantSecurityPolicy

_EMAIL_RE = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_UK_NI_RE = re.compile(r"\b(?:[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D])\b", flags=re.IGNORECASE)
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
_REDACTABLE_TEXT_KEYS = {
    "answer",
    "claim_text",
    "context",
    "reply",
    "requirements_text",
    "rationale",
    "summary",
    "text",
    "text_preview",
    "value",
}
_REDACTABLE_STRING_LIST_KEYS = {"steps"}


@dataclass(frozen=True)
class OutputGuardResult:
    """Validated response payload and output-stage security metadata."""

    payload: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


def _redact_output_text(text: str) -> Tuple[str, int]:
    if not isinstance(text, str) or not text:
        return str(text or ""), 0
    redacted = redact_text(text)
    count = 0
    if redacted != text:
        count += 1
    redacted, replaced = _EMAIL_RE.subn("[email]", redacted)
    count += replaced
    redacted, replaced = _PHONE_RE.subn("[phone]", redacted)
    count += replaced
    redacted, replaced = _SSN_RE.subn("[social-security]", redacted)
    count += replaced
    redacted, replaced = _UK_NI_RE.subn("[national-insurance]", redacted)
    count += replaced
    redacted, replaced = _CARD_RE.subn("[card]", redacted)
    count += replaced
    return redacted, count


def _redact_payload(value: Any, *, key: str = "") -> Tuple[Any, int]:
    if isinstance(value, dict):
        redacted_dict: Dict[str, Any] = {}
        redaction_count = 0
        for child_key, child_value in value.items():
            redacted_child, child_count = _redact_payload(child_value, key=str(child_key or ""))
            redacted_dict[child_key] = redacted_child
            redaction_count += child_count
        return redacted_dict, redaction_count
    if isinstance(value, list):
        redacted_items = []
        redaction_count = 0
        for item in value:
            if isinstance(item, str) and key in _REDACTABLE_STRING_LIST_KEYS:
                redacted_item, child_count = _redact_output_text(item)
            else:
                redacted_item, child_count = _redact_payload(item, key=key)
            redacted_items.append(redacted_item)
            redaction_count += child_count
        return redacted_items, redaction_count
    if isinstance(value, str) and key in _REDACTABLE_TEXT_KEYS:
        return _redact_output_text(value)
    return value, 0


def _require_string(route: str, payload: Dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), str):
        raise ServiceError(
            "invalid_output_payload",
            payload={"details": f"{route} response field '{key}' must be a string."},
        )


def _require_list(route: str, payload: Dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), list):
        raise ServiceError(
            "invalid_output_payload",
            payload={"details": f"{route} response field '{key}' must be a list."},
        )


def _require_dict(route: str, payload: Dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), dict):
        raise ServiceError(
            "invalid_output_payload",
            payload={"details": f"{route} response field '{key}' must be an object."},
        )


def _validate_output_shape(route: str, payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ServiceError(
            "invalid_output_payload",
            payload={"details": f"{route} response payload must be an object."},
        )
    if route == "assistant_requirements":
        for key in ("reply", "provider", "model", "gesture_mode", "avatar_mode"):
            _require_string(route, payload, key)
        return
    if route == "assistant_form_fill":
        _require_list(route, payload, "suggestions")
        for item in payload.get("suggestions") or []:
            if not isinstance(item, dict):
                raise ServiceError(
                    "invalid_output_payload",
                    payload={"details": f"{route} suggestions entries must be objects."},
                )
            if not isinstance(item.get("field_id"), str) or not str(item.get("field_id")).strip():
                raise ServiceError(
                    "invalid_output_payload",
                    payload={"details": f"{route} suggestions entries must include a field_id string."},
                )
        return
    if route in {"playground_plan", "execution_plan"}:
        for key in ("summary", "project_name", "requirements_text", "provider", "model"):
            _require_string(route, payload, key)
        _require_list(route, payload, "steps")
        _require_dict(route, payload, "job_payload")
        token_estimate = payload.get("token_estimate")
        if not isinstance(token_estimate, (int, float)):
            raise ServiceError(
                "invalid_output_payload",
                payload={"details": f"{route} response field 'token_estimate' must be numeric."},
            )
        return
    if route == "assistant_rag_mcp":
        _require_string(route, payload, "answer")
        _require_list(route, payload, "rag_matches")
        if "citations" in payload:
            _require_list(route, payload, "citations")
        if "claim_bindings" in payload:
            _require_list(route, payload, "claim_bindings")
        if "citation_audit" in payload:
            _require_dict(route, payload, "citation_audit")
        return
    if route == "rag_query":
        for key in ("name", "query", "context"):
            _require_string(route, payload, key)
        _require_list(route, payload, "matches")
        if "citations" in payload:
            _require_list(route, payload, "citations")


def apply_output_guard(
    *,
    route: str,
    response_payload: Dict[str, Any],
    policy: AssistantSecurityPolicy,
) -> OutputGuardResult:
    """Validate and optionally redact a response payload before it leaves the pipeline."""

    cleaned_payload = copy.deepcopy(response_payload)
    if policy.output.policy_enabled and policy.output.validate_shapes:
        _validate_output_shape(route, cleaned_payload)

    redaction_count = 0
    if policy.output.policy_enabled and policy.output.redact_pii:
        cleaned_payload, redaction_count = _redact_payload(cleaned_payload)

    return OutputGuardResult(
        payload=cleaned_payload,
        metadata={
            "route": route,
            "policy_enabled": policy.output.policy_enabled,
            "validate_shapes": bool(policy.output.validate_shapes),
            "redaction_count": redaction_count,
        },
    )


def build_assistant_reply_payload(
    *,
    route: str,
    reply_text: str,
    provider: str,
    model: str,
    request_payload: Dict[str, Any],
    policy: AssistantSecurityPolicy,
    reply_payload_builder: Callable[..., Dict[str, Any]],
) -> OutputGuardResult:
    """Build and then guard the standard assistant reply payload shape."""

    safe_reply = str(reply_text or "")
    reply_redaction_count = 0
    if policy.output.policy_enabled and policy.output.redact_pii:
        safe_reply, reply_redaction_count = _redact_output_text(safe_reply)
    response_payload = reply_payload_builder(
        safe_reply,
        provider,
        model,
        payload=request_payload,
    )
    guarded = apply_output_guard(route=route, response_payload=response_payload, policy=policy)
    metadata = dict(guarded.metadata)
    metadata["reply_redaction_count"] = reply_redaction_count
    return OutputGuardResult(payload=guarded.payload, metadata=metadata)
