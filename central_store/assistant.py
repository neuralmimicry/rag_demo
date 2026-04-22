"""Postgres-backed assistant metadata stores.

These stores intentionally mirror the behaviour of the existing file-backed
assistant memory and add trace/conversation persistence without changing the
public Refiner API.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from refiner.solver_memory import SolverEpisode

from central_store.base import clamp_text, coerce_int, jsonb, timestamp

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{1,}")
_MAX_SEARCH_RESULTS = 6

ASSISTANT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS nm_assistant_conversations (
        conversation_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        route TEXT NOT NULL,
        scope TEXT,
        title TEXT,
        last_turn_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_turn_at TIMESTAMPTZ,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_conversations_owner_updated_idx
        ON nm_assistant_conversations (owner, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_conversations_route_updated_idx
        ON nm_assistant_conversations (route, updated_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_assistant_turns (
        turn_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES nm_assistant_conversations(conversation_id) ON DELETE CASCADE,
        owner TEXT NOT NULL,
        role TEXT NOT NULL,
        route TEXT NOT NULL,
        content TEXT,
        prompt_text TEXT,
        requirements_text TEXT,
        rewritten_query TEXT,
        provider TEXT,
        model TEXT,
        request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_turns_conversation_created_idx
        ON nm_assistant_turns (conversation_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_turns_owner_route_created_idx
        ON nm_assistant_turns (owner, route, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_assistant_episodes (
        episode_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        source_path TEXT NOT NULL,
        iteration INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        outcome TEXT NOT NULL,
        summary TEXT,
        search_blob TEXT,
        requirement_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        modified_files JSONB NOT NULL DEFAULT '[]'::jsonb,
        commands JSONB NOT NULL DEFAULT '[]'::jsonb,
        verification_failures JSONB NOT NULL DEFAULT '[]'::jsonb,
        notes JSONB NOT NULL DEFAULT '[]'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_episodes_owner_created_idx
        ON nm_assistant_episodes (owner, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_episodes_owner_source_created_idx
        ON nm_assistant_episodes (owner, source_path, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_assistant_traces (
        trace_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        route TEXT NOT NULL,
        intent TEXT,
        conversation_id TEXT,
        status TEXT NOT NULL DEFAULT 'running',
        provider TEXT,
        model TEXT,
        cache_hit BOOLEAN,
        request_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
        response_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
        error_code TEXT,
        error_detail TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_traces_owner_created_idx
        ON nm_assistant_traces (owner, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_traces_route_created_idx
        ON nm_assistant_traces (route, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_assistant_trace_spans (
        trace_id TEXT NOT NULL REFERENCES nm_assistant_traces(trace_id) ON DELETE CASCADE,
        span_id TEXT NOT NULL,
        stage TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'success',
        duration_ms INTEGER,
        meta JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (trace_id, span_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_trace_spans_stage_created_idx
        ON nm_assistant_trace_spans (stage, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_assistant_semantic_cache (
        cache_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        route TEXT NOT NULL,
        intent TEXT,
        scope_key TEXT NOT NULL,
        query_hash TEXT NOT NULL,
        query_text TEXT NOT NULL,
        normalized_query TEXT NOT NULL,
        query_terms JSONB NOT NULL DEFAULT '[]'::jsonb,
        response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        hit_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ,
        last_hit_at TIMESTAMPTZ
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS nm_assistant_semantic_cache_scope_hash_idx
        ON nm_assistant_semantic_cache (owner, route, scope_key, query_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_semantic_cache_lookup_idx
        ON nm_assistant_semantic_cache (owner, route, scope_key, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_assistant_semantic_cache_expiry_idx
        ON nm_assistant_semantic_cache (expires_at)
    """,
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tokenise(text: str) -> List[str]:
    if not text:
        return []
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _created_at_timestamp(value: str) -> float:
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return time.time()


def _safe_list(value: Any, *, max_items: int, max_chars: int) -> List[str]:
    items: List[str] = []
    for raw in value or []:
        cleaned = clamp_text(raw, max_length=max_chars)
        if not cleaned or cleaned in items:
            continue
        items.append(cleaned)
        if len(items) >= max_items:
            break
    return items


