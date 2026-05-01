"""HTTP handlers for assistant admin/debug routes."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from flask import Response, jsonify, request


HandlerMap = Dict[str, Callable[..., Response]]


def _json_response(payload, status_code: int = 200) -> Response:
    return jsonify(payload), status_code


def _authorise_admin(current_user: Callable[[], Optional[str]], is_admin_user: Callable[[str], bool]) -> Optional[Response]:
    user = current_user()
    if not user:
        return _json_response({"error": "unauthorized"}, 401)
    if not is_admin_user(user):
        return _json_response({"error": "forbidden"}, 403)
    return None


def _clean_arg(name: str) -> str:
    return str(request.args.get(name) or "").strip()


def _limit_arg(name: str, safe_int: Callable[[Any, int], int], *, default: int, maximum: int) -> int:
    return max(1, min(safe_int(request.args.get(name), default), maximum))


def build_assistant_admin_handlers(
    *,
    current_user: Callable[[], Optional[str]],
    is_admin_user: Callable[[str], bool],
    get_assistant_conversation_store: Callable[[], Optional[Any]],
    get_assistant_trace_store: Callable[[], Optional[Any]],
    safe_int: Callable[[Any, int], int],
) -> HandlerMap:
    """Build thin Flask handlers for assistant observability endpoints."""

    def assistant_admin_conversations() -> Response:
        denied = _authorise_admin(current_user, is_admin_user)
        if denied is not None:
            return denied
        store = get_assistant_conversation_store()
        if store is None:
            return _json_response(
                {
                    "error": "assistant_conversation_store_unavailable",
                    "details": "Assistant conversation storage is not configured.",
                },
                503,
            )
        owner = _clean_arg("owner")
        route = _clean_arg("route")
        limit = _limit_arg("limit", safe_int, default=20, maximum=200)
        try:
            conversations = store.list_conversations(owner=owner, route=route, limit=limit)
        except Exception as exc:
            return _json_response(
                {
                    "error": "assistant_conversation_store_read_failed",
                    "details": str(exc),
                },
                500,
            )
        return _json_response(
            {
                "conversations": conversations,
                "filters": {"owner": owner or None, "route": route or None},
                "limit": limit,
            }
        )

    def assistant_admin_conversation_detail(conversation_id: str) -> Response:
        denied = _authorise_admin(current_user, is_admin_user)
        if denied is not None:
            return denied
        store = get_assistant_conversation_store()
        if store is None:
            return _json_response(
                {
                    "error": "assistant_conversation_store_unavailable",
                    "details": "Assistant conversation storage is not configured.",
                },
                503,
            )
        owner = _clean_arg("owner")
        turn_limit = _limit_arg("turn_limit", safe_int, default=50, maximum=200)
        try:
            conversation = store.get_conversation(conversation_id, owner=owner)
            if not conversation:
                return _json_response({"error": "assistant_conversation_not_found"}, 404)
            turn_owner = str(conversation.get("owner") or owner or "").strip()
            turns = store.recent_turns(conversation_id, owner=turn_owner, limit=turn_limit)
        except Exception as exc:
            return _json_response(
                {
                    "error": "assistant_conversation_store_read_failed",
                    "details": str(exc),
                },
                500,
            )
        return _json_response(
            {
                "conversation": conversation,
                "turns": turns,
                "turn_limit": turn_limit,
            }
        )

    def assistant_admin_traces() -> Response:
        denied = _authorise_admin(current_user, is_admin_user)
        if denied is not None:
            return denied
        store = get_assistant_trace_store()
        if store is None:
            return _json_response(
                {
                    "error": "assistant_trace_store_unavailable",
                    "details": "Assistant trace storage is not configured.",
                },
                503,
            )
        owner = _clean_arg("owner")
        route = _clean_arg("route")
        status = _clean_arg("status")
        conversation_id = _clean_arg("conversation_id")
        limit = _limit_arg("limit", safe_int, default=20, maximum=200)
        try:
            traces = store.list_traces(
                owner=owner,
                route=route,
                status=status,
                conversation_id=conversation_id,
                limit=limit,
            )
        except Exception as exc:
            return _json_response(
                {
                    "error": "assistant_trace_store_read_failed",
                    "details": str(exc),
                },
                500,
            )
        return _json_response(
            {
                "traces": traces,
                "filters": {
                    "owner": owner or None,
                    "route": route or None,
                    "status": status or None,
                    "conversation_id": conversation_id or None,
                },
                "limit": limit,
            }
        )

    def assistant_admin_trace_detail(trace_id: str) -> Response:
        denied = _authorise_admin(current_user, is_admin_user)
        if denied is not None:
            return denied
        store = get_assistant_trace_store()
        if store is None:
            return _json_response(
                {
                    "error": "assistant_trace_store_unavailable",
                    "details": "Assistant trace storage is not configured.",
                },
                503,
            )
        owner = _clean_arg("owner")
        span_limit = _limit_arg("span_limit", safe_int, default=200, maximum=500)
        try:
            trace = store.get_trace(trace_id, owner=owner)
            if not trace:
                return _json_response({"error": "assistant_trace_not_found"}, 404)
            spans = store.list_spans(trace_id, limit=span_limit)
        except Exception as exc:
            return _json_response(
                {
                    "error": "assistant_trace_store_read_failed",
                    "details": str(exc),
                },
                500,
            )
        return _json_response(
            {
                "trace": trace,
                "spans": spans,
                "span_limit": span_limit,
            }
        )

    assistant_admin_conversations.__name__ = "assistant_admin_conversations"
    assistant_admin_conversation_detail.__name__ = "assistant_admin_conversation_detail"
    assistant_admin_traces.__name__ = "assistant_admin_traces"
    assistant_admin_trace_detail.__name__ = "assistant_admin_trace_detail"

    return {
        "assistant_admin_conversations": assistant_admin_conversations,
        "assistant_admin_conversation_detail": assistant_admin_conversation_detail,
        "assistant_admin_traces": assistant_admin_traces,
        "assistant_admin_trace_detail": assistant_admin_trace_detail,
    }
