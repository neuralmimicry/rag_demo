"""Channel/persona helpers for assistant-facing conversational flows."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

_CHANNELS = {
    "alexa",
    "api",
    "google_assistant",
    "google_home",
    "instagram",
    "linkedin",
    "messenger",
    "mobile",
    "siri",
    "sms",
    "telegram",
    "web",
    "whatsapp",
}
_DEFAULT_CHANNEL = "web"

_CHANNEL_PROMPT_HINTS: Dict[str, str] = {
    "alexa": "The user is on Alexa voice. Keep the reply short, natural, and easy to speak aloud.",
    "api": "The caller is an API integration. Keep output deterministic and concise.",
    "google_assistant": "The user is on Google Assistant voice. Keep the reply short, natural, and easy to speak aloud.",
    "google_home": "The user is on Google Home voice. Keep the reply short, natural, and easy to speak aloud.",
    "instagram": "The user is on Instagram. Keep the reply compact, skimmable, and avoid dense formatting.",
    "linkedin": "The user is on LinkedIn. Keep a professional business tone with direct wording.",
    "messenger": "The user is on Messenger. Use short chat paragraphs and avoid heavy formatting.",
    "mobile": "The user is on mobile. Keep each paragraph short and avoid long blocks of text.",
    "siri": "The user is on Siri voice. Keep the reply short, natural, and easy to speak aloud.",
    "sms": "The user is on SMS. Keep the message very short and plain text only.",
    "telegram": "The user is on Telegram. Keep replies concise and easy to scan in chat.",
    "web": "The user is on web chat. You may use short bullet lists when they improve clarity.",
    "whatsapp": "The user is on WhatsApp. Keep output brief, plain, and easy to scan.",
}

_PERSONA_PRESETS: Dict[str, Dict[str, Any]] = {
    "requirements": {
        "label": "Requirements Assistant",
        "tone": "clear, structured, and pragmatic",
        "style": "focus on testable outcomes and concise clarifications",
        "goal": "capture verifiable requirements and acceptance criteria",
        "constraints": [
            "Avoid speculative claims.",
            "Prefer explicit requirements over broad narrative text.",
        ],
    },
    "marketing": {
        "label": "Marketing Assistant",
        "tone": "business-oriented and concise",
        "style": "highlight practical value and avoid hype",
        "goal": "answer commercial questions about products and services",
        "constraints": [
            "Do not invent product capabilities.",
            "Use measured, factual wording.",
        ],
    },
    "support": {
        "label": "Support Assistant",
        "tone": "calm and direct",
        "style": "step-by-step troubleshooting and next actions",
        "goal": "help resolve issues quickly and safely",
        "constraints": [
            "State missing diagnostics clearly.",
            "Escalate to handoff when the user asks for a human agent.",
        ],
    },
    "sales": {
        "label": "Sales Assistant",
        "tone": "professional and value-focused",
        "style": "qualify needs, then propose fit-for-purpose options",
        "goal": "progress qualified interest towards a concrete next step",
        "constraints": [
            "Do not promise unavailable timelines or integrations.",
            "Keep claims grounded in known product capability.",
        ],
    },
    "onboarding": {
        "label": "Onboarding Assistant",
        "tone": "supportive and practical",
        "style": "short checklists with clear progression",
        "goal": "help users complete setup quickly and correctly",
        "constraints": [
            "Prioritise the next actionable step.",
            "Avoid unnecessary detail when the user is still setting up.",
        ],
    },
    "technical": {
        "label": "Technical Assistant",
        "tone": "precise and engineering-focused",
        "style": "explicit assumptions, caveats, and implementation detail",
        "goal": "provide technically correct and testable guidance",
        "constraints": [
            "Prefer exact behaviour over broad summaries.",
            "Surface risks and edge-cases explicitly.",
        ],
    },
    "admin_ops": {
        "label": "Aaron — Operator",
        "tone": "engaged, conversational, and technically fluent",
        "style": (
            "think aloud as a knowledgeable colleague; weave live infrastructure context naturally into replies; "
            "lead with the most operationally relevant observation, then invite the operator to dig deeper; "
            "use short paragraphs rather than bullet lists unless listing discrete items; "
            "refer to services by name (Refiner, AARNN, Gail, Conductor, Continuum, Tracey) and to physical nodes "
            "(vega, spirit, qc01) when they are relevant"
        ),
        "goal": (
            "be a proactive infrastructure companion — surface what is happening across the estate, "
            "flag anomalies, and help the operator decide what to do next"
        ),
        "constraints": [
            "Never invent metric values; if live data is absent, say so explicitly.",
            "Keep sensitive operational detail (tokens, credentials, internal IPs) out of responses.",
            "Escalate urgent fault conditions clearly before offering analysis.",
            "Match verbosity to the question: a quick status check gets a quick answer; a deep-dive gets depth.",
        ],
    },
}

_POSITIVE_TERMS = {
    "great",
    "helpful",
    "resolved",
    "fixed",
    "success",
    "successful",
    "clear",
    "excellent",
    "thanks",
    "thank",
}
_NEGATIVE_TERMS = {
    "blocked",
    "failure",
    "failed",
    "error",
    "issue",
    "problem",
    "cannot",
    "can't",
    "worse",
    "frustrating",
}


def _clean_text(value: Any, *, max_length: int = 240) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        return ""
    if len(cleaned) > max_length:
        return cleaned[:max_length]
    return cleaned


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _clean_channel_name(value: Any) -> str:
    cleaned = _clean_text(value, max_length=32).lower().replace("-", "_").replace(" ", "_")
    if cleaned in _CHANNELS:
        return cleaned
    aliases = {
        "amazon_alexa": "alexa",
        "assistant": "google_assistant",
        "fb_messenger": "messenger",
        "facebook_messenger": "messenger",
        "ga": "google_assistant",
        "ghome": "google_home",
        "gassistant": "google_assistant",
        "google": "google_home",
        "googleassistant": "google_assistant",
        "google_assist": "google_assistant",
        "googlehome": "google_home",
        "home": "google_home",
        "ig": "instagram",
        "li": "linkedin",
        "telegram_bot": "telegram",
        "tg": "telegram",
        "text": "sms",
        "voice_alexa": "alexa",
        "voice_google": "google_home",
        "voice_siri": "siri",
        "website": "web",
    }
    return aliases.get(cleaned, _DEFAULT_CHANNEL)


def _clean_profile_id(value: Any) -> str:
    cleaned = _clean_text(value, max_length=64).lower().replace("-", "_")
    if not cleaned:
        return "requirements"
    aliases = {
        "default": "requirements",
        "assistant": "requirements",
        "nm_marketing": "marketing",
        "neuralmimicry_marketing": "marketing",
        "requirements_assistant": "requirements",
        "tech": "technical",
    }
    return aliases.get(cleaned, cleaned)


def _normalise_constraints(value: Any) -> List[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif value:
        items = [value]
    else:
        items = []
    constraints: List[str] = []
    for item in items:
        cleaned = _clean_text(item, max_length=280)
        if not cleaned:
            continue
        if cleaned in constraints:
            continue
        constraints.append(cleaned)
        if len(constraints) >= 12:
            break
    return constraints


def normalise_channel_context(payload: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Return a stable omni-channel context map from request payload variants."""

    values = dict(payload or {})
    merged: Dict[str, Any] = {}
    channel_block = values.get("channel")
    if isinstance(channel_block, dict):
        merged.update(channel_block)
    context_block = values.get("channel_context")
    if isinstance(context_block, dict):
        merged.update(context_block)
    if isinstance(channel_block, str) and channel_block.strip():
        merged.setdefault("name", channel_block)

    name = _clean_channel_name(
        merged.get("name")
        or values.get("channel")
        or values.get("deployment_channel")
        or values.get("channel_name")
    )
    handoff_requested = _coerce_bool(
        merged.get("handoff_requested")
        or merged.get("handoff")
        or values.get("handoff_requested")
    )
    handoff_reason = _clean_text(
        merged.get("handoff_reason") or values.get("handoff_reason"),
        max_length=320,
    )
    external_user_id = _clean_text(
        merged.get("external_user_id") or merged.get("user_id") or values.get("external_user_id"),
        max_length=160,
    )
    external_conversation_id = _clean_text(
        merged.get("external_conversation_id")
        or merged.get("conversation_id")
        or values.get("external_conversation_id"),
        max_length=160,
    )
    session_id = _clean_text(
        merged.get("session_id") or values.get("session_id"),
        max_length=160,
    )
    deployment_id = _clean_text(
        merged.get("deployment_id") or values.get("deployment_id"),
        max_length=160,
    )
    return {
        "name": name,
        "external_user_id": external_user_id,
        "external_conversation_id": external_conversation_id,
        "session_id": session_id,
        "deployment_id": deployment_id,
        "handoff_requested": handoff_requested,
        "handoff_reason": handoff_reason,
    }


