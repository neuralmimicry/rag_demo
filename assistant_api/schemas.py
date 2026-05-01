"""Small request helpers for assistant and RAG route handlers."""

from __future__ import annotations

from typing import Any, Dict

from flask import request


def load_json_object() -> Dict[str, Any]:
    """Return a JSON object payload or an empty mapping when absent."""

    payload = request.get_json(force=True, silent=True)
    if isinstance(payload, dict):
        return payload
    return {}
