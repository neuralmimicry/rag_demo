"""Conversation persistence helpers for the assistant pipeline.

The extracted assistant services keep conversation persistence best-effort so
request success does not depend on the optional central conversation store.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from assistant_pipeline.dependencies import AssistantPipelineDependencies


def conversation_id_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    """Extract a cleaned conversation id from a request payload."""

    cleaned = str(payload.get("conversation_id") or "").strip()
    return cleaned or None


def ensure_conversation(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    conversation_id: Optional[str],
    route: str,
    scope: str = "",
    title: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Ensure a conversation header exists when central conversation storage is enabled."""

    store = deps.get_assistant_conversation_store()
    if store is None or not owner or not conversation_id:
        return
    try:
        store.ensure_conversation(
            conversation_id,
            owner,
            route=route,
            scope=scope,
            title=title,
            metadata=metadata,
        )
    except Exception as exc:  # pragma: no cover - best effort only
        deps.logger.debug("Assistant conversation ensure skipped for %s: %s", route, exc)


def append_turn(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    conversation_id: Optional[str],
    role: str,
    route: str,
    content: str = "",
    prompt_text: str = "",
    requirements_text: str = "",
    rewritten_query: str = "",
    provider: str = "",
    model: str = "",
    request_payload: Optional[Dict[str, Any]] = None,
    response_payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a conversation turn when central conversation storage is enabled."""

    store = deps.get_assistant_conversation_store()
    if store is None or not owner or not conversation_id:
        return
    try:
        store.append_turn(
            conversation_id,
            owner,
            role=role,
            route=route,
            content=content,
            prompt_text=prompt_text,
            requirements_text=requirements_text,
            rewritten_query=rewritten_query,
            provider=provider,
            model=model,
            request_payload=request_payload,
            response_payload=response_payload,
            metadata=metadata,
        )
    except Exception as exc:  # pragma: no cover - best effort only
        deps.logger.debug("Assistant conversation turn skipped for %s/%s: %s", route, role, exc)


def recent_turns(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    conversation_id: Optional[str],
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """Load recent turns for a conversation if the backing store is available."""

    store = deps.get_assistant_conversation_store()
    if store is None or not owner or not conversation_id:
        return []
    try:
        rows = store.recent_turns(conversation_id, owner=owner, limit=limit)
    except Exception as exc:  # pragma: no cover - best effort only
        deps.logger.debug("Assistant conversation read skipped for %s: %s", conversation_id, exc)
        return []
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]
