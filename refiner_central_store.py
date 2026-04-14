from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import threading
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from werkzeug.security import check_password_hash, generate_password_hash

UTC = dt.timezone.utc
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("REFINER_ACCESS_TOKEN_TTL", "43200"))
DEFAULT_SSO_TOKEN_TTL_SECONDS = int(os.getenv("REFINER_SSO_TTL", "300"))

SCHEMA_STATEMENTS: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS nm_users (
        username TEXT PRIMARY KEY,
        password_hash TEXT,
        role TEXT NOT NULL DEFAULT 'user',
        email TEXT,
        external BOOLEAN NOT NULL DEFAULT FALSE,
        provider TEXT,
        subject TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_users_provider_subject_idx
        ON nm_users (provider, subject)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_auth_tokens (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL REFERENCES nm_users(username) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        token_hint TEXT,
        label TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ,
        last_used_at TIMESTAMPTZ,
        disabled BOOLEAN NOT NULL DEFAULT FALSE,
        one_time BOOLEAN NOT NULL DEFAULT FALSE,
        meta JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_auth_tokens_kind_user_idx
        ON nm_auth_tokens (kind, username)
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_auth_tokens_kind_expires_idx
        ON nm_auth_tokens (kind, expires_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_token_accounts (
        scope TEXT NOT NULL,
        account_id TEXT NOT NULL,
        balance INTEGER NOT NULL DEFAULT 0,
        paid_balance INTEGER NOT NULL DEFAULT 0,
        free_balance INTEGER NOT NULL DEFAULT 0,
        last_topup_tokens INTEGER NOT NULL DEFAULT 0,
        last_topup_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ,
        spent_total BIGINT NOT NULL DEFAULT 0,
        cashout_total BIGINT NOT NULL DEFAULT 0,
        shortfall_total BIGINT NOT NULL DEFAULT 0,
        free_grant_total BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (scope, account_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_token_ledger_entries (
        id BIGSERIAL PRIMARY KEY,
        scope TEXT NOT NULL,
        account_id TEXT NOT NULL,
        ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        entry_type TEXT NOT NULL,
        delta INTEGER NOT NULL,
        balance_after INTEGER NOT NULL,
        meta JSONB NOT NULL DEFAULT '{}'::jsonb,
        request_id TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS nm_token_ledger_request_idx
        ON nm_token_ledger_entries (scope, account_id, request_id)
        WHERE request_id IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_token_ledger_lookup_idx
        ON nm_token_ledger_entries (scope, account_id, ts DESC, id DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_mcp_servers (
        owner TEXT NOT NULL REFERENCES nm_users(username) ON DELETE CASCADE,
        name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        auth_type TEXT NOT NULL DEFAULT 'bearer',
        auth_secret_ref TEXT,
        headers_secret_ref TEXT,
        headers JSONB NOT NULL DEFAULT '{}'::jsonb,
        timeout INTEGER NOT NULL DEFAULT 20,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        runtime JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (owner, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_mcp_servers_owner_updated_idx
        ON nm_mcp_servers (owner, updated_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS nm_llm_request_telemetry (
        scope TEXT NOT NULL,
        subject TEXT NOT NULL,
        bucket_hour TIMESTAMPTZ NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        category TEXT NOT NULL,
        requests INTEGER NOT NULL DEFAULT 0,
        successes INTEGER NOT NULL DEFAULT 0,
        quota_errors INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        latency_ms_total BIGINT NOT NULL DEFAULT 0,
        latency_ms_min INTEGER,
        latency_ms_max INTEGER,
        input_chars_total BIGINT NOT NULL DEFAULT 0,
        estimated_input_tokens_total BIGINT NOT NULL DEFAULT 0,
        last_outcome TEXT,
        last_error_class TEXT,
        last_error_detail TEXT,
        last_event_at TIMESTAMPTZ,
        last_max_tokens INTEGER,
        last_reasoning_effort TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (scope, subject, bucket_hour, provider, model, category)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_llm_request_telemetry_scope_subject_bucket_idx
        ON nm_llm_request_telemetry (scope, subject, bucket_hour DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS nm_llm_request_telemetry_bucket_idx
        ON nm_llm_request_telemetry (bucket_hour DESC)
    """,
)


def _timestamp(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_hint(token: str) -> Optional[str]:
    cleaned = str(token or "").strip()
    if not cleaned:
        return None
    if len(cleaned) <= 8:
        return cleaned
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def _jsonb(value: Optional[Dict[str, Any]] = None) -> Jsonb:
    return Jsonb(value or {})


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clean_dimension(value: Any, *, default: str = "", max_length: int = 128) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        cleaned = default
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def _clean_optional_text(value: Any, *, max_length: int = 240) -> Optional[str]:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        return cleaned[:max_length]
    return cleaned


def _coerce_event_datetime(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), UTC)
        except Exception:
            return dt.datetime.now(UTC)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            try:
                parsed = dt.datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC)
            except Exception:
                pass
            try:
                return dt.datetime.fromtimestamp(float(cleaned), UTC)
            except Exception:
                pass
    return dt.datetime.now(UTC)


def _hour_bucket(value: dt.datetime) -> dt.datetime:
    moment = _coerce_event_datetime(value)
    return moment.replace(minute=0, second=0, microsecond=0)


def _default_account_summary() -> Dict[str, Any]:
    return {
        "version": 1,
        "balance": 0,
        "paid_balance": 0,
        "free_balance": 0,
        "last_topup_tokens": 0,
        "last_topup_at": None,
        "updated_at": None,
        "spent_total": 0,
        "cashout_total": 0,
        "shortfall_total": 0,
        "free_grant_total": 0,
    }


def central_store_dsn_from_env(prefix: str = "REFINER_AUTH_DB") -> str:
    explicit = (os.getenv(f"{prefix}_DSN") or os.getenv("REFINER_POSTGRES_DSN") or "").strip()
    if explicit:
        return explicit

    host = (os.getenv(f"{prefix}_HOST") or os.getenv("REFINER_POSTGRES_HOST") or "").strip()
    user = (os.getenv(f"{prefix}_USER") or os.getenv("REFINER_POSTGRES_USER") or "").strip()
    password = (os.getenv(f"{prefix}_PASSWORD") or os.getenv("REFINER_POSTGRES_PASSWORD") or "").strip()
    dbname = (os.getenv(f"{prefix}_NAME") or os.getenv(f"{prefix}_DB") or os.getenv("REFINER_POSTGRES_DB") or "").strip()
    port = (os.getenv(f"{prefix}_PORT") or os.getenv("REFINER_POSTGRES_PORT") or "5432").strip()
    sslmode = (os.getenv(f"{prefix}_SSLMODE") or os.getenv("REFINER_POSTGRES_SSLMODE") or "disable").strip()
    connect_timeout = (os.getenv(f"{prefix}_CONNECT_TIMEOUT") or "5").strip()
    if not host or not user or not dbname:
        return ""
    parts = [
        f"host={host}",
        f"port={port or '5432'}",
        f"user={user}",
        f"dbname={dbname}",
        f"sslmode={sslmode or 'disable'}",
        f"connect_timeout={connect_timeout or '5'}",
        "application_name=refiner",
    ]
    if password:
        parts.append(f"password={password}")
    return " ".join(parts)


class PostgresCentralStore:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4, timeout: float = 10.0):
        self.dsn = dsn
        self.pool = ConnectionPool(
            conninfo=dsn,
            min_size=max(1, int(min_size)),
            max_size=max(1, int(max_size)),
            timeout=max(1.0, float(timeout)),
            kwargs={"row_factory": dict_row},
        )
        self.pool.wait()
        self.ensure_schema()
        self.users = PostgresUserStore(self)
        self.access_tokens = PostgresAccessTokenStore(self)
        self.voice_tokens = PostgresVoiceTokenStore(self)
        self.sso_tokens = PostgresSsoStore(self)
        self.user_ledger = PostgresTokenLedger(self, "user")
        self.team_ledger = PostgresTokenLedger(self, "team")
        self.llm_request_telemetry = PostgresLLMRequestTelemetry(self)

    def close(self) -> None:
        self.pool.close()

    def ensure_schema(self) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                for statement in SCHEMA_STATEMENTS:
                    conn.execute(statement)

    def bootstrap_from_env(self, default_user: str = "") -> None:
        raw = (os.getenv("REFINER_BOOTSTRAP_ACCESS_TOKENS") or "").strip()
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if not isinstance(payload, list):
            return
        for item in payload:
            if not isinstance(item, dict):
                continue
            token = str(item.get("token") or "").strip()
            username = str(item.get("user") or default_user or "").strip()
            if not token or not username:
                continue
            role = str(item.get("role") or "user").strip() or "user"
            label = str(item.get("label") or "").strip() or None
            ttl_seconds = item.get("ttl_seconds")
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            self.users.ensure_user(username, role=role)
            self.access_tokens.ensure_token(
                username,
                token,
                label=label,
                ttl_seconds=int(ttl_seconds) if ttl_seconds not in (None, "") else None,
                meta=meta,
            )


class PostgresUserStore:
    def __init__(self, store: PostgresCentralStore):
        self.store = store
        self.lock = threading.RLock()

    def count_users(self) -> int:
        with self.store.pool.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM nm_users").fetchone()
        return int((row or {}).get("count") or 0)

    def has_users(self) -> bool:
        return self.count_users() > 0

    def ensure_user(self, username: str, *, role: str = "user", email: Optional[str] = None) -> None:
        username = str(username or "").strip()
        if not username:
            return
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_users (username, role, email, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (username) DO UPDATE
                    SET role = COALESCE(NULLIF(EXCLUDED.role, ''), nm_users.role),
                        email = COALESCE(EXCLUDED.email, nm_users.email),
                        updated_at = NOW()
                    """,
                    (username, role or "user", email),
                )

    def ensure_admin_from_env(self) -> None:
        admin_user = (os.getenv("REFINER_ADMIN_USER") or "").strip()
        admin_pass = (
            os.getenv("REFINER_ADMIN_PASS")
            or os.getenv("REFINER_ADMIN_PASSWORD")
            or ""
        ).strip()
        admin_email = (os.getenv("REFINER_ADMIN_EMAIL") or "").strip() or None
        if not admin_user or not admin_pass:
            return
        if self.has_users():
            return
        self.create_user(admin_user, admin_pass, role="admin", email=admin_email)

    def create_user(self, username: str, password: str, role: str = "user", email: Optional[str] = None) -> None:
        username = str(username or "").strip()
        if not username:
            raise ValueError("username is required")
        password_hash = generate_password_hash(password)
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_users (
                        username,
                        password_hash,
                        role,
                        email,
                        external,
                        provider,
                        subject,
                        created_at,
                        updated_at,
                        metadata
                    ) VALUES (%s, %s, %s, %s, FALSE, NULL, NULL, NOW(), NOW(), '{}'::jsonb)
                    ON CONFLICT (username) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash,
                        role = EXCLUDED.role,
                        email = COALESCE(EXCLUDED.email, nm_users.email),
                        external = FALSE,
                        provider = NULL,
                        subject = NULL,
                        updated_at = NOW()
                    """,
                    (username, password_hash, role or "user", email),
                )

    def upsert_external_user(
        self,
        username: str,
        *,
        role: str = "user",
        email: Optional[str] = None,
        provider: str = "oidc",
        subject: Optional[str] = None,
    ) -> None:
        username = str(username or "").strip()
        if not username:
            return
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_users (
                        username,
                        password_hash,
                        role,
                        email,
                        external,
                        provider,
                        subject,
                        created_at,
                        updated_at,
                        metadata
                    ) VALUES (%s, NULL, %s, %s, TRUE, %s, %s, NOW(), NOW(), '{}'::jsonb)
                    ON CONFLICT (username) DO UPDATE
                    SET role = EXCLUDED.role,
                        email = COALESCE(EXCLUDED.email, nm_users.email),
                        external = TRUE,
                        provider = COALESCE(EXCLUDED.provider, nm_users.provider),
                        subject = COALESCE(EXCLUDED.subject, nm_users.subject),
                        updated_at = NOW()
                    """,
                    (username, role or "user", email, provider or None, subject),
                )

    def set_email(self, username: str, email: Optional[str]) -> bool:
        username = str(username or "").strip()
        if not username:
            return False
        with self.store.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE nm_users
                    SET email = %s,
                        updated_at = NOW()
                    WHERE username = %s
                    RETURNING username
                    """,
                    (email, username),
                ).fetchone()
        return bool(row)

    def get_email(self, username: str) -> Optional[str]:
        username = str(username or "").strip()
        if not username:
            return None
        with self.store.pool.connection() as conn:
            row = conn.execute(
                "SELECT email FROM nm_users WHERE username = %s",
                (username,),
            ).fetchone()
        value = (row or {}).get("email")
        return str(value).strip() if value else None

    def verify(self, username: str, password: str) -> bool:
        username = str(username or "").strip()
        if not username:
            return False
        with self.store.pool.connection() as conn:
            row = conn.execute(
                "SELECT password_hash FROM nm_users WHERE username = %s",
                (username,),
            ).fetchone()
        password_hash = (row or {}).get("password_hash")
        if not password_hash:
            return False
        try:
            return check_password_hash(str(password_hash), password)
        except Exception:
            return False

    def get_role(self, username: str) -> Optional[str]:
        username = str(username or "").strip()
        if not username:
            return None
        with self.store.pool.connection() as conn:
            row = conn.execute(
                "SELECT role FROM nm_users WHERE username = %s",
                (username,),
            ).fetchone()
        value = (row or {}).get("role")
        return str(value).strip() if value else None

    def get_metadata(self, username: str) -> Dict[str, Any]:
        username = str(username or "").strip()
        if not username:
            return {}
        with self.store.pool.connection() as conn:
            row = conn.execute(
                "SELECT metadata FROM nm_users WHERE username = %s",
                (username,),
            ).fetchone()
        metadata = (row or {}).get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else {}

    def set_metadata(self, username: str, metadata: Optional[Dict[str, Any]]) -> bool:
        username = str(username or "").strip()
        if not username:
            return False
        with self.store.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE nm_users
                    SET metadata = %s,
                        updated_at = NOW()
                    WHERE username = %s
                    RETURNING username
                    """,
                    (_jsonb(dict(metadata or {})), username),
                ).fetchone()
        return bool(row)


class PostgresAccessTokenStore:
    def __init__(self, store: PostgresCentralStore):
        self.store = store

    def _issue(
        self,
        username: str,
        *,
        kind: str,
        label: Optional[str] = None,
        ttl_seconds: Optional[int] = DEFAULT_ACCESS_TOKEN_TTL_SECONDS,
        meta: Optional[Dict[str, Any]] = None,
        one_time: bool = False,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        username = str(username or "").strip()
        if not username:
            raise ValueError("username is required")
        raw_token = token or secrets.token_urlsafe(32)
        token_id = uuid.uuid4().hex
        expires_at = None
        if ttl_seconds not in (None, ""):
            ttl_value = max(30, int(ttl_seconds))
            expires_at = dt.datetime.now(UTC) + dt.timedelta(seconds=ttl_value)
        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_users (username, role, updated_at)
                    VALUES (%s, 'user', NOW())
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (username,),
                )
                row = conn.execute(
                    """
                    INSERT INTO nm_auth_tokens (
                        id,
                        username,
                        kind,
                        token_hash,
                        token_hint,
                        label,
                        created_at,
                        expires_at,
                        last_used_at,
                        disabled,
                        one_time,
                        meta
                    ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, NULL, FALSE, %s, %s)
                    ON CONFLICT (token_hash) DO UPDATE
                    SET username = EXCLUDED.username,
                        kind = EXCLUDED.kind,
                        token_hint = EXCLUDED.token_hint,
                        label = COALESCE(EXCLUDED.label, nm_auth_tokens.label),
                        expires_at = COALESCE(EXCLUDED.expires_at, nm_auth_tokens.expires_at),
                        disabled = FALSE,
                        one_time = EXCLUDED.one_time,
                        meta = COALESCE(EXCLUDED.meta, nm_auth_tokens.meta)
                    RETURNING id, username, label, created_at, expires_at, kind
                    """,
                    (
                        token_id,
                        username,
                        kind,
                        _hash_token(raw_token),
                        _token_hint(raw_token),
                        label,
                        expires_at,
                        bool(one_time),
                        _jsonb(meta),
                    ),
                ).fetchone()
        return {
            "token": raw_token,
            "id": (row or {}).get("id") or token_id,
            "user": (row or {}).get("username") or username,
            "label": (row or {}).get("label") or label,
            "created_at": _timestamp((row or {}).get("created_at")) or _timestamp(dt.datetime.now(UTC)),
            "expires_at": _timestamp((row or {}).get("expires_at")) or _timestamp(expires_at),
            "kind": (row or {}).get("kind") or kind,
        }

    def issue(
        self,
        username: str,
        *,
        label: Optional[str] = None,
        ttl_seconds: Optional[int] = DEFAULT_ACCESS_TOKEN_TTL_SECONDS,
        meta: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._issue(
            username,
            kind="access",
            label=label,
            ttl_seconds=ttl_seconds,
            meta=meta,
            one_time=False,
            token=token,
        )

    def ensure_token(
        self,
        username: str,
        token: str,
        *,
        label: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._issue(
            username,
            kind="access",
            label=label,
            ttl_seconds=ttl_seconds,
            meta=meta,
            one_time=False,
            token=token,
        )

    def verify(self, token: str) -> Optional[Dict[str, Any]]:
        cleaned = str(token or "").strip()
        if not cleaned:
            return None
        token_hash = _hash_token(cleaned)
        with self.store.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE nm_auth_tokens AS t
                    SET last_used_at = NOW()
                    FROM nm_users AS u
                    WHERE t.token_hash = %s
                      AND t.kind = 'access'
                      AND NOT t.disabled
                      AND NOT t.one_time
                      AND (t.expires_at IS NULL OR t.expires_at > NOW())
                      AND u.username = t.username
                    RETURNING t.id, t.username, t.kind, t.label, t.created_at, t.expires_at, t.meta, u.role
                    """,
                    (token_hash,),
                ).fetchone()
        if not row:
            return None
        return {
            "id": row.get("id"),
            "user": row.get("username"),
            "kind": row.get("kind"),
            "label": row.get("label"),
            "role": row.get("role"),
            "created_at": _timestamp(row.get("created_at")),
            "expires_at": _timestamp(row.get("expires_at")),
            "meta": row.get("meta") if isinstance(row.get("meta"), dict) else {},
        }


class PostgresVoiceTokenStore:
    def __init__(self, store: PostgresCentralStore):
        self.store = store

    def issue(self, user: str, label: Optional[str] = None) -> Dict[str, Any]:
        return self.store.access_tokens._issue(
            user,
            kind="voice",
            label=label,
            ttl_seconds=None,
            meta={"purpose": "voice"},
            one_time=False,
        )

    def verify(self, token: str) -> Optional[str]:
        cleaned = str(token or "").strip()
        if not cleaned:
            return None
        token_hash = _hash_token(cleaned)
        with self.store.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE nm_auth_tokens
                    SET last_used_at = NOW()
                    WHERE token_hash = %s
                      AND kind = 'voice'
                      AND NOT disabled
                      AND NOT one_time
                      AND (expires_at IS NULL OR expires_at > NOW())
                    RETURNING username
                    """,
                    (token_hash,),
                ).fetchone()
        username = (row or {}).get("username")
        return str(username).strip() if username else None

    def list_tokens(self, user: Optional[str] = None) -> List[Dict[str, Any]]:
        params: List[Any] = []
        sql = """
            SELECT id, username, label, created_at, last_used_at, disabled
            FROM nm_auth_tokens
            WHERE kind = 'voice'
        """
        if user:
            sql += " AND username = %s"
            params.append(user)
        sql += " ORDER BY created_at DESC"
        with self.store.pool.connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            {
                "id": row.get("id"),
                "user": row.get("username"),
                "label": row.get("label"),
                "created_at": _timestamp(row.get("created_at")),
                "last_used_at": _timestamp(row.get("last_used_at")),
                "disabled": bool(row.get("disabled")),
            }
            for row in rows or []
        ]

    def revoke(self, token_id: str) -> bool:
        cleaned = str(token_id or "").strip()
        if not cleaned:
            return False
        with self.store.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE nm_auth_tokens
                    SET disabled = TRUE
                    WHERE id = %s AND kind = 'voice'
                    RETURNING id
                    """,
                    (cleaned,),
                ).fetchone()
        return bool(row)


class PostgresSsoStore:
    type_name = "postgres"

    def __init__(self, store: PostgresCentralStore, ttl_seconds: int = DEFAULT_SSO_TOKEN_TTL_SECONDS):
        self.store = store
        self.ttl_seconds = max(30, int(ttl_seconds))

    def issue(self, user: str) -> str:
        issued = self.store.access_tokens._issue(
            user,
            kind="sso",
            ttl_seconds=self.ttl_seconds,
            meta={"purpose": "sso"},
            one_time=True,
        )
        return str(issued.get("token") or "")

    def consume(self, token: str) -> Optional[str]:
        cleaned = str(token or "").strip()
        if not cleaned:
            return None
        token_hash = _hash_token(cleaned)
        with self.store.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    DELETE FROM nm_auth_tokens
                    WHERE token_hash = %s
                      AND kind = 'sso'
                      AND NOT disabled
                      AND one_time
                      AND (expires_at IS NULL OR expires_at > NOW())
                    RETURNING username
                    """,
                    (token_hash,),
                ).fetchone()
        username = (row or {}).get("username")
        return str(username).strip() if username else None

    def health(self) -> Dict[str, Any]:
        try:
            with self.store.pool.connection() as conn:
                conn.execute("SELECT 1")
            return {"type": self.type_name, "ok": True}
        except Exception as exc:
            return {"type": self.type_name, "ok": False, "error": str(exc)}


class PostgresTokenLedger:
    def __init__(self, store: PostgresCentralStore, scope: str):
        self.store = store
        self.scope = scope

    def _account_summary(self, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        summary = _default_account_summary()
        if not row:
            return summary
        summary.update(
            {
                "balance": int(row.get("balance") or 0),
                "paid_balance": int(row.get("paid_balance") or 0),
                "free_balance": int(row.get("free_balance") or 0),
                "last_topup_tokens": int(row.get("last_topup_tokens") or 0),
                "last_topup_at": _timestamp(row.get("last_topup_at")),
                "updated_at": _timestamp(row.get("updated_at")),
                "spent_total": int(row.get("spent_total") or 0),
                "cashout_total": int(row.get("cashout_total") or 0),
                "shortfall_total": int(row.get("shortfall_total") or 0),
                "free_grant_total": int(row.get("free_grant_total") or 0),
            }
        )
        return summary

    def get_summary(self, account_id: str) -> Dict[str, Any]:
        cleaned = str(account_id or "").strip()
        if not cleaned:
            return _default_account_summary()
        with self.store.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM nm_token_accounts
                WHERE scope = %s AND account_id = %s
                """,
                (self.scope, cleaned),
            ).fetchone()
        return self._account_summary(row)

    def list_entries(self, account_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        cleaned = str(account_id or "").strip()
        if not cleaned:
            return []
        limit_value = max(1, min(int(limit), 500))
        with self.store.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT ts, entry_type, delta, balance_after, meta
                FROM nm_token_ledger_entries
                WHERE scope = %s AND account_id = %s
                ORDER BY ts DESC, id DESC
                LIMIT %s
                """,
                (self.scope, cleaned, limit_value),
            ).fetchall()
        return [
            {
                "ts": _timestamp(row.get("ts")),
                "type": row.get("entry_type"),
                "user": cleaned,
                "delta": int(row.get("delta") or 0),
                "balance_after": int(row.get("balance_after") or 0),
                "meta": row.get("meta") if isinstance(row.get("meta"), dict) else {},
                "shortfall": int(((row.get("meta") if isinstance(row.get("meta"), dict) else {}) or {}).get("shortfall") or 0),
            }
            for row in rows or []
        ]

    def record(
        self,
        account_id: str,
        entry_type: str,
        delta: int,
        meta: Optional[Dict[str, Any]] = None,
        *,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cleaned = str(account_id or "").strip()
        if not cleaned:
            raise ValueError("account_id is required")
        meta_dict = dict(meta or {})
        with self.store.pool.connection() as conn:
            with conn.transaction():
                if request_id:
                    existing = conn.execute(
                        """
                        SELECT ts, entry_type, delta, balance_after, meta
                        FROM nm_token_ledger_entries
                        WHERE scope = %s AND account_id = %s AND request_id = %s
                        """,
                        (self.scope, cleaned, request_id),
                    ).fetchone()
                    if existing:
                        existing_meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
                        return {
                            "ts": _timestamp(existing.get("ts")),
                            "type": existing.get("entry_type"),
                            "user": cleaned,
                            "delta": int(existing.get("delta") or 0),
                            "balance_after": int(existing.get("balance_after") or 0),
                            "meta": existing_meta,
                            "shortfall": int((existing_meta or {}).get("shortfall") or 0),
                        }

                conn.execute(
                    """
                    INSERT INTO nm_token_accounts (scope, account_id)
                    VALUES (%s, %s)
                    ON CONFLICT (scope, account_id) DO NOTHING
                    """,
                    (self.scope, cleaned),
                )
                account = conn.execute(
                    """
                    SELECT *
                    FROM nm_token_accounts
                    WHERE scope = %s AND account_id = %s
                    FOR UPDATE
                    """,
                    (self.scope, cleaned),
                ).fetchone()
                summary = self._account_summary(account)
                paid_balance = int(summary.get("paid_balance") or summary.get("balance") or 0)
                free_balance = int(summary.get("free_balance") or 0)
                balance = paid_balance + free_balance
                requested_delta = int(delta or 0)
                new_paid = paid_balance
                new_free = free_balance
                shortfall = 0
                kind = str(entry_type or "adjust").strip().lower() or "adjust"

                if kind == "topup":
                    if requested_delta > 0:
                        new_paid += requested_delta
                    else:
                        requested_delta = 0
                elif kind == "refund":
                    if requested_delta > 0:
                        new_paid += requested_delta
                    else:
                        requested_delta = 0
                elif kind == "grant":
                    if requested_delta > 0:
                        new_free += requested_delta
                    else:
                        requested_delta = 0
                elif kind == "cashout":
                    if requested_delta >= 0:
                        requested_delta = -abs(requested_delta)
                    desired = abs(requested_delta)
                    paid_used = min(new_paid, desired)
                    new_paid -= paid_used
                    shortfall = desired - paid_used
                    if shortfall:
                        meta_dict["shortfall"] = shortfall
                    requested_delta = -paid_used
                    meta_dict["paid_used"] = paid_used
                    meta_dict["free_used"] = 0
                    meta_dict["used_total"] = paid_used
                elif kind == "debit":
                    if requested_delta >= 0:
                        requested_delta = -abs(requested_delta or 0)
                    desired = abs(requested_delta)
                    free_used = min(new_free, desired)
                    new_free -= free_used
                    remaining = desired - free_used
                    paid_used = min(new_paid, remaining)
                    new_paid -= paid_used
                    shortfall = remaining - paid_used
                    if shortfall:
                        meta_dict["shortfall"] = shortfall
                    meta_dict["free_used"] = free_used
                    meta_dict["paid_used"] = paid_used
                    meta_dict["used_total"] = free_used + paid_used
                    requested_delta = -(free_used + paid_used)
                elif kind in {"reserve", "release"}:
                    requested_delta = 0
                elif kind == "sync":
                    target_paid = meta_dict.get("target_paid_balance")
                    target_free = meta_dict.get("target_free_balance")
                    target_balance = meta_dict.get("target_balance")
                    if target_paid is not None or target_free is not None:
                        if target_paid is not None:
                            new_paid = max(0, int(target_paid or 0))
                        if target_free is not None:
                            new_free = max(0, int(target_free or 0))
                    else:
                        if target_balance is None:
                            target_balance = balance + requested_delta
                        try:
                            target_balance = int(float(target_balance))
                        except Exception:
                            target_balance = balance
                        target_balance = max(0, target_balance)
                        if target_balance >= new_free:
                            new_paid = target_balance - new_free
                        else:
                            new_free = target_balance
                            new_paid = 0
                    requested_delta = (new_paid + new_free) - balance

                if requested_delta == 0 and kind not in {"reserve", "release", "sync"}:
                    kind = "adjust"

                new_balance = max(0, new_paid + new_free)
                meta_dict["paid_after"] = new_paid
                meta_dict["free_after"] = new_free

                now = dt.datetime.now(UTC)
                spent_total = int(summary.get("spent_total") or 0)
                cashout_total = int(summary.get("cashout_total") or 0)
                shortfall_total = int(summary.get("shortfall_total") or 0)
                free_grant_total = int(summary.get("free_grant_total") or 0)
                last_topup_tokens = int(summary.get("last_topup_tokens") or 0)
                last_topup_at = account.get("last_topup_at") if account else None

                if kind == "topup":
                    last_topup_tokens = int((meta_dict or {}).get("tokens") or abs(requested_delta) or 0)
                    last_topup_at = now
                if kind == "sync":
                    capacity = meta_dict.get("capacity")
                    if capacity is not None:
                        try:
                            last_topup_tokens = int(capacity or 0)
                            last_topup_at = now
                        except Exception:
                            pass
                if kind == "debit":
                    spent_total += int(meta_dict.get("used_total") or abs(requested_delta) or 0)
                    shortfall_total += int(meta_dict.get("shortfall") or 0)
                if kind == "cashout":
                    cashout_total += abs(requested_delta)
                if kind == "grant":
                    free_grant_total += abs(requested_delta)

                entry_row = conn.execute(
                    """
                    INSERT INTO nm_token_ledger_entries (
                        scope,
                        account_id,
                        ts,
                        entry_type,
                        delta,
                        balance_after,
                        meta,
                        request_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING ts, entry_type, delta, balance_after, meta
                    """,
                    (
                        self.scope,
                        cleaned,
                        now,
                        kind,
                        requested_delta,
                        new_balance,
                        _jsonb(meta_dict),
                        request_id,
                    ),
                ).fetchone()
                conn.execute(
                    """
                    UPDATE nm_token_accounts
                    SET balance = %s,
                        paid_balance = %s,
                        free_balance = %s,
                        last_topup_tokens = %s,
                        last_topup_at = %s,
                        updated_at = %s,
                        spent_total = %s,
                        cashout_total = %s,
                        shortfall_total = %s,
                        free_grant_total = %s
                    WHERE scope = %s AND account_id = %s
                    """,
                    (
                        new_balance,
                        new_paid,
                        new_free,
                        last_topup_tokens,
                        last_topup_at,
                        now,
                        spent_total,
                        cashout_total,
                        shortfall_total,
                        free_grant_total,
                        self.scope,
                        cleaned,
                    ),
                )
        entry_meta = entry_row.get("meta") if isinstance(entry_row.get("meta"), dict) else meta_dict
        return {
            "ts": _timestamp(entry_row.get("ts")),
            "type": entry_row.get("entry_type"),
            "user": cleaned,
            "delta": int(entry_row.get("delta") or 0),
            "balance_after": int(entry_row.get("balance_after") or 0),
            "meta": entry_meta,
            "shortfall": int((entry_meta or {}).get("shortfall") or 0),
        }


class PostgresLLMRequestTelemetry:
    """Hourly rollups for provider latency and success telemetry."""

    def __init__(self, store: PostgresCentralStore):
        self.store = store

    def record(self, scope: str, subject: str, event: Optional[Dict[str, Any]]) -> None:
        scope_value = _clean_dimension(scope, default="user", max_length=32).lower() or "user"
        subject_value = _clean_dimension(subject, default="", max_length=120)
        if not subject_value:
            raise ValueError("subject is required")
        payload = dict(event or {})
        provider = _clean_dimension(payload.get("provider"), default="unknown", max_length=64)
        model = _clean_dimension(payload.get("model"), default="unknown", max_length=128)
        category = _clean_dimension(payload.get("category"), default="llm", max_length=64) or "llm"
        outcome = _clean_dimension(payload.get("outcome"), default="error", max_length=32).lower() or "error"
        if outcome not in {"success", "quota_error", "error"}:
            outcome = "error"
        latency_ms = max(0, _coerce_int(payload.get("latency_ms"), 0))
        input_chars = max(0, _coerce_int(payload.get("input_chars"), 0))
        estimated_input_tokens = max(0, _coerce_int(payload.get("estimated_input_tokens"), 0))
        event_at = _coerce_event_datetime(payload.get("at") or payload.get("ts"))
        bucket_hour = _hour_bucket(event_at)
        error_class = _clean_optional_text(payload.get("error_class"), max_length=96)
        error_detail = _clean_optional_text(payload.get("error_detail"), max_length=240)
        max_tokens = _coerce_int(payload.get("max_tokens"), 0)
        max_tokens_value = max_tokens if max_tokens > 0 else None
        reasoning_effort = _clean_optional_text(payload.get("reasoning_effort"), max_length=32)
        success_count = 1 if outcome == "success" else 0
        quota_count = 1 if outcome == "quota_error" else 0
        error_count = 1 if outcome == "error" else 0

        with self.store.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_llm_request_telemetry (
                        scope,
                        subject,
                        bucket_hour,
                        provider,
                        model,
                        category,
                        requests,
                        successes,
                        quota_errors,
                        errors,
                        latency_ms_total,
                        latency_ms_min,
                        latency_ms_max,
                        input_chars_total,
                        estimated_input_tokens_total,
                        last_outcome,
                        last_error_class,
                        last_error_detail,
                        last_event_at,
                        last_max_tokens,
                        last_reasoning_effort,
                        updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        1, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        NOW()
                    )
                    ON CONFLICT (scope, subject, bucket_hour, provider, model, category) DO UPDATE
                    SET requests = nm_llm_request_telemetry.requests + 1,
                        successes = nm_llm_request_telemetry.successes + EXCLUDED.successes,
                        quota_errors = nm_llm_request_telemetry.quota_errors + EXCLUDED.quota_errors,
                        errors = nm_llm_request_telemetry.errors + EXCLUDED.errors,
                        latency_ms_total = nm_llm_request_telemetry.latency_ms_total + EXCLUDED.latency_ms_total,
                        latency_ms_min = CASE
                            WHEN nm_llm_request_telemetry.latency_ms_min IS NULL THEN EXCLUDED.latency_ms_min
                            WHEN EXCLUDED.latency_ms_min IS NULL THEN nm_llm_request_telemetry.latency_ms_min
                            ELSE LEAST(nm_llm_request_telemetry.latency_ms_min, EXCLUDED.latency_ms_min)
                        END,
                        latency_ms_max = CASE
                            WHEN nm_llm_request_telemetry.latency_ms_max IS NULL THEN EXCLUDED.latency_ms_max
                            WHEN EXCLUDED.latency_ms_max IS NULL THEN nm_llm_request_telemetry.latency_ms_max
                            ELSE GREATEST(nm_llm_request_telemetry.latency_ms_max, EXCLUDED.latency_ms_max)
                        END,
                        input_chars_total = nm_llm_request_telemetry.input_chars_total + EXCLUDED.input_chars_total,
                        estimated_input_tokens_total = nm_llm_request_telemetry.estimated_input_tokens_total + EXCLUDED.estimated_input_tokens_total,
                        last_outcome = CASE
                            WHEN nm_llm_request_telemetry.last_event_at IS NULL
                              OR EXCLUDED.last_event_at IS NULL
                              OR EXCLUDED.last_event_at >= nm_llm_request_telemetry.last_event_at
                            THEN EXCLUDED.last_outcome
                            ELSE nm_llm_request_telemetry.last_outcome
                        END,
                        last_error_class = CASE
                            WHEN nm_llm_request_telemetry.last_event_at IS NULL
                              OR EXCLUDED.last_event_at IS NULL
                              OR EXCLUDED.last_event_at >= nm_llm_request_telemetry.last_event_at
                            THEN COALESCE(EXCLUDED.last_error_class, nm_llm_request_telemetry.last_error_class)
                            ELSE nm_llm_request_telemetry.last_error_class
                        END,
                        last_error_detail = CASE
                            WHEN nm_llm_request_telemetry.last_event_at IS NULL
                              OR EXCLUDED.last_event_at IS NULL
                              OR EXCLUDED.last_event_at >= nm_llm_request_telemetry.last_event_at
                            THEN COALESCE(EXCLUDED.last_error_detail, nm_llm_request_telemetry.last_error_detail)
                            ELSE nm_llm_request_telemetry.last_error_detail
                        END,
                        last_event_at = CASE
                            WHEN nm_llm_request_telemetry.last_event_at IS NULL THEN EXCLUDED.last_event_at
                            WHEN EXCLUDED.last_event_at IS NULL THEN nm_llm_request_telemetry.last_event_at
                            ELSE GREATEST(nm_llm_request_telemetry.last_event_at, EXCLUDED.last_event_at)
                        END,
                        last_max_tokens = CASE
                            WHEN nm_llm_request_telemetry.last_event_at IS NULL
                              OR EXCLUDED.last_event_at IS NULL
                              OR EXCLUDED.last_event_at >= nm_llm_request_telemetry.last_event_at
                            THEN COALESCE(EXCLUDED.last_max_tokens, nm_llm_request_telemetry.last_max_tokens)
                            ELSE nm_llm_request_telemetry.last_max_tokens
                        END,
                        last_reasoning_effort = CASE
                            WHEN nm_llm_request_telemetry.last_event_at IS NULL
                              OR EXCLUDED.last_event_at IS NULL
                              OR EXCLUDED.last_event_at >= nm_llm_request_telemetry.last_event_at
                            THEN COALESCE(EXCLUDED.last_reasoning_effort, nm_llm_request_telemetry.last_reasoning_effort)
                            ELSE nm_llm_request_telemetry.last_reasoning_effort
                        END,
                        updated_at = NOW()
                    """,
                    (
                        scope_value,
                        subject_value,
                        bucket_hour,
                        provider,
                        model,
                        category,
                        success_count,
                        quota_count,
                        error_count,
                        latency_ms,
                        latency_ms,
                        latency_ms,
                        input_chars,
                        estimated_input_tokens,
                        outcome,
                        error_class,
                        error_detail,
                        event_at,
                        max_tokens_value,
                        reasoning_effort,
                    ),
                )

    def summary(
        self,
        *,
        scope: Optional[str] = None,
        subject: Optional[str] = None,
        hours: int = 72,
        limit: int = 20,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        category: Optional[str] = None,
        include_subjects: bool = False,
        subject_limit: int = 12,
    ) -> Dict[str, Any]:
        scope_value = _clean_dimension(scope, default="", max_length=32).lower() if scope is not None else None
        subject_value = _clean_dimension(subject, default="", max_length=120) if subject is not None else None
        provider_value = _clean_dimension(provider, default="", max_length=64) if provider is not None else None
        model_value = _clean_dimension(model, default="", max_length=128) if model is not None else None
        category_value = _clean_dimension(category, default="", max_length=64) if category is not None else None
        hours_value = max(1, _coerce_int(hours, 72))
        limit_value = max(1, min(_coerce_int(limit, 20), 100))
        subject_limit_value = max(1, min(_coerce_int(subject_limit, 12), 100))
        now = dt.datetime.now(UTC)
        window_start = now - dt.timedelta(hours=hours_value)
        filters = (
            window_start,
            scope_value,
            scope_value,
            subject_value,
            subject_value,
            provider_value,
            provider_value,
            model_value,
            model_value,
            category_value,
            category_value,
        )

        def _summary_payload(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            payload = {
                "requests": _coerce_int((row or {}).get("requests"), 0),
                "successes": _coerce_int((row or {}).get("successes"), 0),
                "quota_errors": _coerce_int((row or {}).get("quota_errors"), 0),
                "errors": _coerce_int((row or {}).get("errors"), 0),
                "latency_ms_total": _coerce_int((row or {}).get("latency_ms_total"), 0),
                "latency_ms_min": _coerce_int((row or {}).get("latency_ms_min"), 0) if (row or {}).get("latency_ms_min") is not None else None,
                "latency_ms_max": _coerce_int((row or {}).get("latency_ms_max"), 0) if (row or {}).get("latency_ms_max") is not None else None,
                "input_chars_total": _coerce_int((row or {}).get("input_chars_total"), 0),
                "estimated_input_tokens_total": _coerce_int((row or {}).get("estimated_input_tokens_total"), 0),
                "last_outcome": (row or {}).get("last_outcome"),
                "last_error_class": (row or {}).get("last_error_class"),
                "last_error_detail": (row or {}).get("last_error_detail"),
                "last_event_at": _timestamp((row or {}).get("last_event_at")),
                "last_max_tokens": _coerce_int((row or {}).get("last_max_tokens"), 0) if (row or {}).get("last_max_tokens") is not None else None,
                "last_reasoning_effort": (row or {}).get("last_reasoning_effort"),
            }
            requests = payload["requests"]
            payload["avg_latency_ms"] = round(payload["latency_ms_total"] / requests, 2) if requests else None
            payload["success_rate"] = round(payload["successes"] / requests, 4) if requests else None
            return payload

        with self.store.pool.connection() as conn:
            totals_row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(requests), 0) AS requests,
                    COALESCE(SUM(successes), 0) AS successes,
                    COALESCE(SUM(quota_errors), 0) AS quota_errors,
                    COALESCE(SUM(errors), 0) AS errors,
                    COALESCE(SUM(latency_ms_total), 0) AS latency_ms_total,
                    MIN(latency_ms_min) AS latency_ms_min,
                    MAX(latency_ms_max) AS latency_ms_max,
                    COALESCE(SUM(input_chars_total), 0) AS input_chars_total,
                    COALESCE(SUM(estimated_input_tokens_total), 0) AS estimated_input_tokens_total
                FROM nm_llm_request_telemetry
                WHERE bucket_hour >= %s
                  AND (%s IS NULL OR scope = %s)
                  AND (%s IS NULL OR subject = %s)
                  AND (%s IS NULL OR provider = %s)
                  AND (%s IS NULL OR model = %s)
                  AND (%s IS NULL OR category = %s)
                """,
                filters,
            ).fetchone()
            rows = conn.execute(
                """
                WITH filtered AS (
                    SELECT *
                    FROM nm_llm_request_telemetry
                    WHERE bucket_hour >= %s
                      AND (%s IS NULL OR scope = %s)
                      AND (%s IS NULL OR subject = %s)
                      AND (%s IS NULL OR provider = %s)
                      AND (%s IS NULL OR model = %s)
                      AND (%s IS NULL OR category = %s)
                ),
                aggregated AS (
                    SELECT
                        provider,
                        model,
                        category,
                        SUM(requests) AS requests,
                        SUM(successes) AS successes,
                        SUM(quota_errors) AS quota_errors,
                        SUM(errors) AS errors,
                        SUM(latency_ms_total) AS latency_ms_total,
                        MIN(latency_ms_min) AS latency_ms_min,
                        MAX(latency_ms_max) AS latency_ms_max,
                        SUM(input_chars_total) AS input_chars_total,
                        SUM(estimated_input_tokens_total) AS estimated_input_tokens_total
                    FROM filtered
                    GROUP BY provider, model, category
                ),
                latest AS (
                    SELECT DISTINCT ON (provider, model, category)
                        provider,
                        model,
                        category,
                        last_outcome,
                        last_error_class,
                        last_error_detail,
                        last_event_at,
                        last_max_tokens,
                        last_reasoning_effort
                    FROM filtered
                    ORDER BY provider, model, category, last_event_at DESC NULLS LAST, updated_at DESC
                )
                SELECT
                    aggregated.*,
                    latest.last_outcome,
                    latest.last_error_class,
                    latest.last_error_detail,
                    latest.last_event_at,
                    latest.last_max_tokens,
                    latest.last_reasoning_effort
                FROM aggregated
                LEFT JOIN latest USING (provider, model, category)
                ORDER BY aggregated.requests DESC, aggregated.latency_ms_total DESC, aggregated.provider ASC, aggregated.model ASC
                LIMIT %s
                """,
                (*filters, limit_value),
            ).fetchall()
            subject_rows: List[Dict[str, Any]] = []
            if include_subjects:
                subject_rows = conn.execute(
                    """
                    SELECT
                        subject,
                        scope,
                        SUM(requests) AS requests,
                        SUM(successes) AS successes,
                        SUM(quota_errors) AS quota_errors,
                        SUM(errors) AS errors,
                        SUM(latency_ms_total) AS latency_ms_total,
                        MIN(latency_ms_min) AS latency_ms_min,
                        MAX(latency_ms_max) AS latency_ms_max,
                        MAX(last_event_at) AS last_event_at
                    FROM nm_llm_request_telemetry
                    WHERE bucket_hour >= %s
                      AND (%s IS NULL OR scope = %s)
                      AND (%s IS NULL OR subject = %s)
                      AND (%s IS NULL OR provider = %s)
                      AND (%s IS NULL OR model = %s)
                      AND (%s IS NULL OR category = %s)
                    GROUP BY subject, scope
                    ORDER BY SUM(requests) DESC, MAX(last_event_at) DESC NULLS LAST, subject ASC
                    LIMIT %s
                    """,
                    (*filters, subject_limit_value),
                ).fetchall()

        groups: List[Dict[str, Any]] = []
        for row in rows or []:
            item = _summary_payload(row)
            item["provider"] = row.get("provider")
            item["model"] = row.get("model")
            item["category"] = row.get("category")
            groups.append(item)
        subjects: List[Dict[str, Any]] = []
        for row in subject_rows or []:
            item = _summary_payload(row)
            item["subject"] = row.get("subject")
            item["scope"] = row.get("scope")
            subjects.append(item)
        return {
            "enabled": True,
            "scope": scope_value,
            "subject": subject_value,
            "provider": provider_value,
            "model": model_value,
            "category": category_value,
            "window_hours": hours_value,
            "window_start": _timestamp(window_start),
            "generated_at": _timestamp(now),
            "totals": _summary_payload(totals_row),
            "groups": groups,
            "subjects": subjects,
        }

    def prune_older_than(self, hours: int, *, now: Optional[dt.datetime] = None) -> int:
        hours_value = max(0, _coerce_int(hours, 0))
        if hours_value <= 0:
            return 0
        cutoff = _coerce_event_datetime(now) - dt.timedelta(hours=hours_value)
        with self.store.pool.connection() as conn:
            with conn.transaction():
                result = conn.execute(
                    "DELETE FROM nm_llm_request_telemetry WHERE bucket_hour < %s",
                    (cutoff,),
                )
        return max(0, int(result.rowcount or 0))


def create_central_store_from_env() -> Optional[PostgresCentralStore]:
    dsn = central_store_dsn_from_env()
    if not dsn:
        return None
    min_size = int(os.getenv("REFINER_AUTH_DB_POOL_MIN", "1"))
    max_size = int(os.getenv("REFINER_AUTH_DB_POOL_MAX", "4"))
    timeout = float(os.getenv("REFINER_AUTH_DB_POOL_TIMEOUT", "10"))
    return PostgresCentralStore(dsn, min_size=min_size, max_size=max_size, timeout=timeout)
