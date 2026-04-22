"""Postgres-backed metadata for file-backed RAG collections and queries."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Iterable, List, Optional

from central_store.base import clamp_text, coerce_int, jsonb

RAG_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS nm_rag_collections (
        owner TEXT NOT NULL,
        name TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'user',
        status TEXT NOT NULL DEFAULT 'ready',
        active_version_id TEXT,
        artifact_path TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (owner, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_rag_collections_owner_updated_idx
        ON nm_rag_collections (owner, updated_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_rag_collection_versions (
        version_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ready',
        artifact_path TEXT,
        source_count INTEGER NOT NULL DEFAULT 0,
        document_count INTEGER NOT NULL DEFAULT 0,
        chunk_count INTEGER NOT NULL DEFAULT 0,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        published_at TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_rag_collection_versions_lookup_idx
        ON nm_rag_collection_versions (owner, name, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_rag_documents (
        version_id TEXT NOT NULL REFERENCES nm_rag_collection_versions(version_id) ON DELETE CASCADE,
        doc_id TEXT NOT NULL,
        source TEXT NOT NULL,
        source_path TEXT,
        source_url TEXT,
        content_hash TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (version_id, doc_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_rag_documents_source_idx
        ON nm_rag_documents (source, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_rag_chunks (
        version_id TEXT NOT NULL REFERENCES nm_rag_collection_versions(version_id) ON DELETE CASCADE,
        chunk_id TEXT NOT NULL,
        doc_id TEXT,
        source TEXT NOT NULL,
        citation TEXT,
        text_preview TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (version_id, chunk_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_rag_chunks_source_idx
        ON nm_rag_chunks (source, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_rag_query_audits (
        audit_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        name TEXT NOT NULL,
        route TEXT NOT NULL,
        version_id TEXT,
        query_text TEXT NOT NULL,
        rewritten_query TEXT,
        top_k INTEGER,
        match_count INTEGER NOT NULL DEFAULT 0,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_rag_query_audits_owner_created_idx
        ON nm_rag_query_audits (owner, created_at DESC)
    """,
)


def _document_hash(doc: Any) -> str:
    hasher = hashlib.sha256()
    hasher.update(str(getattr(doc, "doc_id", "")).encode("utf-8", errors="ignore"))
    hasher.update(b"\0")
    hasher.update(str(getattr(doc, "source", "")).encode("utf-8", errors="ignore"))
    hasher.update(b"\0")
    hasher.update(str(getattr(doc, "text", "")).encode("utf-8", errors="ignore"))
    return hasher.hexdigest()


