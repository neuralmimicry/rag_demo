"""HTTP handlers for extracted assistant routes."""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, Tuple

from flask import Response, jsonify, request

from assistant_api.schemas import load_json_object
from assistant_pipeline.contracts import ServiceError
from assistant_pipeline.dependencies import AssistantPipelineDependencies
from assistant_pipeline.experience import normalise_channel_context
from assistant_pipeline.runtime.first_arrival_gate import claim_first_arrival
from assistant_pipeline import service as assistant_service


HandlerMap = Dict[str, Callable[..., Response]]


def _json_response(payload, status_code: int = 200) -> Response:
    return jsonify(payload), status_code


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_workflow(payload: Dict[str, object]) -> str:
    workflow = str(payload.get("workflow") or payload.get("route") or "").strip().lower()
    if workflow in {"assistant_rag_mcp", "assistant-rag-mcp", "assistant-requirements"}:
        return workflow.replace("-", "_")
    if workflow in {"assistant_form_fill", "playground_plan", "execution_plan", "assistant_requirements"}:
        return workflow
    has_rag = isinstance(payload.get("rag"), dict)
    has_tools = isinstance(payload.get("mcp"), dict) or isinstance(payload.get("atlassian"), dict)
    if has_rag or has_tools:
        return "assistant_rag_mcp"
    return "assistant_requirements"


def _response_text(payload: Dict[str, object]) -> str:
    for key in ("reply", "answer", "summary", "message"):
        value = payload.get(key)
        if value is None:
            continue
        cleaned = str(value).strip()
        if cleaned:
            return cleaned
    return ""


def _strip_wake_prefix(text: str, wake_name: str) -> tuple[bool, str]:
    cleaned = str(text or "").strip()
    wake = str(wake_name or "aaron").strip().lower() or "aaron"
    if not cleaned:
        return False, ""
    escaped = re.escape(wake)
    pattern = re.compile(rf"^\s*(?:hey|ok|okay)?\s*{escaped}\s*[,:\-]?\s*(.*)$", re.IGNORECASE)
    match = pattern.match(cleaned)
    if not match:
        return False, cleaned
    remainder = str(match.group(1) or "").strip()
    return True, remainder


def _default_wake_required() -> bool:
    return _coerce_bool(os.getenv("REFINER_AARON_CHANNEL_WAKE_REQUIRED", "true"))


def _text_from_telegram_update(payload: Dict[str, Any]) -> Tuple[str, str, str]:
    message = {}
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            message = candidate
            break
    if not message:
        callback = payload.get("callback_query")
        if isinstance(callback, dict):
            nested_message = callback.get("message")
            if isinstance(nested_message, dict):
                message = nested_message
                callback_data = str(callback.get("data") or "").strip()
                if callback_data:
                    message = dict(message)
                    message["text"] = callback_data
    text = str(message.get("text") or message.get("caption") or "").strip()
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    chat_id = str(chat.get("id") or "").strip()
    sender_id = str(sender.get("id") or "").strip()
    return text, chat_id, sender_id


def _text_from_whatsapp_update(payload: Dict[str, Any]) -> Tuple[str, str, str, str]:
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes") if isinstance(entry.get("changes"), list) else []
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            phone_number_id = str(metadata.get("phone_number_id") or "").strip()
            messages = value.get("messages") if isinstance(value.get("messages"), list) else []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                text_block = message.get("text") if isinstance(message.get("text"), dict) else {}
                interactive = message.get("interactive") if isinstance(message.get("interactive"), dict) else {}
                button_reply = interactive.get("button_reply") if isinstance(interactive.get("button_reply"), dict) else {}
                list_reply = interactive.get("list_reply") if isinstance(interactive.get("list_reply"), dict) else {}
                body = (
                    text_block.get("body")
                    or button_reply.get("title")
                    or list_reply.get("title")
                    or message.get("body")
                    or ""
                )
                text = str(body or "").strip()
                if not text:
                    continue
                from_number = str(message.get("from") or "").strip()
                message_id = str(message.get("id") or "").strip()
                return text, from_number, phone_number_id, message_id
    return "", "", "", ""


