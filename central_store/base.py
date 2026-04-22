"""Shared helpers for the incremental central-store split."""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

try:
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - exercised only when psycopg extras are unavailable
    Jsonb = None

UTC = dt.timezone.utc


def jsonb(value: Optional[Dict[str, Any]] = None) -> Any:
    payload = dict(value or {})
    if Jsonb is None:
        return payload
    return Jsonb(payload)


def timestamp(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def clamp_text(value: Any, *, default: str = "", max_length: int = 512) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip()