def channel_prompt_guidance(channel_context: Mapping[str, Any] | None) -> str:
    """Return channel-specific prompting guidance for assistant responses."""

    context = dict(channel_context or {})
    name = _clean_channel_name(context.get("name"))
    hint = _CHANNEL_PROMPT_HINTS.get(name, _CHANNEL_PROMPT_HINTS[_DEFAULT_CHANNEL])
    if _coerce_bool(context.get("handoff_requested")):
        reason = _clean_text(context.get("handoff_reason"), max_length=240)
        if reason:
            return (
                f"{hint}\nThe user requested human handoff. Acknowledge this and include the handoff reason: {reason}."
            )
        return f"{hint}\nThe user requested human handoff. Acknowledge this and keep the transition clear."
    return hint


def resolve_assistant_persona(
    payload: Mapping[str, Any] | None,
    *,
    default_profile: str = "requirements",
) -> Dict[str, Any]:
    """Resolve assistant persona/tone config from preset and optional overrides."""

    values = dict(payload or {})
    profile_id = _clean_profile_id(values.get("assistant_profile") or values.get("assistant_persona") or default_profile)
    if profile_id not in _PERSONA_PRESETS:
        if profile_id == "requirements":
            profile_id = "requirements"
        else:
            profile_id = "technical"
    preset = dict(_PERSONA_PRESETS.get(profile_id) or _PERSONA_PRESETS["technical"])

    custom = values.get("assistant_profile_config")
    if not isinstance(custom, dict):
        custom = values.get("persona")
    if not isinstance(custom, dict):
        custom = {}

    label = _clean_text(custom.get("label") or preset.get("label"), max_length=96)
    tone = _clean_text(custom.get("tone") or preset.get("tone"), max_length=220)
    style = _clean_text(custom.get("style") or preset.get("style"), max_length=320)
    goal = _clean_text(custom.get("goal") or preset.get("goal"), max_length=320)
    constraints = _normalise_constraints(custom.get("constraints") or preset.get("constraints"))
    return {
        "id": profile_id,
        "label": label or profile_id,
        "tone": tone,
        "style": style,
        "goal": goal,
        "constraints": constraints,
    }