def _safe_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 24:
                break
            safe_key = clamp_text(key, max_length=96)
            if not safe_key:
                continue
            if item in (None, "", [], {}):
                continue
            cleaned[safe_key] = item
        return cleaned
    return {}


def _row_payload(row: Any) -> Dict[str, Any]:
    payload = dict(row or {})
    for key, value in list(payload.items()):
        if hasattr(value, "strftime"):
            payload[key] = timestamp(value)
    return payload


def _episode_from_row(row: Dict[str, Any]) -> SolverEpisode:
    requirement_ids = row.get("requirement_ids") if isinstance(row.get("requirement_ids"), list) else []
    modified_files = row.get("modified_files") if isinstance(row.get("modified_files"), list) else []
    commands = row.get("commands") if isinstance(row.get("commands"), list) else []
    verification_failures = row.get("verification_failures") if isinstance(row.get("verification_failures"), list) else []
    notes = row.get("notes") if isinstance(row.get("notes"), list) else []
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    created_at = row.get("created_at")
    if hasattr(created_at, "strftime"):
        created_at = created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return SolverEpisode(
        episode_id=clamp_text(row.get("episode_id"), max_length=128) or uuid.uuid4().hex,
        source_path=clamp_text(row.get("source_path"), max_length=512),
        iteration=coerce_int(row.get("iteration"), 0),
        created_at=clamp_text(created_at, default=_now_iso(), max_length=64),
        outcome=clamp_text(row.get("outcome"), default="success", max_length=32),
        summary=clamp_text(row.get("summary"), max_length=1200),
        requirement_ids=_safe_list(requirement_ids, max_items=16, max_chars=64),
        modified_files=_safe_list(modified_files, max_items=12, max_chars=240),
        commands=_safe_list(commands, max_items=8, max_chars=240),
        verification_failures=_safe_list(verification_failures, max_items=8, max_chars=280),
        notes=_safe_list(notes, max_items=8, max_chars=280),
        metadata=_safe_metadata(metadata),
    )


