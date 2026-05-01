"""Policy checks for assistant-initiated external tool and action use."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from assistant_pipeline.security.policies import AssistantSecurityPolicy

_UNSAFE_ACTION_RE = re.compile(
    r"\b(delete|remove|destroy|drop|purge|erase|wipe|truncate|reset|revoke|cancel|refund|charge|shutdown|restart|"
    r"reboot|deploy|apply|write|update|create|send|email|message|push|execute|run)\b",
    flags=re.IGNORECASE,
)
_SAFE_READ_RE = re.compile(
    r"\b(get|list|search|find|read|fetch|show|describe|status|preview|validate|test|dry[_ -]?run)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ToolUseGuardResult:
    """Outcome of validating one external tool-use request."""

    allowed: bool
    error_code: str = ""
    blocked_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _normalise_text(value: Any) -> str:
    return str(value or "").strip()


def _bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _tool_risk_level(prompt: str, tool_name: str, arguments: Mapping[str, Any]) -> str:
    argument_parts = []
    for key, value in (arguments or {}).items():
        cleaned_key = _normalise_text(key)
        cleaned_value = _normalise_text(value)
        if cleaned_key:
            argument_parts.append(cleaned_key)
        if cleaned_value:
            argument_parts.append(cleaned_value)
    combined = " ".join(
        [
            _normalise_text(prompt),
            _normalise_text(tool_name).replace("_", " ").replace("-", " "),
            " ".join(argument_parts),
        ]
    ).strip()
    if not combined:
        return "unknown"
    has_unsafe_signal = bool(_UNSAFE_ACTION_RE.search(combined))
    has_safe_signal = bool(_SAFE_READ_RE.search(combined))
    if has_unsafe_signal:
        return "unsafe"
    if has_safe_signal and not has_unsafe_signal:
        return "read_only"
    return "unknown"


def apply_tool_use_guard(
    *,
    route: str,
    prompt: str,
    mcp_request: Mapping[str, Any],
    is_admin_user: bool,
    policy: AssistantSecurityPolicy,
    request_kind: str = "mcp",
) -> ToolUseGuardResult:
    """Validate whether an external tool or action call should proceed."""

    server_name = _normalise_text(mcp_request.get("server"))
    tool_name = _normalise_text(mcp_request.get("tool"))
    arguments = mcp_request.get("arguments")
    arguments_dict = dict(arguments) if isinstance(arguments, Mapping) else {}
    confirmation_present = _bool_flag(mcp_request.get("confirmed")) or _bool_flag(mcp_request.get("allow_unsafe"))
    preview_mode = any(_bool_flag(arguments_dict.get(key)) for key in ("dry_run", "preview", "read_only"))
    risk_level = _tool_risk_level(prompt, tool_name, arguments_dict)
    resolved_request_kind = _normalise_text(request_kind).lower() or "mcp"
    blocked_reason: Optional[str] = None
    error_code = ""
    if resolved_request_kind == "mcp" and policy.tool.admin_only_mcp and not is_admin_user:
        error_code = "mcp_forbidden"
        blocked_reason = "MCP tool use is restricted to administrators."
    elif policy.tool.block_unsafe_tool_requests and risk_level == "unsafe" and not (confirmation_present or preview_mode):
        error_code = "unsafe_tool_use_blocked"
        blocked_reason = (
            f"Explicit confirmation is required before using unsafe MCP tool '{tool_name or 'unknown'}'."
        )
    return ToolUseGuardResult(
        allowed=blocked_reason is None,
        error_code=error_code,
        blocked_reason=blocked_reason,
        metadata={
            "route": route,
            "server": server_name,
            "tool": tool_name,
            "request_kind": resolved_request_kind,
            "admin_user": bool(is_admin_user),
            "admin_only_mcp": bool(policy.tool.admin_only_mcp),
            "unsafe_tool_policy": bool(policy.tool.block_unsafe_tool_requests),
            "risk_level": risk_level,
            "confirmation_present": confirmation_present,
            "preview_mode": preview_mode,
        },
    )
