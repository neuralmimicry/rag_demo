"""Assistant episodic memory helpers.

These helpers keep the existing file-backed assistant memory behaviour stable
while allowing the extracted assistant pipeline to reuse the same logic outside
`refiner_web.py`.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from refiner.solver_memory import SolverEpisode, SolverEpisodeStore


EpisodeStoreFactory = Callable[[str], SolverEpisodeStore]
CentralEpisodeStoreGetter = Callable[[], Optional[Any]]


def _debug(logger: Any, message: str, *args: Any) -> None:
    if logger is None:
        return
    try:
        logger.debug(message, *args)
    except Exception:
        pass


def assistant_memory_scope(route: str, *, mode: str = "", profile: str = "") -> str:
    """Build a stable assistant memory scope key."""

    parts = [str(route or "").strip().lower()]
    if mode:
        parts.append(str(mode).strip().lower())
    if profile:
        parts.append(str(profile).strip().lower())
    return ":".join(part for part in parts if part)


def assistant_memory_query_text(
    *,
    prompt: str = "",
    requirements_text: str = "",
    messages: Optional[List[Dict[str, Any]]] = None,
    extra_parts: Optional[List[str]] = None,
) -> str:
    """Build a compact lexical query over the current assistant request."""

    blocks: List[str] = []
    for part in [prompt, requirements_text] + list(extra_parts or []):
        cleaned = str(part or "").strip()
        if cleaned:
            blocks.append(cleaned)
    for msg in (messages or [])[-6:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        content = str(msg.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            blocks.append(f"{role}: {content}")
    return "\n\n".join(blocks).strip()


def assistant_memory_matches(
    user: str,
    *,
    scope: str,
    query_text: str,
    limit: int,
    episode_store_for_user: EpisodeStoreFactory,
    get_central_store: CentralEpisodeStoreGetter,
    logger: Any,
) -> List[SolverEpisode]:
    """Prefer same-scope memories, then fall back to the user's wider history."""

    store = episode_store_for_user(user)
    central_store = get_central_store()
    if query_text:
        matches = store.search(query_text, source_path=scope, limit=limit)
        if matches:
            return matches
        if central_store is not None:
            try:
                matches = central_store.search(user, query_text, source_path=scope, limit=limit)
                if matches:
                    return matches
            except Exception as exc:
                _debug(logger, "Assistant memory central lookup failed: %s", exc)
        matches = store.search(query_text, limit=limit)
        if matches:
            return matches
        if central_store is not None:
            try:
                return central_store.search(user, query_text, limit=limit)
            except Exception as exc:
                _debug(logger, "Assistant memory central fallback failed: %s", exc)
        return []
    matches = store.recent(source_path=scope, limit=limit)
    if matches:
        return matches
    if central_store is not None:
        try:
            matches = central_store.recent(user, source_path=scope, limit=limit)
            if matches:
                return matches
        except Exception as exc:
            _debug(logger, "Assistant memory central recent lookup failed: %s", exc)
    matches = store.recent(limit=limit)
    if matches:
        return matches
    if central_store is not None:
        try:
            return central_store.recent(user, limit=limit)
        except Exception as exc:
            _debug(logger, "Assistant memory central recent fallback failed: %s", exc)
    return []


def assistant_memory_entry_line(entry: SolverEpisode) -> str:
    """Render one compact assistant memory line for prompt injection."""

    bits: List[str] = []
    summary = str(entry.summary or "").strip()
    if summary:
        bits.append(summary)
    if entry.requirement_ids:
        bits.append("requirements=" + ", ".join(entry.requirement_ids[:4]))
    context_notes = [note for note in entry.notes[:2] if isinstance(note, str) and note.strip()]
    if context_notes:
        bits.append("context=" + "; ".join(context_notes))
    return ". ".join(bit for bit in bits if bit).strip()


def assistant_memory_prompt_block(
    user: str,
    *,
    scope: str,
    query_text: str,
    header: str,
    episode_store_for_user: EpisodeStoreFactory,
    get_central_store: CentralEpisodeStoreGetter,
    logger: Any,
    limit: int = 3,
    max_chars: int = 1400,
) -> str:
    """Render retrieved episodic memory as a bounded prompt block."""

    try:
        matches = assistant_memory_matches(
            user,
            scope=scope,
            query_text=query_text,
            limit=limit,
            episode_store_for_user=episode_store_for_user,
            get_central_store=get_central_store,
            logger=logger,
        )
    except Exception as exc:
        _debug(logger, "Assistant memory lookup failed: %s", exc)
        return ""
    if not matches:
        return ""
    lines = [header]
    for entry in matches:
        line = assistant_memory_entry_line(entry)
        if line:
            lines.append(f"- {line}")
    text = "\n".join(lines).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "...(truncated)"