class PostgresAssistantConversationStore:
    """Shared store for assistant conversation headers and turns."""

    def __init__(self, store: Any):
        self.store = store

    def ensure_conversation(
        self,
        conversation_id: str,
        owner: str,
        *,
        route: str,
        scope: str = "",
        title: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conversation_id = clamp_text(conversation_id, max_length=128)
        owner = clamp_text(owner, max_length=128)
        if not conversation_id or not owner:
            return
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_assistant_conversations (
                        conversation_id,
                        owner,
                        route,
                        scope,
                        title,
                        updated_at,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                    ON CONFLICT (conversation_id) DO UPDATE
                    SET route = COALESCE(NULLIF(EXCLUDED.route, ''), nm_assistant_conversations.route),
                        scope = COALESCE(NULLIF(EXCLUDED.scope, ''), nm_assistant_conversations.scope),
                        title = COALESCE(NULLIF(EXCLUDED.title, ''), nm_assistant_conversations.title),
                        updated_at = NOW(),
                        metadata = CASE
                            WHEN EXCLUDED.metadata = '{}'::jsonb THEN nm_assistant_conversations.metadata
                            ELSE EXCLUDED.metadata
                        END
                    WHERE nm_assistant_conversations.owner = EXCLUDED.owner
                    """,
                    (
                        conversation_id,
                        owner,
                        clamp_text(route, max_length=96) or "assistant",
                        clamp_text(scope, max_length=128) or None,
                        clamp_text(title, max_length=240) or None,
                        jsonb(metadata),
                    ),
                )

    def append_turn(
        self,
        conversation_id: str,
        owner: str,
        *,
        role: str,
        route: str,
        content: str = "",
        prompt_text: str = "",
        requirements_text: str = "",
        rewritten_query: str = "",
        provider: str = "",
        model: str = "",
        request_payload: Optional[Dict[str, Any]] = None,
        response_payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        conversation_id = clamp_text(conversation_id, max_length=128)
        owner = clamp_text(owner, max_length=128)
        if not conversation_id or not owner:
            return ""
        turn_id = uuid.uuid4().hex
        with self.store.pool.connection() as conn:
            with conn.transaction():
                inserted = conn.execute(
                    """
                    INSERT INTO nm_assistant_turns (
                        turn_id,
                        conversation_id,
                        owner,
                        role,
                        route,
                        content,
                        prompt_text,
                        requirements_text,
                        rewritten_query,
                        provider,
                        model,
                        request_payload,
                        response_payload,
                        metadata
                    )
                    SELECT
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    FROM nm_assistant_conversations
                    WHERE conversation_id = %s AND owner = %s
                    """,
                    (
                        turn_id,
                        conversation_id,
                        owner,
                        clamp_text(role, default="user", max_length=24),
                        clamp_text(route, default="assistant", max_length=96),
                        content or None,
                        prompt_text or None,
                        requirements_text or None,
                        rewritten_query or None,
                        clamp_text(provider, max_length=128) or None,
                        clamp_text(model, max_length=256) or None,
                        jsonb(request_payload),
                        jsonb(response_payload),
                        jsonb(metadata),
                        conversation_id,
                        owner,
                    ),
                )
                if getattr(inserted, "rowcount", 0) <= 0:
                    return ""
                conn.execute(
                    """
                    UPDATE nm_assistant_conversations
                    SET last_turn_id = %s,
                        last_turn_at = NOW(),
                        updated_at = NOW()
                    WHERE conversation_id = %s AND owner = %s
                    """,
                    (turn_id, conversation_id, owner),
                )
        return turn_id

    def list_conversations(self, owner: str = "", *, route: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        owner = clamp_text(owner, max_length=128)
        route = clamp_text(route, max_length=96)
        limit_val = max(1, min(coerce_int(limit, 50), 200))
        where: List[str] = []
        params: List[Any] = []
        if owner:
            where.append("owner = %s")
            params.append(owner)
        if route:
            where.append("route = %s")
            params.append(route)
        query = """
            SELECT
                conversation_id,
                owner,
                route,
                scope,
                title,
                last_turn_id,
                created_at,
                updated_at,
                last_turn_at,
                metadata
            FROM nm_assistant_conversations
        """
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY updated_at DESC LIMIT %s"
        with self.store.pool.connection() as conn:
            rows = conn.execute(query, tuple(params + [limit_val])).fetchall()
        return [_row_payload(row) for row in rows]

    def get_conversation(self, conversation_id: str, *, owner: str = "") -> Optional[Dict[str, Any]]:
        conversation_id = clamp_text(conversation_id, max_length=128)
        owner = clamp_text(owner, max_length=128)
        if not conversation_id:
            return None
        where = ["conversation_id = %s"]
        params: List[Any] = [conversation_id]
        if owner:
            where.append("owner = %s")
            params.append(owner)
        with self.store.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    conversation_id,
                    owner,
                    route,
                    scope,
                    title,
                    last_turn_id,
                    created_at,
                    updated_at,
                    last_turn_at,
                    metadata
                FROM nm_assistant_conversations
                WHERE """
                + " AND ".join(where),
                tuple(params),
            ).fetchone()
        return _row_payload(row) if row else None

    def recent_turns(self, conversation_id: str, *, owner: str = "", limit: int = 12) -> List[Dict[str, Any]]:
        conversation_id = clamp_text(conversation_id, max_length=128)
        owner = clamp_text(owner, max_length=128)
        if not conversation_id:
            return []
        limit_val = max(1, min(coerce_int(limit, 12), 100))
        where = ["conversation_id = %s"]
        params: List[Any] = [conversation_id]
        if owner:
            where.append("owner = %s")
            params.append(owner)
        with self.store.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    turn_id,
                    role,
                    route,
                    content,
                    prompt_text,
                    requirements_text,
                    rewritten_query,
                    provider,
                    model,
                    request_payload,
                    response_payload,
                    metadata,
                    created_at
                FROM nm_assistant_turns
                WHERE """
                + " AND ".join(where)
                + """
                ORDER BY created_at DESC
                LIMIT %s
                """,
                tuple(params + [limit_val]),
            ).fetchall()
        results = [_row_payload(row) for row in rows]
        results.reverse()
        return results


class PostgresAssistantEpisodeStore:
    """Shared episodic memory store compatible with `SolverEpisodeStore`."""

    def __init__(self, store: Any):
        self.store = store

    def record(self, owner: str, episode: SolverEpisode) -> None:
        if not isinstance(episode, SolverEpisode):
            return
        owner = clamp_text(owner, max_length=128)
        if not owner:
            return
        payload = episode.to_record()
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_assistant_episodes (
                        episode_id,
                        owner,
                        source_path,
                        iteration,
                        created_at,
                        outcome,
                        summary,
                        search_blob,
                        requirement_ids,
                        modified_files,
                        commands,
                        verification_failures,
                        notes,
                        metadata
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s::jsonb,
                        %s::jsonb,
                        %s::jsonb,
                        %s::jsonb,
                        %s
                    )
                    ON CONFLICT (episode_id) DO UPDATE
                    SET owner = EXCLUDED.owner,
                        source_path = EXCLUDED.source_path,
                        iteration = EXCLUDED.iteration,
                        created_at = EXCLUDED.created_at,
                        outcome = EXCLUDED.outcome,
                        summary = EXCLUDED.summary,
                        search_blob = EXCLUDED.search_blob,
                        requirement_ids = EXCLUDED.requirement_ids,
                        modified_files = EXCLUDED.modified_files,
                        commands = EXCLUDED.commands,
                        verification_failures = EXCLUDED.verification_failures,
                        notes = EXCLUDED.notes,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        payload["episode_id"],
                        owner,
                        payload["source_path"],
                        coerce_int(payload.get("iteration"), 0),
                        payload.get("created_at") or _now_iso(),
                        payload.get("outcome") or "success",
                        payload.get("summary") or "",
                        episode.search_blob(),
                        json.dumps(payload.get("requirement_ids") or []),
                        json.dumps(payload.get("modified_files") or []),
                        json.dumps(payload.get("commands") or []),
                        json.dumps(payload.get("verification_failures") or []),
                        json.dumps(payload.get("notes") or []),
                        jsonb(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
                    ),
                )

    def snapshot(
        self,
        owner: str,
        *,
        source_path: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[SolverEpisode]:
        owner = clamp_text(owner, max_length=128)
        if not owner:
            return []
        where = ["owner = %s"]
        params: List[Any] = [owner]
        if source_path:
            where.append("source_path = %s")
            params.append(source_path)
        query = "SELECT * FROM nm_assistant_episodes " + "WHERE " + " AND ".join(where)
        if limit is not None and limit > 0:
            query += " ORDER BY created_at DESC"
            query += " LIMIT %s"
            params.append(max(1, int(limit)))
        else:
            query += " ORDER BY created_at ASC"
        with self.store.pool.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        entries = [_episode_from_row(dict(row)) for row in rows]
        if limit is not None and limit > 0:
            entries.reverse()
        return entries

    def recent(self, owner: str, *, source_path: Optional[str] = None, limit: int = 3) -> List[SolverEpisode]:
        owner = clamp_text(owner, max_length=128)
        if not owner:
            return []
        limit_val = max(1, min(coerce_int(limit, 3), _MAX_SEARCH_RESULTS))
        where = ["owner = %s"]
        params: List[Any] = [owner]
        if source_path:
            where.append("source_path = %s")
            params.append(source_path)
        with self.store.pool.connection() as conn:
            rows = conn.execute(
                (
                    "SELECT * FROM nm_assistant_episodes WHERE "
                    + " AND ".join(where)
                    + " ORDER BY created_at DESC LIMIT %s"
                ),
                tuple(params + [limit_val]),
            ).fetchall()
        return [_episode_from_row(dict(row)) for row in rows]

    def search(
        self,
        owner: str,
        query_text: str,
        *,
        source_path: Optional[str] = None,
        requirement_ids: Optional[Sequence[str]] = None,
        limit: int = 3,
    ) -> List[SolverEpisode]:
        owner = clamp_text(owner, max_length=128)
        if not owner:
            return []
        limit_val = max(1, min(coerce_int(limit, 3), _MAX_SEARCH_RESULTS))
        where = ["owner = %s"]
        params: List[Any] = [owner]
        if source_path:
            where.append("(source_path = %s OR source_path LIKE %s)")
            params.extend([source_path, f"%{source_path.split('/')[-1]}"])
        with self.store.pool.connection() as conn:
            rows = conn.execute(
                (
                    "SELECT * FROM nm_assistant_episodes WHERE "
                    + " AND ".join(where)
                    + " ORDER BY created_at DESC LIMIT %s"
                ),
                tuple(params + [max(60, limit_val * 25)]),
            ).fetchall()
        entries = [_episode_from_row(dict(row)) for row in rows]
        if not entries:
            return []
        query_tokens = set(_tokenise(query_text))
        wanted_requirement_ids = [item for item in (requirement_ids or []) if item]
        scored: List[Tuple[float, SolverEpisode]] = []
        now = time.time()
        for entry in entries:
            score = 0.0
            if source_path and entry.source_path == source_path:
                score += 6.0
            elif source_path and entry.source_path.split("/")[-1] == source_path.split("/")[-1]:
                score += 2.0
            if wanted_requirement_ids:
                overlap = len(set(entry.requirement_ids) & set(wanted_requirement_ids))
                score += overlap * 2.5
            entry_tokens = set(_tokenise(entry.search_blob()))
            if query_tokens and entry_tokens:
                overlap = len(query_tokens & entry_tokens)
                if overlap:
                    score += overlap / max(1.0, math.sqrt(len(entry_tokens)))
            age_seconds = max(0.0, now - _created_at_timestamp(entry.created_at))
            score += max(0.0, 2.0 - (age_seconds / 86400.0) * 0.05)
            if entry.outcome == "failure":
                score += 0.5
            elif entry.outcome == "success":
                score += 0.75
            if score > 0:
                scored.append((score, entry))
        if not scored:
            return self.recent(owner, source_path=source_path, limit=limit_val)
        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [entry for _, entry in scored[:limit_val]]


class PostgresAssistantTraceStore:
    """Shared trace store for assistant and RAG stage visibility."""

    def __init__(self, store: Any):
        self.store = store

    def start_trace(
        self,
        trace_id: str,
        owner: str,
        *,
        route: str,
        intent: str = "",
        conversation_id: Optional[str] = None,
        request_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        trace_id = clamp_text(trace_id, max_length=128)
        owner = clamp_text(owner, max_length=128)
        if not trace_id or not owner:
            return
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_assistant_traces (
                        trace_id,
                        owner,
                        route,
                        intent,
                        conversation_id,
                        status,
                        request_meta,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 'running', %s, NOW())
                    ON CONFLICT (trace_id) DO NOTHING
                    """,
                    (
                        trace_id,
                        owner,
                        clamp_text(route, default="assistant", max_length=96),
                        clamp_text(intent, max_length=96) or None,
                        clamp_text(conversation_id, max_length=128) or None,
                        jsonb(request_meta),
                    ),
                )

    def record_span(
        self,
        trace_id: str,
        stage: str,
        *,
        status: str = "success",
        duration_ms: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        trace_id = clamp_text(trace_id, max_length=128)
        stage = clamp_text(stage, max_length=96)
        if not trace_id or not stage:
            return
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_assistant_trace_spans (
                        trace_id,
                        span_id,
                        stage,
                        status,
                        duration_ms,
                        meta
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        trace_id,
                        uuid.uuid4().hex,
                        stage,
                        clamp_text(status, default="success", max_length=24),
                        duration_ms,
                        jsonb(meta),
                    ),
                )

    def finish_trace(
        self,
        trace_id: str,
        *,
        status: str,
        provider: str = "",
        model: str = "",
        cache_hit: Optional[bool] = None,
        response_meta: Optional[Dict[str, Any]] = None,
        error_code: str = "",
        error_detail: str = "",
    ) -> None:
        trace_id = clamp_text(trace_id, max_length=128)
        if not trace_id:
            return
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE nm_assistant_traces
                    SET status = %s,
                        provider = COALESCE(NULLIF(%s, ''), provider),
                        model = COALESCE(NULLIF(%s, ''), model),
                        cache_hit = COALESCE(%s, cache_hit),
                        response_meta = CASE
                            WHEN %s = '{}'::jsonb THEN response_meta
                            ELSE %s
                        END,
                        error_code = NULLIF(%s, ''),
                        error_detail = NULLIF(%s, ''),
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE trace_id = %s
                    """,
                    (
                        clamp_text(status, default="success", max_length=24),
                        clamp_text(provider, max_length=128),
                        clamp_text(model, max_length=256),
                        cache_hit,
                        jsonb(response_meta),
                        jsonb(response_meta),
                        clamp_text(error_code, max_length=96),
                        clamp_text(error_detail, max_length=4000),
                        trace_id,
                    ),
                )

    def list_traces(
        self,
        owner: str = "",
        *,
        route: str = "",
        status: str = "",
        conversation_id: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        owner = clamp_text(owner, max_length=128)
        route = clamp_text(route, max_length=96)
        status = clamp_text(status, max_length=24)
        conversation_id = clamp_text(conversation_id, max_length=128)
        limit_val = max(1, min(coerce_int(limit, 50), 200))
        where: List[str] = []
        params: List[Any] = []
        if owner:
            where.append("owner = %s")
            params.append(owner)
        if route:
            where.append("route = %s")
            params.append(route)
        if status:
            where.append("status = %s")
            params.append(status)
        if conversation_id:
            where.append("conversation_id = %s")
            params.append(conversation_id)
        query = """
            SELECT
                trace_id,
                owner,
                route,
                intent,
                conversation_id,
                status,
                provider,
                model,
                cache_hit,
                request_meta,
                response_meta,
                error_code,
                error_detail,
                created_at,
                updated_at,
                finished_at
            FROM nm_assistant_traces
        """
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC LIMIT %s"
        with self.store.pool.connection() as conn:
            rows = conn.execute(query, tuple(params + [limit_val])).fetchall()
        return [_row_payload(row) for row in rows]

    def get_trace(self, trace_id: str, *, owner: str = "") -> Optional[Dict[str, Any]]:
        trace_id = clamp_text(trace_id, max_length=128)
        owner = clamp_text(owner, max_length=128)
        if not trace_id:
            return None
        where = ["trace_id = %s"]
        params: List[Any] = [trace_id]
        if owner:
            where.append("owner = %s")
            params.append(owner)
        with self.store.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    trace_id,
                    owner,
                    route,
                    intent,
                    conversation_id,
                    status,
                    provider,
                    model,
                    cache_hit,
                    request_meta,
                    response_meta,
                    error_code,
                    error_detail,
                    created_at,
                    updated_at,
                    finished_at
                FROM nm_assistant_traces
                WHERE """
                + " AND ".join(where),
                tuple(params),
            ).fetchone()
        return _row_payload(row) if row else None

    def list_spans(self, trace_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        trace_id = clamp_text(trace_id, max_length=128)
        if not trace_id:
            return []
        limit_val = max(1, min(coerce_int(limit, 200), 500))
        with self.store.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    trace_id,
                    span_id,
                    stage,
                    status,
                    duration_ms,
                    meta,
                    created_at
                FROM nm_assistant_trace_spans
                WHERE trace_id = %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (trace_id, limit_val),
            ).fetchall()
        return [_row_payload(row) for row in rows]