class PostgresRagMetadataStore:
    """Metadata registry for file-backed per-owner RAG indexes."""

    def __init__(self, store: Any):
        self.store = store

    def _upsert_collection_state(
        self,
        conn: Any,
        *,
        owner: str,
        name: str,
        scope: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
        active_version_id: str = "",
        artifact_path: str = "",
    ) -> None:
        conn.execute(
            """
            INSERT INTO nm_rag_collections (
                owner,
                name,
                scope,
                status,
                active_version_id,
                artifact_path,
                metadata,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (owner, name) DO UPDATE
            SET scope = COALESCE(NULLIF(EXCLUDED.scope, ''), nm_rag_collections.scope),
                status = COALESCE(NULLIF(EXCLUDED.status, ''), nm_rag_collections.status),
                active_version_id = COALESCE(NULLIF(EXCLUDED.active_version_id, ''), nm_rag_collections.active_version_id),
                artifact_path = COALESCE(NULLIF(EXCLUDED.artifact_path, ''), nm_rag_collections.artifact_path),
                metadata = CASE
                    WHEN EXCLUDED.metadata = '{}'::jsonb THEN nm_rag_collections.metadata
                    ELSE EXCLUDED.metadata
                END,
                updated_at = NOW()
            """,
            (
                owner,
                name,
                clamp_text(scope, default="user", max_length=24),
                clamp_text(status, default="ready", max_length=24),
                clamp_text(active_version_id, max_length=128),
                clamp_text(artifact_path, max_length=2048) or None,
                jsonb(metadata),
            ),
        )

    def start_collection_build(
        self,
        owner: str,
        name: str,
        *,
        version_id: Optional[str] = None,
        status: str = "building",
        scope: str = "user",
        artifact_path: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        owner = clamp_text(owner, max_length=128)
        name = clamp_text(name, max_length=128)
        if not owner or not name:
            return ""
        version_id = clamp_text(version_id, max_length=128) or uuid.uuid4().hex
        with self.store.pool.connection() as conn:
            with conn.transaction():
                self._upsert_collection_state(
                    conn,
                    owner=owner,
                    name=name,
                    scope=scope,
                    status=status,
                    metadata=metadata,
                )
                conn.execute(
                    """
                    INSERT INTO nm_rag_collection_versions (
                        version_id,
                        owner,
                        name,
                        status,
                        artifact_path,
                        source_count,
                        document_count,
                        chunk_count,
                        metadata,
                        published_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 0, 0, 0, %s, NULL)
                    ON CONFLICT (version_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        artifact_path = COALESCE(NULLIF(EXCLUDED.artifact_path, ''), nm_rag_collection_versions.artifact_path),
                        metadata = CASE
                            WHEN EXCLUDED.metadata = '{}'::jsonb THEN nm_rag_collection_versions.metadata
                            ELSE EXCLUDED.metadata
                        END
                    """,
                    (
                        version_id,
                        owner,
                        name,
                        clamp_text(status, default="building", max_length=24),
                        clamp_text(artifact_path, max_length=2048) or None,
                        jsonb(metadata),
                    ),
                )
        return version_id

    def fail_collection_build(
        self,
        owner: str,
        name: str,
        *,
        version_id: str,
        status: str = "failed",
        artifact_path: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        owner = clamp_text(owner, max_length=128)
        name = clamp_text(name, max_length=128)
        version_id = clamp_text(version_id, max_length=128)
        if not owner or not name or not version_id:
            return ""
        with self.store.pool.connection() as conn:
            with conn.transaction():
                self._upsert_collection_state(
                    conn,
                    owner=owner,
                    name=name,
                    scope="user",
                    status=status,
                    metadata=metadata,
                )
                conn.execute(
                    """
                    INSERT INTO nm_rag_collection_versions (
                        version_id,
                        owner,
                        name,
                        status,
                        artifact_path,
                        source_count,
                        document_count,
                        chunk_count,
                        metadata,
                        published_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 0, 0, 0, %s, NULL)
                    ON CONFLICT (version_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        artifact_path = COALESCE(NULLIF(EXCLUDED.artifact_path, ''), nm_rag_collection_versions.artifact_path),
                        metadata = CASE
                            WHEN EXCLUDED.metadata = '{}'::jsonb THEN nm_rag_collection_versions.metadata
                            ELSE EXCLUDED.metadata
                        END
                    """,
                    (
                        version_id,
                        owner,
                        name,
                        clamp_text(status, default="failed", max_length=24),
                        clamp_text(artifact_path, max_length=2048) or None,
                        jsonb(metadata),
                    ),
                )
        return version_id

    def record_collection_version(
        self,
        owner: str,
        name: str,
        *,
        artifact_path: str,
        source_count: int,
        documents: Iterable[Any],
        chunks: Iterable[Any],
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "ready",
        scope: str = "user",
        version_id: Optional[str] = None,
    ) -> str:
        owner = clamp_text(owner, max_length=128)
        name = clamp_text(name, max_length=128)
        if not owner or not name:
            return ""
        version_id = clamp_text(version_id, max_length=128) or uuid.uuid4().hex
        document_rows = []
        for doc in documents or []:
            doc_metadata = getattr(doc, "metadata", None) if isinstance(getattr(doc, "metadata", None), dict) else {}
            document_rows.append(
                (
                    version_id,
                    clamp_text(getattr(doc, "doc_id", None), max_length=128) or uuid.uuid4().hex,
                    clamp_text(getattr(doc, "source", None), default="source", max_length=512),
                    clamp_text(doc_metadata.get("source_path"), max_length=1024) or None,
                    clamp_text(doc_metadata.get("source_url"), max_length=1024) or None,
                    _document_hash(doc),
                    jsonb(doc_metadata),
                )
            )
        chunk_rows = []
        for chunk in chunks or []:
            chunk_metadata = getattr(chunk, "metadata", None) if isinstance(getattr(chunk, "metadata", None), dict) else {}
            preview = clamp_text(getattr(chunk, "text", None), max_length=500)
            chunk_rows.append(
                (
                    version_id,
                    clamp_text(getattr(chunk, "chunk_id", None), max_length=128) or uuid.uuid4().hex,
                    clamp_text(getattr(chunk, "doc_id", None), max_length=128) or None,
                    clamp_text(getattr(chunk, "source", None), default="source", max_length=512),
                    clamp_text(getattr(chunk, "citation", None), max_length=512) or None,
                    preview or None,
                    jsonb(chunk_metadata),
                )
            )
        with self.store.pool.connection() as conn:
            with conn.transaction():
                self._upsert_collection_state(
                    conn,
                    owner=owner,
                    name=name,
                    scope=scope,
                    status=status,
                    metadata=metadata,
                    active_version_id=version_id,
                    artifact_path=artifact_path,
                )
                conn.execute(
                    """
                    INSERT INTO nm_rag_collection_versions (
                        version_id,
                        owner,
                        name,
                        status,
                        artifact_path,
                        source_count,
                        document_count,
                        chunk_count,
                        metadata,
                        published_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (version_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        artifact_path = EXCLUDED.artifact_path,
                        source_count = EXCLUDED.source_count,
                        document_count = EXCLUDED.document_count,
                        chunk_count = EXCLUDED.chunk_count,
                        metadata = EXCLUDED.metadata,
                        published_at = NOW()
                    """,
                    (
                        version_id,
                        owner,
                        name,
                        clamp_text(status, default="ready", max_length=24),
                        clamp_text(artifact_path, max_length=2048) or None,
                        max(0, coerce_int(source_count, 0)),
                        len(document_rows),
                        len(chunk_rows),
                        jsonb(metadata),
                    ),
                )
                conn.execute("DELETE FROM nm_rag_documents WHERE version_id = %s", (version_id,))
                conn.execute("DELETE FROM nm_rag_chunks WHERE version_id = %s", (version_id,))
                if document_rows:
                    conn.executemany(
                        """
                        INSERT INTO nm_rag_documents (
                            version_id,
                            doc_id,
                            source,
                            source_path,
                            source_url,
                            content_hash,
                            metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        document_rows,
                    )
                if chunk_rows:
                    conn.executemany(
                        """
                        INSERT INTO nm_rag_chunks (
                            version_id,
                            chunk_id,
                            doc_id,
                            source,
                            citation,
                            text_preview,
                            metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        chunk_rows,
                    )
        return version_id

    def get_active_version(self, owner: str, name: str) -> Optional[Dict[str, Any]]:
        owner = clamp_text(owner, max_length=128)
        name = clamp_text(name, max_length=128)
        if not owner or not name:
            return None
        with self.store.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT c.owner, c.name, c.active_version_id, c.status, c.artifact_path, c.metadata,
                       v.source_count, v.document_count, v.chunk_count, v.created_at, v.published_at
                FROM nm_rag_collections c
                LEFT JOIN nm_rag_collection_versions v ON v.version_id = c.active_version_id
                WHERE c.owner = %s AND c.name = %s
                """,
                (owner, name),
            ).fetchone()
        return dict(row) if row else None

    def record_query_audit(
        self,
        owner: str,
        name: str,
        *,
        route: str,
        query_text: str,
        rewritten_query: str = "",
        top_k: int = 0,
        match_count: int = 0,
        version_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        owner = clamp_text(owner, max_length=128)
        name = clamp_text(name, max_length=128)
        route = clamp_text(route, max_length=96)
        if not owner or not name or not route:
            return ""
        audit_id = uuid.uuid4().hex
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_rag_query_audits (
                        audit_id,
                        owner,
                        name,
                        route,
                        version_id,
                        query_text,
                        rewritten_query,
                        top_k,
                        match_count,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        audit_id,
                        owner,
                        name,
                        route,
                        clamp_text(version_id, max_length=128) or None,
                        query_text,
                        rewritten_query or None,
                        coerce_int(top_k, 0) or None,
                        max(0, coerce_int(match_count, 0)),
                        jsonb(metadata),
                    ),
                )
        return audit_id

    def delete_collection(self, owner: str, name: str) -> bool:
        owner = clamp_text(owner, max_length=128)
        name = clamp_text(name, max_length=128)
        if not owner or not name:
            return False
        with self.store.pool.connection() as conn:
            with conn.transaction():
                version_rows = conn.execute(
                    "SELECT version_id FROM nm_rag_collection_versions WHERE owner = %s AND name = %s",
                    (owner, name),
                ).fetchall()
                version_ids = [str(row.get("version_id") or "").strip() for row in version_rows if row]
                if version_ids:
                    conn.executemany(
                        "DELETE FROM nm_rag_collection_versions WHERE version_id = %s",
                        [(version_id,) for version_id in version_ids if version_id],
                    )
                deleted = conn.execute(
                    "DELETE FROM nm_rag_collections WHERE owner = %s AND name = %s",
                    (owner, name),
                )
        return bool(getattr(deleted, "rowcount", 0) or 0)