def build_assistant_handlers(deps: AssistantPipelineDependencies) -> HandlerMap:
    """Build thin Flask handlers that delegate to the assistant pipeline."""

    def _invoke(service_fn) -> Response:
        try:
            result = service_fn(
                deps,
                user=deps.current_user(),
                payload=load_json_object(),
            )
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    def assistant_rag_mcp() -> Response:
        return _invoke(assistant_service.assistant_rag_mcp)

    def assistant_requirements() -> Response:
        return _invoke(assistant_service.assistant_requirements)

    def assistant_form_fill() -> Response:
        return _invoke(assistant_service.assistant_form_fill)

    def playground_plan() -> Response:
        return _invoke(assistant_service.playground_plan)

    def execution_plan() -> Response:
        return _invoke(assistant_service.execution_plan)

    def assistant_onboarding_plan() -> Response:
        return _invoke(assistant_service.assistant_onboarding_plan)

    def _dispatch_aaron(
        *,
        user: str,
        payload: Dict[str, Any],
        default_channel: str,
        wake_required_default: bool,
    ) -> Tuple[Dict[str, Any], int]:
        wake_name = str(
            payload.get("wake_name")
            or payload.get("assistant_name")
            or os.getenv("REFINER_AARON_WAKE_NAME")
            or "Aaron"
        ).strip() or "Aaron"
        wake_value = payload.get("require_wake_word")
        if wake_value is None:
            wake_value = payload.get("wake_required")
        wake_required = _coerce_bool(wake_value) if wake_value is not None else wake_required_default
        channel_source = (
            payload.get("channel")
            or payload.get("source")
            or payload.get("platform")
            or payload.get("origin")
            or default_channel
        )
        channel_name = normalise_channel_context({"channel": channel_source}).get("name") or "web"
        text_value = (
            payload.get("prompt")
            or payload.get("text")
            or payload.get("input")
            or payload.get("input_text")
            or payload.get("message")
            or payload.get("query")
            or ""
        )
        wake_detected, stripped_prompt = _strip_wake_prefix(str(text_value or ""), wake_name)
        if wake_required and not wake_detected:
            reminder = f"Say {wake_name} before your request."
            return (
                {
                    "assistant_name": "Aaron",
                    "wake_word": wake_name,
                    "wake_word_detected": False,
                    "channel": channel_name,
                    "workflow": _resolve_workflow(payload),
                    "reply": reminder,
                    "response": {"reply": reminder},
                },
                200,
            )
        if wake_detected and not stripped_prompt:
            listening = "I am listening. What do you need?"
            return (
                {
                    "assistant_name": "Aaron",
                    "wake_word": wake_name,
                    "wake_word_detected": True,
                    "channel": channel_name,
                    "workflow": _resolve_workflow(payload),
                    "reply": listening,
                    "response": {"reply": listening},
                },
                200,
            )
        dedupe_decision = claim_first_arrival(
            owner=user,
            prompt=stripped_prompt or text_value,
            channel=channel_name,
        )
        if dedupe_decision.get("suppressed"):
            winner_channel = str(dedupe_decision.get("winner_channel") or "").strip() or "another"
            response_payload = {
                "reply": "",
                "delivery_suppressed": True,
                "suppression_reason": str(dedupe_decision.get("reason") or "duplicate_cross_channel"),
                "first_channel": winner_channel,
            }
            return (
                {
                    "assistant_name": "Aaron",
                    "wake_word": wake_name,
                    "wake_word_detected": bool(wake_detected),
                    "channel": channel_name,
                    "workflow": _resolve_workflow(payload),
                    "reply": "",
                    "delivery_suppressed": True,
                    "first_channel": winner_channel,
                    "response": response_payload,
                },
                200,
            )

        request_payload = dict(payload or {})
        request_payload["channel"] = channel_name
        if stripped_prompt:
            request_payload["prompt"] = stripped_prompt
        channel_context = request_payload.get("channel_context")
        context = dict(channel_context) if isinstance(channel_context, dict) else {}
        context.setdefault("name", channel_name)
        request_payload["channel_context"] = context
        if not request_payload.get("assistant_profile") and request_payload.get("profile"):
            request_payload["assistant_profile"] = request_payload.get("profile")

        workflow = _resolve_workflow(request_payload)
        if workflow == "assistant_rag_mcp":
            service_fn = assistant_service.assistant_rag_mcp
        elif workflow == "assistant_form_fill":
            service_fn = assistant_service.assistant_form_fill
        elif workflow == "playground_plan":
            service_fn = assistant_service.playground_plan
        elif workflow == "execution_plan":
            service_fn = assistant_service.execution_plan
        else:
            service_fn = assistant_service.assistant_requirements
            request_payload.setdefault("mode", str(request_payload.get("mode") or "ask").strip().lower() or "ask")

        try:
            result = service_fn(deps, user=user, payload=request_payload)
        except ServiceError as exc:
            return exc.to_payload(), exc.status_code

        response_payload = dict(result.payload or {})
        reply = _response_text(response_payload)
        envelope = {
            "assistant_name": "Aaron",
            "wake_word": wake_name,
            "wake_word_detected": bool(wake_detected),
            "channel": channel_name,
            "workflow": workflow,
            "reply": reply,
            "response": response_payload,
        }
        if channel_name == "alexa" and reply:
            envelope["alexa_response"] = {
                "version": "1.0",
                "response": {"outputSpeech": {"type": "PlainText", "text": reply}, "shouldEndSession": True},
            }
        if channel_name in {"google_home", "google_assistant"} and reply:
            envelope["google_response"] = {"fulfillmentText": reply}
        if channel_name == "siri" and reply:
            envelope["siri_response"] = {"text": reply}
        return envelope, result.status_code

    def assistant_aaron_respond() -> Response:
        payload = load_json_object()
        user = deps.current_user()
        if not user:
            return _json_response({"error": "unauthorized"}, 401)
        envelope, status_code = _dispatch_aaron(
            user=user,
            payload=payload,
            default_channel="web",
            wake_required_default=False,
        )
        return _json_response(envelope, status_code)

    def assistant_telegram_webhook() -> Response:
        payload = load_json_object()
        expected_secret = str(os.getenv("REFINER_TELEGRAM_WEBHOOK_SECRET") or "").strip()
        if expected_secret:
            provided_secret = str(request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
            if provided_secret != expected_secret:
                return _json_response({"error": "unauthorized"}, 401)
        text, chat_id, sender_id = _text_from_telegram_update(payload)
        if not text:
            return _json_response(
                {
                    "status": "ignored",
                    "provider": "telegram",
                    "reason": "no_message_text",
                    "update_id": payload.get("update_id"),
                },
                200,
            )
        owner = f"telegram:{chat_id or sender_id or 'unknown'}"
        request_payload: Dict[str, Any] = {
            "text": text,
            "channel": "telegram",
            "wake_name": os.getenv("REFINER_AARON_WAKE_NAME") or "Aaron",
            "require_wake_word": _default_wake_required(),
        }
        workflow = str(request.args.get("workflow") or "").strip()
        profile = str(request.args.get("assistant_profile") or request.args.get("profile") or "").strip()
        if workflow:
            request_payload["workflow"] = workflow
        if profile:
            request_payload["assistant_profile"] = profile
        envelope, status_code = _dispatch_aaron(
            user=owner,
            payload=request_payload,
            default_channel="telegram",
            wake_required_default=_default_wake_required(),
        )
        response_payload: Dict[str, Any] = {
            "status": "ok",
            "provider": "telegram",
            "update_id": payload.get("update_id"),
            "chat_id": chat_id,
            "assistant": envelope,
        }
        reply = str(envelope.get("reply") or "").strip()
        if chat_id and reply:
            response_payload["telegram_response"] = {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": reply,
            }
        return _json_response(response_payload, status_code)

    def assistant_whatsapp_webhook() -> Response:
        if request.method == "GET":
            mode = str(request.args.get("hub.mode") or "").strip().lower()
            challenge = str(request.args.get("hub.challenge") or "")
            verify_token = str(request.args.get("hub.verify_token") or "").strip()
            expected_token = str(os.getenv("REFINER_WHATSAPP_VERIFY_TOKEN") or "").strip()
            if mode == "subscribe" and expected_token and verify_token == expected_token:
                return Response(challenge, mimetype="text/plain")
            return _json_response({"error": "verification_failed"}, 403)

        payload = load_json_object()
        text, from_number, phone_number_id, message_id = _text_from_whatsapp_update(payload)
        if not text:
            return _json_response(
                {
                    "status": "ignored",
                    "provider": "whatsapp",
                    "reason": "no_message_text",
                },
                200,
            )
        owner = f"whatsapp:{from_number}" if from_number else "whatsapp:unknown"
        request_payload: Dict[str, Any] = {
            "text": text,
            "channel": "whatsapp",
            "wake_name": os.getenv("REFINER_AARON_WAKE_NAME") or "Aaron",
            "require_wake_word": _default_wake_required(),
        }
        workflow = str(request.args.get("workflow") or "").strip()
        profile = str(request.args.get("assistant_profile") or request.args.get("profile") or "").strip()
        if workflow:
            request_payload["workflow"] = workflow
        if profile:
            request_payload["assistant_profile"] = profile
        envelope, status_code = _dispatch_aaron(
            user=owner,
            payload=request_payload,
            default_channel="whatsapp",
            wake_required_default=_default_wake_required(),
        )
        response_payload: Dict[str, Any] = {
            "status": "ok",
            "provider": "whatsapp",
            "from": from_number,
            "phone_number_id": phone_number_id,
            "message_id": message_id,
            "assistant": envelope,
        }
        reply = str(envelope.get("reply") or "").strip()
        if from_number and reply:
            response_payload["whatsapp_response"] = {
                "messaging_product": "whatsapp",
                "to": from_number,
                "type": "text",
                "text": {"body": reply},
            }
            if phone_number_id:
                response_payload["whatsapp_response"]["phone_number_id"] = phone_number_id
        return _json_response(response_payload, status_code)

    assistant_rag_mcp.__name__ = "assistant_rag_mcp"
    assistant_requirements.__name__ = "assistant_requirements"
    assistant_form_fill.__name__ = "assistant_form_fill"
    playground_plan.__name__ = "playground_plan"
    execution_plan.__name__ = "execution_plan"
    assistant_onboarding_plan.__name__ = "assistant_onboarding_plan"
    assistant_aaron_respond.__name__ = "assistant_aaron_respond"
    assistant_telegram_webhook.__name__ = "assistant_telegram_webhook"
    assistant_whatsapp_webhook.__name__ = "assistant_whatsapp_webhook"

    return {
        "assistant_rag_mcp": assistant_rag_mcp,
        "assistant_requirements": assistant_requirements,
        "assistant_form_fill": assistant_form_fill,
        "playground_plan": playground_plan,
        "execution_plan": execution_plan,
        "assistant_onboarding_plan": assistant_onboarding_plan,
        "assistant_aaron_respond": assistant_aaron_respond,
        "assistant_telegram_webhook": assistant_telegram_webhook,
        "assistant_whatsapp_webhook": assistant_whatsapp_webhook,
    }
