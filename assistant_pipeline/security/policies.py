"""Security policy models for assistant pipeline request and response handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, FrozenSet, Mapping

_DEFAULT_ALLOWED_MESSAGE_ROLES = frozenset({"user", "assistant"})
_DEFAULT_BLOCKED_IMPERSONATION_ROLES = frozenset({"system", "developer", "tool", "function"})


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalise_roles(value: Any, *, default: FrozenSet[str]) -> FrozenSet[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        cleaned = {str(item).strip().lower() for item in value if str(item).strip()}
        if cleaned:
            return frozenset(cleaned)
    return frozenset(default)


@dataclass(frozen=True)
class AssistantToolUsePolicy:
    """Policy controls for assistant-triggered external tool and action use."""

    admin_only_mcp: bool = True
    block_unsafe_tool_requests: bool = False


@dataclass(frozen=True)
class AssistantInputPolicy:
    """Input-stage security controls for assistant and RAG requests."""

    policy_enabled: bool = True
    strict_message_roles: bool = False
    block_prompt_leak_requests: bool = False
    validate_rag_source_urls: bool = False
    allowed_message_roles: FrozenSet[str] = field(default_factory=lambda: _DEFAULT_ALLOWED_MESSAGE_ROLES)
    blocked_impersonation_roles: FrozenSet[str] = field(
        default_factory=lambda: _DEFAULT_BLOCKED_IMPERSONATION_ROLES
    )


@dataclass(frozen=True)
class AssistantOutputPolicy:
    """Output-stage security controls for assistant and RAG responses."""

    policy_enabled: bool = True
    redact_pii: bool = False
    validate_shapes: bool = True


@dataclass(frozen=True)
class AssistantSecurityPolicy:
    """Resolved request and response policy bundle for the assistant pipeline."""

    input: AssistantInputPolicy = field(default_factory=AssistantInputPolicy)
    tool: AssistantToolUsePolicy = field(default_factory=AssistantToolUsePolicy)
    output: AssistantOutputPolicy = field(default_factory=AssistantOutputPolicy)


def assistant_security_policy_from_config(config: Mapping[str, Any] | None) -> AssistantSecurityPolicy:
    """Build a typed policy object from runtime configuration flags."""

    values = dict(config or {})
    policy_enabled = _flag(values.get("policy_enabled"), True)
    return AssistantSecurityPolicy(
        input=AssistantInputPolicy(
            policy_enabled=policy_enabled,
            strict_message_roles=_flag(values.get("strict_message_roles"), False),
            block_prompt_leak_requests=_flag(values.get("block_prompt_leak_requests"), False),
            validate_rag_source_urls=_flag(values.get("validate_rag_source_urls"), False),
            allowed_message_roles=_normalise_roles(
                values.get("allowed_message_roles"),
                default=_DEFAULT_ALLOWED_MESSAGE_ROLES,
            ),
            blocked_impersonation_roles=_normalise_roles(
                values.get("blocked_impersonation_roles"),
                default=_DEFAULT_BLOCKED_IMPERSONATION_ROLES,
            ),
        ),
        tool=AssistantToolUsePolicy(
            admin_only_mcp=_flag(values.get("admin_only_mcp"), True),
            block_unsafe_tool_requests=_flag(values.get("block_unsafe_tool_requests"), False),
        ),
        output=AssistantOutputPolicy(
            policy_enabled=_flag(values.get("output_policy_enabled"), policy_enabled),
            redact_pii=_flag(values.get("redact_output_pii"), False),
            validate_shapes=_flag(values.get("validate_output_shapes"), True),
        ),
    )
