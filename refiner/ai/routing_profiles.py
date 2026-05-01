"""Shared AI routing-profile contract loader for Refiner and Gail.

Refiner keeps a repo-local copy for offline/local fallback, while Gail owns the
same JSON contract for the shared runtime. The schema stays deliberately simple
so Rust and Python can consume it without extra dependencies.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Set

_CONTRACT_CACHE: Dict[tuple[str, int, int], Dict[str, Any]] = {}
_CONTRACT_LOCK = threading.Lock()


def _repo_default_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "ai-routing-profiles.json"


def resolve_routing_profiles_path(explicit_path: Optional[str] = None) -> str:
    candidates = [
        explicit_path,
        os.getenv("REFINER_AI_ROUTING_PROFILES_PATH"),
        str(_repo_default_path()),
    ]
    for raw in candidates:
        cleaned = str(raw or "").strip()
        if not cleaned:
            continue
        candidate = Path(cleaned).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
    raise FileNotFoundError("no Refiner AI routing-profiles contract file could be resolved")


def _normalized_strings(values: Iterable[Any]) -> Set[str]:
    return {
        str(value).strip().lower()
        for value in values
        if str(value).strip()
    }


def _normalize_role_profiles(value: Any) -> Dict[str, Set[str]]:
    if not isinstance(value, Mapping):
        return {}
    normalized: Dict[str, Set[str]] = {}
    for role, tags in value.items():
        role_key = str(role).strip().lower() or "general"
        if isinstance(tags, str):
            tags = [tags]
        if isinstance(tags, Sequence):
            normalized[role_key] = _normalized_strings(tags)
    return normalized


def _normalize_mapping(value: Any) -> Dict[str, Set[str]]:
    if not isinstance(value, Mapping):
        return {}
    normalized: Dict[str, Set[str]] = {}
    for key, items in value.items():
        name = str(key).strip().lower()
        if not name:
            continue
        if isinstance(items, str):
            items = [items]
        if isinstance(items, Sequence):
            normalized[name] = _normalized_strings(items)
    return normalized


def load_routing_profiles(explicit_path: Optional[str] = None) -> Dict[str, Any]:
    path = resolve_routing_profiles_path(explicit_path)
    stat = os.stat(path)
    cache_key = (path, int(stat.st_mtime_ns), int(stat.st_size))
    with _CONTRACT_LOCK:
        cached = _CONTRACT_CACHE.get(cache_key)
        if cached is not None:
            return cached

    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, MutableMapping):
        raw = {}

    workflow_profiles: Dict[str, Dict[str, Set[str]]] = {}
    workflows = raw.get("workflow_profiles") if isinstance(raw.get("workflow_profiles"), Mapping) else {}
    for workflow, role_profiles in workflows.items():
        workflow_key = str(workflow).strip().lower()
        if not workflow_key:
            continue
        workflow_profiles[workflow_key] = _normalize_role_profiles(role_profiles)

    contract = {
        "version": int(raw.get("version") or 1),
        "path": path,
        "workflow_profiles": workflow_profiles,
        "keyword_tags": _normalize_mapping(raw.get("keyword_tags")),
        "provider_specialties": _normalize_mapping(raw.get("provider_specialties")),
    }
    with _CONTRACT_LOCK:
        _CONTRACT_CACHE.clear()
        _CONTRACT_CACHE[cache_key] = contract
    return contract


def workflow_tags(workflow: str, role: str, text: str, *, explicit_path: Optional[str] = None) -> Set[str]:
    contract = load_routing_profiles(explicit_path)
    tags: Set[str] = set()
    workflow_key = str(workflow or "").strip().lower()
    role_key = str(role or "").strip().lower() or "general"
    profile = contract["workflow_profiles"].get(workflow_key, {})
    tags.update(profile.get("general", set()))
    tags.update(profile.get(role_key, set()))
    lowered = str(text or "").lower()
    for tag, keywords in contract["keyword_tags"].items():
        if any(keyword in lowered for keyword in keywords):
            tags.add(tag)
    return tags


def base_provider_specialties(provider_type: str, *, explicit_path: Optional[str] = None) -> Set[str]:
    contract = load_routing_profiles(explicit_path)
    return set(contract["provider_specialties"].get(str(provider_type or "").strip().lower(), set()))