def assistant_memory_reference_payload(
    user: str,
    *,
    scope: str,
    query_text: str,
    episode_store_for_user: EpisodeStoreFactory,
    get_central_store: CentralEpisodeStoreGetter,
    logger: Any,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Render retrieved episodic memory as compact structured references."""

    try:
        matches = assistant_memory_matches(
            user,
            scope=scope,
            query_text=query_text,
            limit=limit,
            episode_store_for_user=episode_store_for_user,
            get_central_store=get_central_store,
            logger=logger,
        )
    except Exception as exc:
        _debug(logger, "Assistant memory lookup failed: %s", exc)
        return []
    payload: List[Dict[str, Any]] = []
    for entry in matches:
        item: Dict[str, Any] = {
            "summary": str(entry.summary or "").strip(),
        }
        if entry.requirement_ids:
            item["requirement_ids"] = list(entry.requirement_ids[:6])
        if entry.notes:
            item["notes"] = [str(note).strip() for note in entry.notes[:2] if str(note).strip()]
        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
        project_name = str(metadata.get("project_name") or "").strip()
        if project_name:
            item["project_name"] = project_name
        workflow = str(metadata.get("workflow") or "").strip()
        if workflow:
            item["workflow"] = workflow
        scope_name = str(metadata.get("scope") or "").strip()
        if scope_name:
            item["scope"] = scope_name
        field_ids = metadata.get("field_ids")
        if isinstance(field_ids, list):
            item["field_ids"] = [str(field_id).strip() for field_id in field_ids[:8] if str(field_id).strip()]
        steps = metadata.get("steps")
        if isinstance(steps, list):
            item["steps"] = [str(step).strip() for step in steps[:4] if str(step).strip()]
        suggestions = metadata.get("suggestions")
        if isinstance(suggestions, list):
            cleaned_suggestions: List[Dict[str, Any]] = []
            for suggestion in suggestions[:4]:
                if not isinstance(suggestion, dict):
                    continue
                field_id = str(suggestion.get("field_id") or suggestion.get("id") or "").strip()
                value = suggestion.get("value")
                rationale = str(suggestion.get("rationale") or "").strip()
                if not field_id:
                    continue
                entry_payload: Dict[str, Any] = {"field_id": field_id}
                if value not in (None, ""):
                    entry_payload["value"] = value
                if rationale:
                    entry_payload["rationale"] = rationale[:160]
                cleaned_suggestions.append(entry_payload)
            if cleaned_suggestions:
                item["suggestions"] = cleaned_suggestions
        if item.get("summary") or item.get("notes") or item.get("steps") or item.get("suggestions"):
            payload.append(item)
    return payload


def assistant_memory_requirement_ids(text: str, limit: int = 12) -> List[str]:
    """Extract bounded requirement ids from assistant output."""

    seen = set()
    result: List[str] = []
    for match in re.finditer(r"\bREQ-\d{3,}\b", text or "", re.I):
        req_id = match.group(0).upper()
        if req_id in seen:
            continue
        seen.add(req_id)
        result.append(req_id)
        if len(result) >= limit:
            break
    return result


def assistant_memory_summary(text: str, *, fallback: str = "", max_chars: int = 260) -> str:
    """Create a bounded single-line episode summary."""

    source = text or fallback
    cleaned = re.sub(r"\s+", " ", str(source or "")).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 15].rstrip() + "...(truncated)"


def record_assistant_memory(
    user: str,
    *,
    scope: str,
    prompt_text: str,
    requirements_text: str,
    reply_text: str,
    episode_store_for_user: EpisodeStoreFactory,
    get_central_store: CentralEpisodeStoreGetter,
    logger: Any,
    project_name: str = "",
    steps: Optional[List[str]] = None,
    extra_notes: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a compact assistant episode without affecting request success."""

    if not user:
        return
    try:
        store = episode_store_for_user(user)
        recent = store.snapshot(source_path=scope, limit=1)
        iteration = (recent[-1].iteration if recent else 0) + 1
        notes: List[str] = []
        for raw in [
            f"prompt: {assistant_memory_summary(prompt_text, max_chars=180)}" if prompt_text else "",
            f"context: {assistant_memory_summary(requirements_text, max_chars=180)}" if requirements_text else "",
        ] + list(extra_notes or []):
            cleaned = str(raw or "").strip()
            if cleaned and cleaned not in notes:
                notes.append(cleaned)
        episode_metadata: Dict[str, Any] = {
            "project_name": assistant_memory_summary(project_name, max_chars=80) if project_name else "",
            "steps": [str(step).strip() for step in (steps or [])[:6] if str(step).strip()],
            "reply_chars": len(reply_text or ""),
        }
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                cleaned_key = str(key or "").strip()
                if not cleaned_key:
                    continue
                if value in ("", None, [], {}):
                    continue
                episode_metadata[cleaned_key] = value
        episode = SolverEpisode(
            episode_id=uuid.uuid4().hex,
            source_path=scope,
            iteration=iteration,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            outcome="success",
            summary=assistant_memory_summary(reply_text, fallback=prompt_text),
            requirement_ids=assistant_memory_requirement_ids(reply_text),
            notes=notes[:6],
            metadata=episode_metadata,
        )
        store.record(episode)
        central_store = get_central_store()
        if central_store is not None:
            try:
                central_store.record(user, episode)
            except Exception as exc:
                _debug(logger, "Assistant episodic memory central write skipped: %s", exc)
    except Exception as exc:
        _debug(logger, "Assistant episodic memory skipped: %s", exc)


def should_use_assistant_ask_memory(
    prompt: str,
    *,
    requirements_text: str,
    messages: Optional[List[Dict[str, Any]]],
    is_marketing_assistant: bool,
) -> bool:
    """Only add memory to ask flows when the request has enough reusable context."""

    if is_marketing_assistant:
        return False
    prompt_len = len((prompt or "").strip())
    context_len = len((requirements_text or "").strip())
    message_count = len(messages or [])
    return bool(prompt_len >= 80 or context_len >= 80 or message_count >= 3)
