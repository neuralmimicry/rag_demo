"""Minimal JSON-RPC MCP client plus durable per-owner server registries.

The module keeps the runtime RPC client intentionally small while offering two
registry backends:

- ``MCPServerStore`` for file-backed shared-storage persistence, and
- ``PostgresMCPServerStore`` for multi-instance deployments sharing Postgres.

Registry entries are designed to store secret *references* rather than raw
credentials. ``auth_token`` and ``headers`` remain available on the config
object so callers can resolve secrets at runtime before instantiating
``MCPClient``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from security_utils import ensure_dir_permissions, ensure_file_permissions

try:
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - exercised only when psycopg extras are unavailable
    Jsonb = None

UTC = dt.timezone.utc


def _now_iso() -> str:
    return dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonb(value: Optional[Dict[str, Any]] = None) -> Any:
    payload = dict(value or {})
    if Jsonb is None:
        return payload
    return Jsonb(payload)


def _sanitize_owner(owner: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", owner or "default")


def _deep_copy_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict(value or {})


def _timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return current.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cleaned = str(value).strip()
    return cleaned or None


def _merge_runtime(current: Optional[Dict[str, Any]], patch: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = _deep_copy_dict(current)
    for key, value in dict(patch or {}).items():
        if value is None:
            merged.pop(str(key), None)
            continue
        merged[str(key)] = value
    return merged


@dataclass
class MCPServerConfig:
    """Connection settings and operator-visible state for one MCP server."""

    name: str
    base_url: str
    auth_type: str = "bearer"
    auth_token: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    timeout: int = 20
    auth_secret_ref: Optional[str] = None
    headers_secret_ref: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    runtime: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def _internal_metadata(self) -> Dict[str, Any]:
        payload = self.metadata if isinstance(self.metadata, dict) else {}
        internal = payload.get("_refiner")
        return dict(internal) if isinstance(internal, dict) else {}

    def public_metadata(self) -> Dict[str, Any]:
        """Return metadata with Refiner's internal bookkeeping removed."""
        payload = self.metadata if isinstance(self.metadata, dict) else {}
        return {key: value for key, value in payload.items() if key != "_refiner"}

    def header_keys(self) -> List[str]:
        """Return stable header names without exposing values."""
        keys: List[str] = []
        if isinstance(self.headers, dict):
            keys.extend(str(key).strip() for key in self.headers.keys() if str(key).strip())
        internal = self._internal_metadata()
        stored = internal.get("header_keys")
        if isinstance(stored, list):
            keys.extend(str(key).strip() for key in stored if str(key).strip())
        return sorted(set(keys))

    def masked(self) -> Dict[str, Any]:
        """Return a safe representation with secrets hidden."""
        return {
            "name": self.name,
            "base_url": self.base_url,
            "auth_type": self.auth_type,
            "has_token": bool(self.auth_token or self.auth_secret_ref),
            "auth_secret_ref": self.auth_secret_ref,
            "headers_secret_ref": self.headers_secret_ref,
            "headers": {key: "***" for key in self.header_keys()},
            "timeout": self.timeout,
            "metadata": self.public_metadata(),
            "runtime": _deep_copy_dict(self.runtime),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_record(self, *, persist_plain_headers: bool = False) -> Dict[str, Any]:
        """Serialize the persisted representation without raw auth tokens."""
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        internal = self._internal_metadata()
        header_keys = self.header_keys()
        if header_keys:
            internal["header_keys"] = header_keys
        elif "header_keys" in internal:
            internal.pop("header_keys", None)
        if internal:
            metadata = dict(metadata)
            metadata["_refiner"] = internal
        elif "_refiner" in metadata:
            metadata = dict(metadata)
            metadata.pop("_refiner", None)

        payload: Dict[str, Any] = {
            "name": self.name,
            "base_url": self.base_url,
            "auth_type": self.auth_type or "bearer",
            "auth_secret_ref": self.auth_secret_ref,
            "headers_secret_ref": self.headers_secret_ref,
            "timeout": int(self.timeout or 20),
            "metadata": metadata,
            "runtime": _deep_copy_dict(self.runtime),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if persist_plain_headers and isinstance(self.headers, dict):
            payload["headers"] = {str(key): str(value) for key, value in self.headers.items()}
        return payload

    @classmethod
    def from_record(cls, entry: Dict[str, Any]) -> "MCPServerConfig":
        """Deserialize a stored config while tolerating legacy plaintext fields."""
        headers = entry.get("headers") if isinstance(entry.get("headers"), dict) else None
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        runtime = entry.get("runtime") if isinstance(entry.get("runtime"), dict) else {}
        return cls(
            name=str(entry.get("name") or ""),
            base_url=str(entry.get("base_url") or ""),
            auth_type=str(entry.get("auth_type") or "bearer"),
            auth_token=entry.get("auth_token"),
            headers={str(key): str(value) for key, value in headers.items()} if headers else None,
            timeout=int(entry.get("timeout") or 20),
            auth_secret_ref=str(entry.get("auth_secret_ref") or "").strip() or None,
            headers_secret_ref=str(entry.get("headers_secret_ref") or "").strip() or None,
            metadata=_deep_copy_dict(metadata),
            runtime=_deep_copy_dict(runtime),
            created_at=_timestamp(entry.get("created_at")),
            updated_at=_timestamp(entry.get("updated_at")),
        )


class MCPClient:
    """Thin JSON-RPC 2.0 client for MCP servers."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.headers:
            headers.update(self.config.headers)
        if self.config.auth_token:
            if self.config.auth_type in {"bearer", "oauth"}:
                headers["Authorization"] = f"Bearer {self.config.auth_token}"
        return headers

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": method,
            "params": params or {},
        }
        try:
            resp = self._session.post(
                self.config.base_url,
                headers=self._headers(),
                json=payload,
                timeout=self.config.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"MCP request failed: {exc}") from exc
        if resp.status_code >= 300:
            raise RuntimeError(f"MCP server error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"MCP error: {data.get('error')}")
        return data

    def initialize(self, client_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._rpc("initialize", {"client": client_info or {}})

    def list_tools(self) -> Dict[str, Any]:
        return self._rpc("tools/list")

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def list_resources(self) -> Dict[str, Any]:
        return self._rpc("resources/list")

    def read_resource(self, uri: str) -> Dict[str, Any]:
        return self._rpc("resources/read", {"uri": uri})


class MCPServerStore:
    """JSON-file store for per-owner MCP server configs."""

    def __init__(self, root: str):
        self.root = root
        ensure_dir_permissions(self.root, mode=0o700)

    def _path_for(self, owner: str) -> str:
        return os.path.join(self.root, f"{_sanitize_owner(owner)}.json")

    def _load_payload(self, owner: str) -> Dict[str, Any]:
        path = self._path_for(owner)
        if not os.path.exists(path):
            return {"servers": []}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {"servers": []}
        if not isinstance(payload, dict):
            return {"servers": []}
        items = payload.get("servers")
        if not isinstance(items, list):
            payload["servers"] = []
        return payload

    def _write_payload(self, owner: str, payload: Dict[str, Any]) -> None:
        path = self._path_for(owner)
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp, path)
            ensure_file_permissions(path, mode=0o600)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def list_servers(self, owner: str) -> List[MCPServerConfig]:
        payload = self._load_payload(owner)
        items = payload.get("servers") if isinstance(payload.get("servers"), list) else []
        results: List[MCPServerConfig] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            config = MCPServerConfig.from_record(entry)
            if not config.name:
                continue
            results.append(config)
        return sorted(results, key=lambda item: item.name.lower())

    def save_server(self, owner: str, config: MCPServerConfig) -> None:
        now = _now_iso()
        payload = self._load_payload(owner)
        items = payload.get("servers") if isinstance(payload.get("servers"), list) else []
        next_items: List[Dict[str, Any]] = []
        created_at = config.created_at or now
        replaced = False
        for entry in items:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name") or "") == config.name:
                created_at = str(entry.get("created_at") or "").strip() or created_at
                replaced = True
                continue
            next_items.append(entry)
        stored = MCPServerConfig(
            name=config.name,
            base_url=config.base_url,
            auth_type=config.auth_type,
            headers=config.headers,
            timeout=config.timeout,
            auth_secret_ref=config.auth_secret_ref,
            headers_secret_ref=config.headers_secret_ref,
            metadata=_deep_copy_dict(config.metadata),
            runtime=_deep_copy_dict(config.runtime),
            created_at=created_at,
            updated_at=now,
        )
        if not replaced and not stored.created_at:
            stored.created_at = now
        next_items.append(stored.to_record())
        payload["servers"] = sorted(next_items, key=lambda item: str(item.get("name") or "").lower())
        self._write_payload(owner, payload)

    def delete_server(self, owner: str, name: str) -> bool:
        payload = self._load_payload(owner)
        items = payload.get("servers") if isinstance(payload.get("servers"), list) else []
        next_items = [entry for entry in items if isinstance(entry, dict) and str(entry.get("name") or "") != name]
        if len(next_items) == len(items):
            return False
        payload["servers"] = next_items
        self._write_payload(owner, payload)
        return True

    def get_server(self, owner: str, name: str) -> Optional[MCPServerConfig]:
        for server in self.list_servers(owner):
            if server.name == name:
                return server
        return None

    def update_runtime(self, owner: str, name: str, runtime_patch: Dict[str, Any]) -> bool:
        payload = self._load_payload(owner)
        items = payload.get("servers") if isinstance(payload.get("servers"), list) else []
        updated = False
        now = _now_iso()
        next_items: List[Dict[str, Any]] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name") or "") != name:
                next_items.append(entry)
                continue
            config = MCPServerConfig.from_record(entry)
            config.runtime = _merge_runtime(config.runtime, runtime_patch)
            config.updated_at = now
            next_items.append(config.to_record())
            updated = True
        if updated:
            payload["servers"] = next_items
            self._write_payload(owner, payload)
        return updated


class PostgresMCPServerStore:
    """Postgres-backed MCP registry for multi-instance Refiner deployments."""

    def __init__(self, pool: Any):
        self.pool = pool

    @staticmethod
    def _row_to_config(row: Optional[Dict[str, Any]]) -> Optional[MCPServerConfig]:
        if not row:
            return None
        return MCPServerConfig(
            name=str(row.get("name") or ""),
            base_url=str(row.get("base_url") or ""),
            auth_type=str(row.get("auth_type") or "bearer"),
            headers=row.get("headers") if isinstance(row.get("headers"), dict) else None,
            timeout=int(row.get("timeout") or 20),
            auth_secret_ref=str(row.get("auth_secret_ref") or "").strip() or None,
            headers_secret_ref=str(row.get("headers_secret_ref") or "").strip() or None,
            metadata=_deep_copy_dict(row.get("metadata") if isinstance(row.get("metadata"), dict) else {}),
            runtime=_deep_copy_dict(row.get("runtime") if isinstance(row.get("runtime"), dict) else {}),
            created_at=_timestamp(row.get("created_at")),
            updated_at=_timestamp(row.get("updated_at")),
        )

    def list_servers(self, owner: str) -> List[MCPServerConfig]:
        cleaned_owner = str(owner or "").strip()
        if not cleaned_owner:
            return []
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT name, base_url, auth_type, auth_secret_ref, headers_secret_ref, headers,
                       timeout, metadata, runtime, created_at, updated_at
                FROM nm_mcp_servers
                WHERE owner = %s
                ORDER BY updated_at DESC, name ASC
                """,
                (cleaned_owner,),
            ).fetchall()
        results: List[MCPServerConfig] = []
        for row in rows or []:
            config = self._row_to_config(row)
            if config and config.name:
                results.append(config)
        return results

    def save_server(self, owner: str, config: MCPServerConfig) -> None:
        cleaned_owner = str(owner or "").strip()
        if not cleaned_owner:
            raise ValueError("owner is required")
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO nm_users (username, role, updated_at)
                    VALUES (%s, 'user', NOW())
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (cleaned_owner,),
                )
                conn.execute(
                    """
                    INSERT INTO nm_mcp_servers (
                        owner,
                        name,
                        base_url,
                        auth_type,
                        auth_secret_ref,
                        headers_secret_ref,
                        headers,
                        timeout,
                        metadata,
                        runtime,
                        created_at,
                        updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (owner, name) DO UPDATE
                    SET base_url = EXCLUDED.base_url,
                        auth_type = EXCLUDED.auth_type,
                        auth_secret_ref = EXCLUDED.auth_secret_ref,
                        headers_secret_ref = EXCLUDED.headers_secret_ref,
                        headers = EXCLUDED.headers,
                        timeout = EXCLUDED.timeout,
                        metadata = EXCLUDED.metadata,
                        runtime = EXCLUDED.runtime,
                        updated_at = NOW()
                    """,
                    (
                        cleaned_owner,
                        config.name,
                        config.base_url,
                        config.auth_type or "bearer",
                        config.auth_secret_ref,
                        config.headers_secret_ref,
                        _jsonb(config.headers if isinstance(config.headers, dict) else {}),
                        int(config.timeout or 20),
                        _jsonb(config.metadata if isinstance(config.metadata, dict) else {}),
                        _jsonb(config.runtime if isinstance(config.runtime, dict) else {}),
                    ),
                )

    def delete_server(self, owner: str, name: str) -> bool:
        cleaned_owner = str(owner or "").strip()
        cleaned_name = str(name or "").strip()
        if not cleaned_owner or not cleaned_name:
            return False
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    DELETE FROM nm_mcp_servers
                    WHERE owner = %s AND name = %s
                    RETURNING name
                    """,
                    (cleaned_owner, cleaned_name),
                ).fetchone()
        return bool(row)

    def get_server(self, owner: str, name: str) -> Optional[MCPServerConfig]:
        cleaned_owner = str(owner or "").strip()
        cleaned_name = str(name or "").strip()
        if not cleaned_owner or not cleaned_name:
            return None
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT name, base_url, auth_type, auth_secret_ref, headers_secret_ref, headers,
                       timeout, metadata, runtime, created_at, updated_at
                FROM nm_mcp_servers
                WHERE owner = %s AND name = %s
                """,
                (cleaned_owner, cleaned_name),
            ).fetchone()
        return self._row_to_config(row)

    def update_runtime(self, owner: str, name: str, runtime_patch: Dict[str, Any]) -> bool:
        cleaned_owner = str(owner or "").strip()
        cleaned_name = str(name or "").strip()
        if not cleaned_owner or not cleaned_name:
            return False
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT runtime
                    FROM nm_mcp_servers
                    WHERE owner = %s AND name = %s
                    FOR UPDATE
                    """,
                    (cleaned_owner, cleaned_name),
                ).fetchone()
                if not row:
                    return False
                current_runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
                merged_runtime = _merge_runtime(current_runtime, runtime_patch)
                conn.execute(
                    """
                    UPDATE nm_mcp_servers
                    SET runtime = %s,
                        updated_at = NOW()
                    WHERE owner = %s AND name = %s
                    """,
                    (_jsonb(merged_runtime), cleaned_owner, cleaned_name),
                )
        return True