def persona_prompt_guidance(persona: Mapping[str, Any] | None) -> str:
    """Build deterministic persona instructions to append to system prompts."""

    values = dict(persona or {})
    lines: List[str] = []
    label = _clean_text(values.get("label"), max_length=96)
    tone = _clean_text(values.get("tone"), max_length=220)
    style = _clean_text(values.get("style"), max_length=320)
    goal = _clean_text(values.get("goal"), max_length=320)
    if label:
        lines.append(f"Persona profile: {label}.")
    if goal:
        lines.append(f"Primary objective: {goal}.")
    if tone:
        lines.append(f"Tone: {tone}.")
    if style:
        lines.append(f"Interaction style: {style}.")
    constraints = _normalise_constraints(values.get("constraints"))
    if constraints:
        lines.append("Operational constraints:")
        for item in constraints:
            lines.append(f"- {item}")
    return "\n".join(lines)


def infer_sentiment_label(text: str) -> str:
    """Return a lightweight sentiment label for analytics metadata."""

    cleaned = str(text or "").lower()
    tokens = re.findall(r"[a-z][a-z']*", cleaned)
    if not tokens:
        return "neutral"
    positive_hits = sum(1 for token in tokens if token in _POSITIVE_TERMS)
    negative_hits = sum(1 for token in tokens if token in _NEGATIVE_TERMS)
    delta = positive_hits - negative_hits
    if delta >= 2:
        return "positive"
    if delta <= -2:
        return "negative"
    return "neutral"


def derive_engagement_markers(
    *,
    payload: Mapping[str, Any] | None,
    channel_context: Mapping[str, Any] | None,
    reply_text: str,
    atlassian_result: Any = None,
    mcp_result: Any = None,
) -> Dict[str, Any]:
    """Derive handoff/conversion/sentiment markers for response metadata."""

    values = dict(payload or {})
    channel = dict(channel_context or {})
    handoff_requested = _coerce_bool(channel.get("handoff_requested") or values.get("handoff_requested"))
    handoff_reason = _clean_text(channel.get("handoff_reason") or values.get("handoff_reason"), max_length=320)
    conversion_completed = _coerce_bool(
        values.get("conversion_completed")
        or values.get("goal_completed")
        or values.get("conversion_event")
    )
    if not conversion_completed and isinstance(atlassian_result, dict):
        status = _clean_text(atlassian_result.get("status"), max_length=64).lower()
        conversion_completed = status in {"applied", "completed", "success"}
    if not conversion_completed and isinstance(mcp_result, dict):
        conversion_completed = _coerce_bool(mcp_result.get("converted") or mcp_result.get("completed"))
    return {
        "sentiment_label": infer_sentiment_label(reply_text),
        "handoff_requested": handoff_requested,
        "handoff_reason": handoff_reason,
        "conversion_completed": conversion_completed,
    }


def channel_response_payload(channel_context: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Build a response-safe channel payload."""

    raw_context = dict(channel_context or {})
    context = raw_context if raw_context.get("name") else normalise_channel_context(raw_context)
    name = _clean_channel_name(context.get("name"))
    payload = {
        "name": name,
        "handoff_requested": bool(context.get("handoff_requested")),
    }
    for key in ("external_user_id", "external_conversation_id", "session_id", "deployment_id", "handoff_reason"):
        value = _clean_text(context.get(key), max_length=320)
        if value:
            payload[key] = value
    return payload


def assistant_experience_response_meta(
    *,
    channel_context: Mapping[str, Any] | None,
    persona: Mapping[str, Any] | None,
    markers: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Return compact analytics metadata fields for trace response payloads."""

    raw_context = dict(channel_context or {})
    channel = raw_context if raw_context.get("name") else normalise_channel_context(raw_context)
    channel_name = _clean_channel_name(channel.get("name"))
    profile_id = _clean_profile_id((persona or {}).get("id") or "requirements")
    marker_values = dict(markers or {})
    return {
        "channel": channel_name,
        "assistant_profile": profile_id,
        "handoff_requested": bool(marker_values.get("handoff_requested")),
        "conversion_completed": bool(marker_values.get("conversion_completed")),
        "sentiment_label": _clean_text(marker_values.get("sentiment_label"), max_length=24) or "neutral",
    }
