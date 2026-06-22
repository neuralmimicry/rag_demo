"""HTTP handlers for assistant admin/debug routes."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

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


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _bucket_increment(bucket: Dict[str, int], key: str) -> None:
    cleaned = str(key or "").strip() or "unknown"
    bucket[cleaned] = int(bucket.get(cleaned) or 0) + 1


def _sorted_breakdown(bucket: Dict[str, int]) -> List[Dict[str, Any]]:
    ordered = sorted(bucket.items(), key=lambda item: (-item[1], item[0]))
    return [{"label": key, "count": count} for key, count in ordered]


def _fallback_trace_analytics(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(traces)
    if total <= 0:
        return {
            "window_hours": None,
            "total_traces": 0,
            "success_rate": 0.0,
            "cache_hit_rate": 0.0,
            "handoff_rate": 0.0,
            "conversion_rate": 0.0,
            "avg_duration_ms": 0.0,
            "breakdowns": {
                "route": [],
                "channel": [],
                "assistant_profile": [],
                "sentiment": [],
                "provider": [],
                "error_code": [],
            },
        }

    success_count = 0
    cache_hits = 0
    handoff_count = 0
    conversion_count = 0

    route_counts: Dict[str, int] = {}
    channel_counts: Dict[str, int] = {}
    profile_counts: Dict[str, int] = {}
    sentiment_counts: Dict[str, int] = {}
    provider_counts: Dict[str, int] = {}
    error_counts: Dict[str, int] = {}

    for row in traces:
        status = str(row.get("status") or "").strip().lower()
        if status == "success":
            success_count += 1
        if _as_bool(row.get("cache_hit")):
            cache_hits += 1
        request_meta = row.get("request_meta") if isinstance(row.get("request_meta"), dict) else {}
        response_meta = row.get("response_meta") if isinstance(row.get("response_meta"), dict) else {}
        _bucket_increment(route_counts, str(row.get("route") or "unknown"))
        _bucket_increment(
            channel_counts,
            str(response_meta.get("channel") or request_meta.get("channel") or "web").strip().lower(),
        )
        _bucket_increment(
            profile_counts,
            str(response_meta.get("assistant_profile") or request_meta.get("assistant_profile") or "requirements")
            .strip()
            .lower(),
        )
        _bucket_increment(sentiment_counts, str(response_meta.get("sentiment_label") or "neutral").strip().lower())
        provider = str(row.get("provider") or "unknown").strip()
        model = str(row.get("model") or "default").strip()
        _bucket_increment(provider_counts, f"{provider}/{model}")
        if _as_bool(response_meta.get("handoff_requested")):
            handoff_count += 1
        if _as_bool(response_meta.get("conversion_completed")):
            conversion_count += 1
        error_code = str(row.get("error_code") or "").strip()
        if error_code:
            _bucket_increment(error_counts, error_code)

    return {
        "window_hours": None,
        "total_traces": total,
        "success_rate": round(success_count / total, 4),
        "cache_hit_rate": round(cache_hits / total, 4),
        "handoff_rate": round(handoff_count / total, 4),
        "conversion_rate": round(conversion_count / total, 4),
        "avg_duration_ms": 0.0,
        "breakdowns": {
            "route": _sorted_breakdown(route_counts),
            "channel": _sorted_breakdown(channel_counts),
            "assistant_profile": _sorted_breakdown(profile_counts),
            "sentiment": _sorted_breakdown(sentiment_counts),
            "provider": _sorted_breakdown(provider_counts),
            "error_code": _sorted_breakdown(error_counts),
        },
    }


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

    def assistant_admin_analytics() -> Response:
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
        channel = _clean_arg("channel").lower()
        assistant_profile = _clean_arg("assistant_profile").lower()
        since_hours = max(1, min(safe_int(request.args.get("since_hours"), 24), 24 * 30))
        limit = _limit_arg("limit", safe_int, default=2000, maximum=10000)
        try:
            if hasattr(store, "analytics_summary"):
                analytics = store.analytics_summary(
                    owner=owner,
                    route=route,
                    channel=channel,
                    assistant_profile=assistant_profile,
                    since_hours=since_hours,
                    limit=limit,
                )
            else:
                traces = store.list_traces(owner=owner, route=route, limit=limit)
                analytics = _fallback_trace_analytics(traces if isinstance(traces, list) else [])
                analytics["window_hours"] = since_hours
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
                "analytics": analytics,
                "filters": {
                    "owner": owner or None,
                    "route": route or None,
                    "channel": channel or None,
                    "assistant_profile": assistant_profile or None,
                    "since_hours": since_hours,
                },
                "limit": limit,
            }
        )

    assistant_admin_conversations.__name__ = "assistant_admin_conversations"
    assistant_admin_conversation_detail.__name__ = "assistant_admin_conversation_detail"
    assistant_admin_traces.__name__ = "assistant_admin_traces"
    assistant_admin_trace_detail.__name__ = "assistant_admin_trace_detail"
    assistant_admin_analytics.__name__ = "assistant_admin_analytics"

    return {
        "assistant_admin_conversations": assistant_admin_conversations,
        "assistant_admin_conversation_detail": assistant_admin_conversation_detail,
        "assistant_admin_traces": assistant_admin_traces,
        "assistant_admin_trace_detail": assistant_admin_trace_detail,
        "assistant_admin_analytics": assistant_admin_analytics,
    }
