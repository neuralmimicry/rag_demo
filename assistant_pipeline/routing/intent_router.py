"""Intent-routing helpers for assistant and RAG pipeline requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class AssistantRoutingPolicy:
    """Feature flags and limits for route-intent resolution."""

    enabled: bool = False
    skill_hint_limit: int = 4
    capability_hint_max_items: int = 4


@dataclass(frozen=True)
class RouteIntent:
    """Resolved route strategy used to shape prompts and cache scope."""

    route: str
    intent_id: str
    prompt_profile: str
    workflow: str = ""
    role: str = "assistant"
    cacheable: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


def assistant_routing_policy_from_config(config: Mapping[str, Any] | None) -> AssistantRoutingPolicy:
    """Build a typed routing policy from runtime config."""

    values = dict(config or {})
    try:
        skill_hint_limit = max(1, int(values.get("skill_hint_limit") or 4))
    except Exception:
        skill_hint_limit = 4
    try:
        capability_hint_max_items = max(1, int(values.get("capability_hint_max_items") or 4))
    except Exception:
        capability_hint_max_items = 4
    return AssistantRoutingPolicy(
        enabled=_flag(values.get("enabled"), False),
        skill_hint_limit=skill_hint_limit,
        capability_hint_max_items=capability_hint_max_items,
    )


def resolve_route_intent(
    *,
    route: str,
    payload: Mapping[str, Any] | None,
    policy: AssistantRoutingPolicy,
    is_marketing_assistant: bool = False,
    has_rag: bool = False,
    has_mcp: bool = False,
) -> RouteIntent:
    """Resolve a stable intent record for the current route execution."""

    route_name = _clean(route) or "assistant"
    values = dict(payload or {})

    if route_name == "assistant_requirements":
        mode = _clean(values.get("mode") or "ask").lower() or "ask"
        profile = "marketing" if is_marketing_assistant else "requirements"
        if policy.enabled:
            intent_id = f"assistant_requirements:{profile}:{mode}"
        else:
            intent_id = f"assistant_requirements:{mode}"
        return RouteIntent(
            route=route_name,
            intent_id=intent_id,
            prompt_profile=profile,
            workflow="assistant_requirements",
            role="assistant",
            cacheable=False,
            metadata={
                "routing_enabled": policy.enabled,
                "mode": mode,
                "profile": profile,
            },
        )

    if route_name == "assistant_form_fill":
        workflow = _clean(values.get("workflow")) or "generic_form"
        scope = _clean(values.get("scope")) or "workflow"
        intent_id = f"assistant_form_fill:{workflow}" if policy.enabled else "assistant_form_fill"
        return RouteIntent(
            route=route_name,
            intent_id=intent_id,
            prompt_profile="structured_form",
            workflow=workflow,
            role="assistant",
            cacheable=False,
            metadata={
                "routing_enabled": policy.enabled,
                "workflow": workflow,
                "scope": scope,
            },
        )

    if route_name == "playground_plan":
        intent_id = "playground_plan:quick_build" if policy.enabled else "playground_plan"
        return RouteIntent(
            route=route_name,
            intent_id=intent_id,
            prompt_profile="playground_planner",
            workflow="playground_plan",
            role="planner",
            cacheable=False,
            metadata={
                "routing_enabled": policy.enabled,
                "project_run": True,
            },
        )

    if route_name == "assistant_rag_mcp":
        if has_mcp and has_rag:
            prompt_profile = "rag_mcp_live"
        elif has_mcp:
            prompt_profile = "mcp_live"
        elif has_rag:
            prompt_profile = "rag_grounded"
        else:
            prompt_profile = "direct_answer"
        if policy.enabled:
            intent_id = f"assistant_rag_mcp:{prompt_profile}"
        else:
            intent_id = "assistant_rag_mcp"
        return RouteIntent(
            route=route_name,
            intent_id=intent_id,
            prompt_profile=prompt_profile,
            workflow="assistant_rag_mcp",
            role="assistant",
            cacheable=bool(has_rag and not has_mcp),
            metadata={
                "routing_enabled": policy.enabled,
                "has_rag": bool(has_rag),
                "has_mcp": bool(has_mcp),
                "prompt_profile": prompt_profile,
            },
        )

    if route_name == "rag_query":
        intent_id = "rag_query:retrieval" if policy.enabled else "rag_query"
        return RouteIntent(
            route=route_name,
            intent_id=intent_id,
            prompt_profile="rag_retrieval",
            workflow="rag_query",
            role="assistant",
            cacheable=True,
            metadata={
                "routing_enabled": policy.enabled,
                "top_k": values.get("top_k"),
            },
        )

    return RouteIntent(
        route=route_name,
        intent_id=route_name,
        prompt_profile=route_name,
        workflow=route_name,
        role="assistant",
        cacheable=False,
        metadata={"routing_enabled": policy.enabled},
    )