class PostgresAssistantSemanticCacheStore:
    """Shared semantic cache store for read-only assistant and RAG responses."""

    def __init__(self, store: Any):
        self.store = store

    def list_candidates(
        self,
        owner: str,
        route: str,
        scope_key: str,
        *,
        intent: str = "",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        owner = clamp_text(owner, max_length=128)
        route = clamp_text(route, max_length=96)
        scope_key = clamp_text(scope_key, max_length=256)
        intent = clamp_text(intent, max_length=128)
        if not owner or not route or not scope_key:
            return []
        limit_val = max(1, min(coerce_int(limit, 20), 100))
        where = [
            "owner = %s",
            "route = %s",
            "scope_key = %s",
            "(expires_at IS NULL OR expires_at > NOW())",
        ]
        params: List[Any] = [owner, route, scope_key]
        if intent:
            where.append("(intent = %s OR intent IS NULL)")
            params.append(intent)
        with self.store.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    cache_id,
                    owner,
                    route,
                    intent,
                    scope_key,
                    query_hash,
                    query_text,
                    normalized_query,
                    query_terms,
                    response_payload,
                    metadata,
                    hit_count,
                    created_at,
                    expires_at,
                    last_hit_at
                FROM nm_assistant_semantic_cache
                WHERE """
                + " AND ".join(where)
                + """
                ORDER BY last_hit_at DESC NULLS LAST, created_at DESC
                LIMIT %s
                """,
                tuple(params + [limit_val]),
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def upsert_entry(
        self,
        owner: str,
        route: str,
        scope_key: str,
        *,
        intent: str = "",
        query_text: str,
        normalized_query: str,
        query_terms: Sequence[str],
        response_payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ttl_hours: float = 12.0,
    ) -> str:
        owner = clamp_text(owner, max_length=128)
        route = clamp_text(route, max_length=96)
        scope_key = clamp_text(scope_key, max_length=256)
        intent = clamp_text(intent, max_length=128)
        query_text = clamp_text(query_text, max_length=4000)
        normalized_query = clamp_text(normalized_query, max_length=4000)
        if not owner or not route or not scope_key or not normalized_query:
            return ""
        cache_id = uuid.uuid4().hex
        query_hash = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
        ttl_value = max(0.0, float(ttl_hours or 0.0))
        with self.store.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO nm_assistant_semantic_cache (
                        cache_id,
                        owner,
                        route,
                        intent,
                        scope_key,
                        query_hash,
                        query_text,
                        normalized_query,
                        query_terms,
                        response_payload,
                        metadata,
                        expires_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s,
                        %s,
                        CASE
                            WHEN %s <= 0 THEN NULL
                            ELSE NOW() + (%s * INTERVAL '1 hour')
                        END
                    )
                    ON CONFLICT (owner, route, scope_key, query_hash) DO UPDATE
                    SET intent = COALESCE(NULLIF(EXCLUDED.intent, ''), nm_assistant_semantic_cache.intent),
                        query_text = EXCLUDED.query_text,
                        normalized_query = EXCLUDED.normalized_query,
                        query_terms = EXCLUDED.query_terms,
                        response_payload = EXCLUDED.response_payload,
                        metadata = EXCLUDED.metadata,
                        expires_at = EXCLUDED.expires_at
                    RETURNING cache_id
                    """,
                    (
                        cache_id,
                        owner,
                        route,
                        intent or None,
                        scope_key,
                        query_hash,
                        query_text,
                        normalized_query,
                        json.dumps([clamp_text(item, max_length=64) for item in query_terms if clamp_text(item, max_length=64)]),
                        jsonb(response_payload),
                        jsonb(metadata),
                        ttl_value,
                        ttl_value,
                    ),
                ).fetchone()
        return clamp_text((row or {}).get("cache_id"), max_length=128)

    def record_hit(self, cache_id: str) -> None:
        cache_id = clamp_text(cache_id, max_length=128)
        if not cache_id:
            return
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE nm_assistant_semantic_cache
                    SET hit_count = hit_count + 1,
                        last_hit_at = NOW()
                    WHERE cache_id = %s
                    """,
                    (cache_id,),
                )
