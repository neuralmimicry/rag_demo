"""Shared helpers for dict or object retrieval match payloads."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Mapping


def match_field(item: Any, name: str, default: Any = None) -> Any:
    """Return a named field from a dict-like or object payload."""

    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def match_metadata(item: Any) -> Dict[str, Any]:
    """Return a copy of match metadata when available."""

    value = match_field(item, "metadata", {})
    return dict(value) if isinstance(value, dict) else {}


def match_citation(item: Any) -> str:
    """Return the preferred citation label for a match payload."""

    citation = str(match_field(item, "citation", "") or match_metadata(item).get("citation") or "").strip()
    return citation or str(match_field(item, "source", "") or "source")


def match_text(item: Any) -> str:
    """Return match text consistently."""

    return str(match_field(item, "text", "") or "")


def match_chunk_id(item: Any) -> str:
    """Return the retrieval chunk identifier consistently."""

    return str(match_field(item, "chunk_id", "") or "").strip()


def match_score(item: Any) -> float:
    """Return the numeric score for a match payload."""

    try:
        return float(match_field(item, "score", 0.0) or 0.0)
    except Exception:
        return 0.0


def clone_match(item: Any, *, score: float) -> Any:
    """Return a copy of a retrieval match with an updated score."""

    payload = {
        "chunk_id": match_chunk_id(item),
        "source": str(match_field(item, "source", "") or "").strip(),
        "score": float(score),
        "text": match_text(item),
        "metadata": match_metadata(item),
        "citation": match_citation(item),
    }
    if isinstance(item, Mapping):
        cloned = dict(item)
        cloned.update(payload)
        return cloned
    match_type = getattr(item, "__class__", None)
    if match_type is not None:
        try:
            return match_type(**payload)
        except Exception:
            pass
    return SimpleNamespace(**payload)
