"""First-arrival channel dedupe for near-simultaneous Aaron requests."""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set


_DEFAULT_WINDOW_SEC = 6.0
_MIN_WINDOW_SEC = 0.5
_MAX_WINDOW_SEC = 30.0
_DEFAULT_CHANNELS = {
    "alexa",
    "google_assistant",
    "google_home",
    "messenger",
    "siri",
    "telegram",
    "voice",
    "voice_capture",
    "whatsapp",
}
_WHITESPACE_RE = re.compile(r"\s+")
_PROMPT_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class _Claim:
    channel: str
    claimed_at: float
    expires_at: float


_CLAIM_LOCK = threading.RLock()
_CLAIMS: Dict[str, _Claim] = {}


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_window(value: Any) -> float:
    try:
        window = float(value)
    except (TypeError, ValueError):
        window = _DEFAULT_WINDOW_SEC
    if window < _MIN_WINDOW_SEC:
        return _MIN_WINDOW_SEC
    if window > _MAX_WINDOW_SEC:
        return _MAX_WINDOW_SEC
    return window


def _normalise_prompt(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned:
        return ""
    cleaned = _PROMPT_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned[:320]


def _normalise_owner(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned:
        return ""
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned[:160]


def _normalise_channel(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _parse_channel_set(value: Any) -> Set[str]:
    if value is None:
        return set(_DEFAULT_CHANNELS)
    raw = str(value).strip()
    if not raw:
        return set(_DEFAULT_CHANNELS)
    channels: Set[str] = set()
    for item in raw.split(","):
        cleaned = _normalise_channel(item)
        if cleaned:
            channels.add(cleaned)
    if not channels:
        return set(_DEFAULT_CHANNELS)
    return channels


def _enabled() -> bool:
    return _coerce_bool(os.getenv("REFINER_AARON_FIRST_ARRIVAL_ENABLED"), True)


def _window_sec() -> float:
    return _coerce_window(os.getenv("REFINER_AARON_FIRST_ARRIVAL_WINDOW_SEC"))


def _dedupe_channels() -> Set[str]:
    return _parse_channel_set(os.getenv("REFINER_AARON_FIRST_ARRIVAL_CHANNELS"))


def _is_dedupe_channel(channel: str) -> bool:
    return channel in _dedupe_channels()


def _claim_key(owner: str, prompt: str) -> str:
    token = f"{owner}\n{prompt}".encode("utf-8")
    return hashlib.sha256(token).hexdigest()


def _prune_expired(now_ts: float) -> None:
    expired = [key for key, claim in _CLAIMS.items() if claim.expires_at <= now_ts]
    for key in expired:
        _CLAIMS.pop(key, None)


def claim_first_arrival(
    *,
    owner: Any,
    prompt: Any,
    channel: Any,
) -> Dict[str, Any]:
    """Return a suppression decision for near-simultaneous cross-channel requests."""

    channel_name = _normalise_channel(channel) or "web"
    owner_name = _normalise_owner(owner)
    prompt_text = _normalise_prompt(prompt)
    if not _enabled():
        return {"suppressed": False, "channel": channel_name, "reason": "disabled"}
    if not _is_dedupe_channel(channel_name):
        return {"suppressed": False, "channel": channel_name, "reason": "channel_not_deduped"}
    if not owner_name or not prompt_text:
        return {"suppressed": False, "channel": channel_name, "reason": "insufficient_identity"}

    now_ts = time.time()
    expires_at = now_ts + _window_sec()
    key = _claim_key(owner_name, prompt_text)
    with _CLAIM_LOCK:
        _prune_expired(now_ts)
        existing = _CLAIMS.get(key)
        if existing and existing.expires_at > now_ts:
            if existing.channel != channel_name:
                return {
                    "suppressed": True,
                    "channel": channel_name,
                    "winner_channel": existing.channel,
                    "first_seen_at": existing.claimed_at,
                    "reason": "duplicate_cross_channel",
                }
            existing.expires_at = max(existing.expires_at, expires_at)
            _CLAIMS[key] = existing
            return {
                "suppressed": False,
                "channel": channel_name,
                "winner_channel": existing.channel,
                "first_seen_at": existing.claimed_at,
                "reason": "same_channel_repeat",
            }

        _CLAIMS[key] = _Claim(channel=channel_name, claimed_at=now_ts, expires_at=expires_at)
        return {
            "suppressed": False,
            "channel": channel_name,
            "winner_channel": channel_name,
            "first_seen_at": now_ts,
            "reason": "first_arrival",
        }


def reset_first_arrival_claims_for_tests() -> None:
    """Clear in-memory dedupe claims for deterministic tests."""

    with _CLAIM_LOCK:
        _CLAIMS.clear()

