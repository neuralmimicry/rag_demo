import json
import csv
import io
import logging
import os
import queue
import re
import shlex
import signal
import smtplib
import secrets as secrets_lib
import subprocess
import sys
import threading
import time
import uuid
import datetime as dt
from collections import deque
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
import shutil
import statistics
import tempfile
from flask import Flask, Response, jsonify, render_template, request, redirect, session, url_for, send_from_directory, g
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from llm_providers import get_provider, LLMError
from file_converter import FileConverter
from rag_engine import RagDocument, RagIndex, RagStore
from mcp_client import MCPClient, MCPServerConfig, MCPServerStore
from capabilities import get_capabilities, capability_summary, select_skills, format_skill_brief

logger = logging.getLogger(__name__)

try:
    import redis  # type: ignore
except Exception:
    redis = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "web", "public")
JOB_ROOT = os.getenv("REFINER_JOB_DIR", os.path.join(BASE_DIR, "job_data"))
PROJECTS_ROOT = os.path.join(JOB_ROOT, "projects")
SECRET_STORE_ROOT = os.path.join(JOB_ROOT, "secrets")
USERS_PATH = os.path.join(JOB_ROOT, "users.json")
WORKSPACE_ROOT = os.path.join(JOB_ROOT, "workspaces")
DEFAULT_FORK_ORG = os.getenv("REFINER_FORK_ORG", "neuralmimicry")
DEFAULT_WORKERS = int(os.getenv("REFINER_WORKERS", "2"))
DEFAULT_TAIL = int(os.getenv("REFINER_LOG_TAIL", "200"))
DEFAULT_REPO_PRIVATE = str(os.getenv("REFINER_REPO_PRIVATE", "1")).lower() in {"1", "true", "yes"}
REQUIREMENTS_MAX_BYTES = int(os.getenv("REFINER_REQUIREMENTS_SCAN_BYTES", "20000"))
REQUIREMENTS_IMPORT_MAX_BYTES = int(os.getenv("REFINER_REQUIREMENTS_IMPORT_MAX_BYTES", "4000000"))
REFUND_SCREENSHOT_MAX_BYTES = int(os.getenv("REFINER_REFUND_SCREENSHOT_MAX_BYTES", "5000000"))
REFUND_MAX_FILES = int(os.getenv("REFINER_REFUND_MAX_FILES", "6"))
SITE_BASE = (os.getenv("NEURALMIMICRY_SITE_BASE") or "https://neuralmimicry.ai").rstrip("/")
JOB_META_FILENAME = "job.json"
JOB_META_VERSION = 1
ACTIVE_WINDOW_SEC = int(os.getenv("REFINER_ACTIVE_WINDOW_SEC", "120"))
LEDGER_ROOT = os.path.join(JOB_ROOT, "ledger")
ESTIMATE_REPO_TTL_SEC = int(os.getenv("REFINER_ESTIMATE_REPO_TTL", "30"))
ESTIMATE_REPO_MAX_FILES = int(os.getenv("REFINER_ESTIMATE_REPO_MAX_FILES", "900"))
ESTIMATE_REPO_MAX_SEC = float(os.getenv("REFINER_ESTIMATE_REPO_MAX_SEC", "0.35"))
ESTIMATE_REPO_MAX_FILE_BYTES = int(os.getenv("REFINER_ESTIMATE_REPO_MAX_FILE_BYTES", "300000"))
ESTIMATE_REPO_SAMPLE_MULTIPLIER = float(os.getenv("REFINER_ESTIMATE_REPO_SAMPLE_MULTIPLIER", "1.6"))
ESTIMATE_CALIBRATION_TTL_SEC = int(os.getenv("REFINER_ESTIMATE_CALIBRATION_TTL", "90"))
DEFAULT_LLM_MAX_TOKENS = int(os.getenv("REFINER_DEFAULT_LLM_MAX_TOKENS", "6000"))
RESUME_LLM_MAX_TOKENS_CAP = int(os.getenv("REFINER_RESUME_LLM_MAX_TOKENS_CAP", "12000"))
UK_TZ = ZoneInfo("Europe/London")
UK_DATETIME_FORMAT = "%d/%m/%Y %H:%M:%S"
RAG_STORE_ROOT = os.path.join(JOB_ROOT, "rag")
MCP_STORE_ROOT = os.path.join(JOB_ROOT, "mcp")
RAG_MAX_DOCS = int(os.getenv("REFINER_RAG_MAX_DOCS", "60"))
RAG_MAX_DOC_BYTES = int(os.getenv("REFINER_RAG_MAX_DOC_BYTES", "600000"))
RAG_DEFAULT_CHUNK_SIZE = int(os.getenv("REFINER_RAG_CHUNK_SIZE", "1200"))
RAG_DEFAULT_CHUNK_OVERLAP = int(os.getenv("REFINER_RAG_CHUNK_OVERLAP", "200"))
RAG_DEFAULT_MAX_CHUNKS = int(os.getenv("REFINER_RAG_MAX_CHUNKS", "2000"))
RAG_ALLOWED_ROOTS = [
    p for p in (os.getenv("REFINER_RAG_ALLOWED_ROOTS") or "").split(",") if p.strip()
]
if BASE_DIR not in RAG_ALLOWED_ROOTS:
    RAG_ALLOWED_ROOTS.append(BASE_DIR)
if JOB_ROOT not in RAG_ALLOWED_ROOTS:
    RAG_ALLOWED_ROOTS.append(JOB_ROOT)
try:
    TOKEN_BTC_RATE = float(os.getenv("REFINER_TOKEN_BTC_RATE", "0.000016"))
except Exception:
    TOKEN_BTC_RATE = 0.000016
PORTAL_WEBHOOK_URL = (os.getenv("REFINER_PORTAL_WEBHOOK") or "").strip()
PORTAL_WEBHOOK_TOKEN = (os.getenv("REFINER_PORTAL_WEBHOOK_TOKEN") or "").strip()
PORTAL_WEBHOOK_TIMEOUT = int(os.getenv("REFINER_PORTAL_WEBHOOK_TIMEOUT", "12"))

ESTIMATE_TEXT_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".sql",
    ".md",
    ".txt",
    ".rst",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
}

ESTIMATE_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".research_cache",
    "__pypackages__",
    "site-packages",
    "dist-packages",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "test_output",
    "project_solver_output",
    "delivery_pipeline_output",
}

REFUND_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

_estimate_repo_cache: Dict[str, Dict[str, Any]] = {}
_estimate_repo_cache_lock = threading.Lock()
_estimate_calibration_cache: Dict[str, Any] = {"ts": 0.0, "data": {}, "job_count": 0}

app = Flask(__name__, static_folder="web/static", template_folder="web/templates")
app.secret_key = os.getenv("REFINER_SECRET_KEY") or os.urandom(32)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> List[str]:
    value = os.getenv(name, "")
    items = []
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        items.append(entry.rstrip("/"))
    return items


def _normalize_samesite(value: Optional[str]) -> str:
    if not value:
        return ""
    cleaned = value.strip().lower()
    if cleaned == "none":
        return "None"
    if cleaned == "lax":
        return "Lax"
    if cleaned == "strict":
        return "Strict"
    return ""


APP_START_TIME = time.time()

METRICS_PATH = (os.getenv("REFINER_METRICS_PATH", "/metrics") or "/metrics").strip()
if not METRICS_PATH.startswith("/"):
    METRICS_PATH = f"/{METRICS_PATH}"

try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

    PROMETHEUS_AVAILABLE = True
except Exception:
    PROMETHEUS_AVAILABLE = False

METRICS_ENABLED = _env_flag("REFINER_METRICS_ENABLED", True) and PROMETHEUS_AVAILABLE

if METRICS_ENABLED:
    REQUEST_COUNT = Counter(
        "refiner_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
    )
    REQUEST_LATENCY = Histogram(
        "refiner_http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "path"],
    )
    INFLIGHT = Gauge("refiner_http_inflight_requests", "In-flight HTTP requests")
    JOBS_BY_STATUS = Gauge("refiner_jobs_total", "Jobs by status", ["status"])
    JOB_QUEUE_DEPTH = Gauge("refiner_job_queue_depth", "Job queue depth")
    WORKER_COUNT = Gauge("refiner_worker_threads", "Worker threads")
    UPTIME = Gauge("refiner_uptime_seconds", "Application uptime in seconds")


CORS_ORIGINS = _env_list("REFINER_CORS_ORIGINS")
CORS_ALLOW_HEADERS = ["Content-Type", "Authorization", "X-Requested-With"]
CORS_ALLOW_METHODS = ["GET", "POST", "DELETE", "OPTIONS"]
CORS_MAX_AGE = int(os.getenv("REFINER_CORS_MAX_AGE", "600"))
API_BASE = os.getenv("REFINER_API_BASE", "").strip().rstrip("/")
COOKIE_DOMAIN = (os.getenv("REFINER_COOKIE_DOMAIN") or "").strip() or None

COOKIE_SAMESITE = _normalize_samesite(os.getenv("REFINER_COOKIE_SAMESITE"))
if not COOKIE_SAMESITE:
    COOKIE_SAMESITE = "None" if CORS_ORIGINS else "Lax"
SECURE_COOKIES = _env_flag("REFINER_SECURE_COOKIES", COOKIE_SAMESITE == "None")
ENFORCE_HTTPS = _env_flag("REFINER_ENFORCE_HTTPS", False)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE=SECURE_COOKIES,
    SESSION_COOKIE_DOMAIN=COOKIE_DOMAIN,
)

SSO_TTL_SECONDS = int(os.getenv("REFINER_SSO_TTL", "300"))
SSO_STORE_MODE = (os.getenv("REFINER_SSO_STORE") or "auto").strip().lower()
SSO_REDIS_URL = (os.getenv("REFINER_SSO_REDIS_URL") or os.getenv("REDIS_URL") or "").strip() or None
SSO_REDIS_PREFIX = (os.getenv("REFINER_SSO_REDIS_PREFIX") or "refiner:sso:").strip() or "refiner:sso:"

class SsoStore:
    type_name = "base"

    def issue(self, user: str) -> str:
        raise NotImplementedError

    def consume(self, token: str) -> Optional[str]:
        raise NotImplementedError

    def health(self) -> Dict[str, Any]:
        return {"type": self.type_name, "ok": True}


class MemorySsoStore(SsoStore):
    type_name = "memory"

    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = max(30, int(ttl_seconds))
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: Optional[float] = None) -> None:
        timestamp = now if now is not None else time.time()
        with self._lock:
            expired = [
                token for token, meta in self._tokens.items() if meta.get("expires_at", 0) <= timestamp
            ]
            for token in expired:
                self._tokens.pop(token, None)

    def issue(self, user: str) -> str:
        token = secrets_lib.token_urlsafe(32)
        timestamp = time.time()
        expires_at = timestamp + self.ttl_seconds
        self._prune(timestamp)
        with self._lock:
            self._tokens[token] = {"user": user, "issued_at": timestamp, "expires_at": expires_at}
        return token

    def consume(self, token: str) -> Optional[str]:
        if not token:
            return None
        self._prune()
        with self._lock:
            meta = self._tokens.pop(token, None)
        if not meta:
            return None
        if meta.get("expires_at", 0) <= time.time():
            return None
        return meta.get("user")

    def health(self) -> Dict[str, Any]:
        return {"type": self.type_name, "ok": True}


class RedisSsoStore(SsoStore):
    type_name = "redis"

    def __init__(self, client: Any, prefix: str, ttl_seconds: int):
        self._client = client
        self._prefix = prefix
        self._ttl_seconds = max(30, int(ttl_seconds))

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    def issue(self, user: str) -> str:
        token = secrets_lib.token_urlsafe(32)
        self._client.setex(self._key(token), self._ttl_seconds, user)
        return token

    def consume(self, token: str) -> Optional[str]:
        if not token:
            return None
        key = self._key(token)
        if hasattr(self._client, "getdel"):
            user = self._client.getdel(key)
        else:
            script = "local v = redis.call('GET', KEYS[1]); if v then redis.call('DEL', KEYS[1]); end; return v;"
            user = self._client.eval(script, 1, key)
        if user:
            return str(user)
        return None

    def health(self) -> Dict[str, Any]:
        try:
            self._client.ping()
            return {"type": self.type_name, "ok": True}
        except Exception as exc:
            return {"type": self.type_name, "ok": False, "error": str(exc)}


def _init_sso_store() -> Dict[str, Any]:
    status: Dict[str, Any] = {"mode": SSO_STORE_MODE}
    mode = SSO_STORE_MODE
    if mode not in {"auto", "redis", "memory"}:
        status.update({"ok": False, "error": "invalid_mode"})
        mode = "auto"
    if mode == "memory":
        status.update({"ok": True, "active_store": "memory"})
        return {"store": MemorySsoStore(SSO_TTL_SECONDS), "status": status}
    if mode in {"auto", "redis"}:
        if not SSO_REDIS_URL:
            status.update(
                {
                    "ok": mode != "redis",
                    "active_store": "memory",
                    "error": "redis_url_missing" if mode == "redis" else None,
                }
            )
            return {"store": MemorySsoStore(SSO_TTL_SECONDS), "status": status}
        if redis is None:
            status.update(
                {
                    "ok": False,
                    "active_store": "memory",
                    "error": "redis_library_missing",
                }
            )
            return {"store": MemorySsoStore(SSO_TTL_SECONDS), "status": status}
        try:
            client = redis.Redis.from_url(SSO_REDIS_URL, decode_responses=True)
            client.ping()
            status.update({"ok": True, "active_store": "redis"})
            return {"store": RedisSsoStore(client, SSO_REDIS_PREFIX, SSO_TTL_SECONDS), "status": status}
        except Exception as exc:
            status.update(
                {
                    "ok": mode != "redis",
                    "active_store": "memory",
                    "error": "redis_unavailable",
                    "detail": str(exc),
                }
            )
            return {"store": MemorySsoStore(SSO_TTL_SECONDS), "status": status}
    status.update({"ok": True, "active_store": "memory"})
    return {"store": MemorySsoStore(SSO_TTL_SECONDS), "status": status}


_sso_init = _init_sso_store()
SSO_STORE: SsoStore = _sso_init["store"]
SSO_STORE_STATUS: Dict[str, Any] = _sso_init["status"]
if not SSO_STORE_STATUS.get("ok", True):
    logger.warning("SSO store initialized with warning: %s", SSO_STORE_STATUS)


def _sso_store_health() -> Dict[str, Any]:
    status = dict(SSO_STORE_STATUS)
    store_health = SSO_STORE.health()
    status["active_store"] = status.get("active_store") or store_health.get("type")
    status["store"] = store_health
    status["ok"] = bool(status.get("ok", True)) and bool(store_health.get("ok", True))
    return status


def _issue_sso_token(user: str) -> str:
    return SSO_STORE.issue(user)


def _consume_sso_token(token: str) -> Optional[str]:
    return SSO_STORE.consume(token)


def _safe_next_path(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        return "/"
    if raw.startswith("//"):
        return "/"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not raw.startswith("/"):
        return f"/{raw}"
    return raw


def _normalise_allowed_roots() -> List[str]:
    roots = []
    for root in RAG_ALLOWED_ROOTS:
        if not root:
            continue
        try:
            roots.append(os.path.abspath(root))
        except Exception:
            continue
    return sorted(set(roots))


def _is_path_allowed(path: str, allowed_roots: Optional[List[str]] = None) -> bool:
    if not path:
        return False
    roots = allowed_roots or _normalise_allowed_roots()
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False
    for root in roots:
        if abs_path == root or abs_path.startswith(root + os.sep):
            return True
    return False


def _safe_source_label(path: str) -> str:
    return os.path.basename(path) or path


def _coerce_rag_sources(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources = []
    raw_sources = payload.get("sources")
    if isinstance(raw_sources, list):
        sources.extend([item for item in raw_sources if isinstance(item, dict)])
    raw_paths = payload.get("paths")
    if isinstance(raw_paths, list):
        for path in raw_paths:
            if isinstance(path, str):
                sources.append({"path": path})
    return sources


def _build_rag_documents(
    sources: List[Dict[str, Any]],
    *,
    max_docs: int,
    max_doc_bytes: int,
    allowed_roots: Optional[List[str]] = None,
) -> List[RagDocument]:
    docs: List[RagDocument] = []
    converter = FileConverter(llm=None, llm_params=None)
    for idx, entry in enumerate(sources[:max_docs], start=1):
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        path = entry.get("path")
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        source_label = entry.get("source") or entry.get("title")
        if path and isinstance(path, str):
            path = path.strip()
            if not path:
                continue
            if not _is_path_allowed(path, allowed_roots):
                continue
            if not os.path.exists(path):
                continue
            try:
                if os.path.getsize(path) > max_doc_bytes:
                    continue
            except Exception:
                continue
            source_label = source_label or _safe_source_label(path)
            text = converter.convert(path)
            if isinstance(text, str) and text.startswith("Error:"):
                continue
        if not text or not isinstance(text, str):
            continue
        doc_id = entry.get("id") or f"doc-{idx:03d}"
        docs.append(
            RagDocument(
                doc_id=str(doc_id),
                source=str(source_label or doc_id),
                text=text,
                metadata=metadata,
            )
        )
    return docs

if _env_flag("REFINER_TRUST_PROXY", False):
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


def _ensure_dirs() -> None:
    os.makedirs(JOB_ROOT, exist_ok=True)
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
    os.makedirs(SECRET_STORE_ROOT, exist_ok=True)
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    os.makedirs(LEDGER_ROOT, exist_ok=True)
    os.makedirs(RAG_STORE_ROOT, exist_ok=True)
    os.makedirs(MCP_STORE_ROOT, exist_ok=True)


def _now_iso() -> str:
    return dt.datetime.now(UK_TZ).strftime(UK_DATETIME_FORMAT)


def _parse_timestamp(value: Optional[str]) -> Optional[dt.datetime]:
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    for fmt in (UK_DATETIME_FORMAT, "%d/%m/%Y %H:%M"):
        try:
            parsed = dt.datetime.strptime(cleaned, fmt)
            return parsed.replace(tzinfo=UK_TZ)
        except ValueError:
            continue
    try:
        if cleaned.endswith("Z"):
            parsed = dt.datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%SZ")
            return parsed.replace(tzinfo=dt.timezone.utc)
        parsed = dt.datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def _timestamp_sort_key(value: Optional[str]) -> float:
    parsed = _parse_timestamp(value)
    if not parsed:
        return 0.0
    return parsed.timestamp()


def _normalise_timestamp(value: Optional[str]) -> Optional[str]:
    parsed = _parse_timestamp(value)
    if not parsed:
        return value
    return parsed.astimezone(UK_TZ).strftime(UK_DATETIME_FORMAT)


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip())
    cleaned = cleaned.strip("-")
    return cleaned or "project"


SECRET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\\-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$")

GUARDRAIL_PATTERNS = {
    "violence": [
        r"\\b(make|build|assemble|design)\\b.*\\b(bomb|explosive|grenade)\\b",
        r"\\b(weapon|firearm|gun)\\b",
        r"\\bassassinate\\b",
    ],
    "malware": [
        r"\\b(ransomware|keylogger|malware|virus|trojan|rootkit)\\b",
        r"\\b(phishing|credential\\s+harvest)\\b",
    ],
    "illegal": [
        r"\\b(counterfeit|forgery|fake\\s+id)\\b",
        r"\\bcredit\\s+card\\s+fraud\\b",
        r"\\bdrug\\s+trafficking\\b",
    ],
    "self_harm": [
        r"\\b(suicide|self-harm|self harm)\\b",
    ],
}


class SecretStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.data: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self.data = data
        except Exception:
            self.data = {}

    def _write(self) -> None:
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def list_masked(self) -> List[Dict[str, str]]:
        with self.lock:
            items = []
            for name, entry in sorted(self.data.items()):
                value = entry.get("value") if isinstance(entry, dict) else ""
                masked = self._mask(value)
                updated_at = entry.get("updated_at") if isinstance(entry, dict) else None
                items.append({"name": name, "masked": masked, "updated_at": updated_at})
            return items

    def set(self, name: str, value: str) -> None:
        with self.lock:
            self.data[name] = {"value": value, "updated_at": _now_iso()}
            self._write()

    def delete(self, name: str) -> bool:
        with self.lock:
            if name not in self.data:
                return False
            self.data.pop(name, None)
            self._write()
            return True

    def get_env(self) -> Dict[str, str]:
        with self.lock:
            env = {}
            for name, entry in self.data.items():
                if isinstance(entry, dict) and entry.get("value"):
                    env[name] = entry["value"]
            return env

    @staticmethod
    def _mask(value: Optional[str]) -> str:
        if not value:
            return "not set"
        tail = value[-4:] if len(value) >= 4 else value
        return f"***{tail}"


class UserStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.users: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self.users = data
        except Exception:
            self.users = {}

    def _write(self) -> None:
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.users, handle, indent=2)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def has_users(self) -> bool:
        with self.lock:
            return bool(self.users)

    def ensure_admin_from_env(self) -> None:
        admin_user = (os.getenv("REFINER_ADMIN_USER") or "").strip()
        admin_pass = (os.getenv("REFINER_ADMIN_PASS") or "").strip()
        admin_email = (os.getenv("REFINER_ADMIN_EMAIL") or "").strip()
        if not admin_user or not admin_pass:
            return
        with self.lock:
            if self.users:
                return
            self.users[admin_user] = {
                "password": generate_password_hash(admin_pass),
                "created_at": _now_iso(),
                "role": "admin",
            }
            if admin_email:
                self.users[admin_user]["email"] = admin_email
            self._write()

    def create_user(self, username: str, password: str, role: str = "user", email: Optional[str] = None) -> None:
        with self.lock:
            self.users[username] = {
                "password": generate_password_hash(password),
                "created_at": _now_iso(),
                "role": role,
            }
            if email:
                self.users[username]["email"] = email
            self._write()

    def set_email(self, username: str, email: Optional[str]) -> bool:
        with self.lock:
            entry = self.users.get(username)
            if not entry:
                return False
            if email:
                entry["email"] = email
            else:
                entry.pop("email", None)
            entry["updated_at"] = _now_iso()
            self._write()
            return True

    def get_email(self, username: str) -> Optional[str]:
        with self.lock:
            entry = self.users.get(username) or {}
            return entry.get("email")

    def verify(self, username: str, password: str) -> bool:
        with self.lock:
            entry = self.users.get(username)
            if not entry:
                return False
            return check_password_hash(entry.get("password") or "", password)

    def get_role(self, username: str) -> Optional[str]:
        with self.lock:
            entry = self.users.get(username) or {}
            return entry.get("role")


class TokenLedger:
    def __init__(self, root: str):
        self.root = root
        self.lock = threading.RLock()
        os.makedirs(root, exist_ok=True)

    def _safe_user(self, user: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\\-]+", "_", user or "unknown")

    def _ledger_path(self, user: str) -> str:
        return os.path.join(self.root, f"{self._safe_user(user)}.jsonl")

    def _summary_path(self, user: str) -> str:
        return os.path.join(self.root, f"{self._safe_user(user)}.summary.json")

    def _default_summary(self) -> Dict[str, Any]:
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

    def _load_summary(self, user: str) -> Dict[str, Any]:
        path = self._summary_path(user)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    summary = self._default_summary()
                    summary.update(data)
                    if "paid_balance" not in data:
                        summary["paid_balance"] = int(summary.get("balance") or 0)
                        summary["free_balance"] = int(summary.get("free_balance") or 0)
                    return summary
            except Exception:
                pass
        return self._rebuild_summary(user)

    def _write_summary(self, user: str, summary: Dict[str, Any]) -> None:
        _write_json_atomic(self._summary_path(user), summary)

    def _rebuild_summary(self, user: str) -> Dict[str, Any]:
        summary = self._default_summary()
        path = self._ledger_path(user)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        meta = entry.get("meta") or {}
                        delta = int(entry.get("delta") or 0)
                        paid_after = meta.get("paid_after")
                        free_after = meta.get("free_after")
                        if paid_after is not None or free_after is not None:
                            if paid_after is not None:
                                summary["paid_balance"] = int(paid_after or 0)
                            if free_after is not None:
                                summary["free_balance"] = int(free_after or 0)
                        else:
                            if entry.get("type") == "grant":
                                summary["free_balance"] += max(0, delta)
                            else:
                                summary["paid_balance"] = max(0, summary["paid_balance"] + delta)
                        summary["balance"] = max(0, summary["paid_balance"] + summary["free_balance"])
                        etype = entry.get("type")
                        if etype == "topup":
                            summary["last_topup_tokens"] = int(entry.get("meta", {}).get("tokens") or abs(delta) or 0)
                            summary["last_topup_at"] = entry.get("ts")
                        if etype == "sync":
                            capacity = entry.get("meta", {}).get("capacity")
                            if capacity is not None:
                                summary["last_topup_tokens"] = int(capacity or 0)
                                summary["last_topup_at"] = entry.get("ts")
                        if etype == "debit":
                            used = meta.get("used_total")
                            if used is None:
                                used = abs(delta)
                            summary["spent_total"] += int(used or 0)
                            shortfall = int(entry.get("meta", {}).get("shortfall") or 0)
                            summary["shortfall_total"] += shortfall
                        if etype == "cashout":
                            summary["cashout_total"] += abs(delta)
                        if etype == "grant":
                            summary["free_grant_total"] += abs(delta)
                        summary["updated_at"] = entry.get("ts")
            except Exception:
                pass
        self._write_summary(user, summary)
        return summary

    def get_summary(self, user: str) -> Dict[str, Any]:
        with self.lock:
            return self._load_summary(user)

    def record(
        self,
        user: str,
        entry_type: str,
        delta: int,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta = meta or {}
        with self.lock:
            summary = self._load_summary(user)
            paid_balance = int(summary.get("paid_balance") or summary.get("balance") or 0)
            free_balance = int(summary.get("free_balance") or 0)
            balance = paid_balance + free_balance
            requested_delta = int(delta or 0)
            new_paid = paid_balance
            new_free = free_balance
            shortfall = 0
            if entry_type == "topup":
                if requested_delta > 0:
                    new_paid += requested_delta
                else:
                    requested_delta = 0
            elif entry_type == "refund":
                if requested_delta > 0:
                    new_paid += requested_delta
                else:
                    requested_delta = 0
            elif entry_type == "grant":
                if requested_delta > 0:
                    new_free += requested_delta
                else:
                    requested_delta = 0
            elif entry_type == "cashout":
                if requested_delta >= 0:
                    requested_delta = -abs(requested_delta)
                desired = abs(requested_delta)
                paid_used = min(new_paid, desired)
                new_paid -= paid_used
                shortfall = desired - paid_used
                if shortfall:
                    meta["shortfall"] = shortfall
                requested_delta = -(paid_used)
                meta["paid_used"] = paid_used
                meta["free_used"] = 0
                meta["used_total"] = paid_used
            elif entry_type == "debit":
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
                    meta["shortfall"] = shortfall
                meta["free_used"] = free_used
                meta["paid_used"] = paid_used
                meta["used_total"] = free_used + paid_used
                requested_delta = -(free_used + paid_used)
            elif entry_type in {"reserve", "release"}:
                requested_delta = 0
            elif entry_type == "sync":
                target_paid = meta.get("target_paid_balance")
                target_free = meta.get("target_free_balance")
                target_balance = meta.get("target_balance")
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
            if requested_delta == 0 and entry_type not in {"reserve", "release", "sync"}:
                entry_type = "adjust"
            new_balance = max(0, new_paid + new_free)
            meta["paid_after"] = new_paid
            meta["free_after"] = new_free
            entry = {
                "ts": _now_iso(),
                "type": entry_type,
                "user": user,
                "delta": requested_delta,
                "balance_after": new_balance,
                "meta": meta,
            }
            path = self._ledger_path(user)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")

            summary["balance"] = new_balance
            summary["paid_balance"] = new_paid
            summary["free_balance"] = new_free
            if entry_type == "topup":
                summary["last_topup_tokens"] = int(meta.get("tokens") or abs(requested_delta) or 0)
                summary["last_topup_at"] = entry["ts"]
            if entry_type == "sync" and meta.get("capacity") is not None:
                summary["last_topup_tokens"] = int(meta.get("capacity") or 0)
                summary["last_topup_at"] = meta.get("capacity_ts") or entry["ts"]
            if entry_type == "debit":
                used_total = int(meta.get("used_total") or abs(requested_delta) or 0)
                summary["spent_total"] = int(summary.get("spent_total") or 0) + used_total
                summary["shortfall_total"] = int(summary.get("shortfall_total") or 0) + int(meta.get("shortfall") or 0)
            if entry_type == "cashout":
                summary["cashout_total"] = int(summary.get("cashout_total") or 0) + abs(requested_delta)
            if entry_type == "grant":
                summary["free_grant_total"] = int(summary.get("free_grant_total") or 0) + abs(requested_delta)
            summary["updated_at"] = entry["ts"]
            self._write_summary(user, summary)
            entry["shortfall"] = shortfall
            return entry

    def list_entries(self, user: str, limit: int = 50) -> List[Dict[str, Any]]:
        path = self._ledger_path(user)
        if not os.path.exists(path):
            return []
        try:
            lines = deque(maxlen=limit)
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        lines.append(line)
            entries = []
            for line in reversed(lines):
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
            return entries
        except Exception:
            return []


TOKEN_RE = re.compile(
    r"Token usage \[(?P<category>[^\]]+)\] (?P<provider>[^/\s]+)/(?P<model>[^:]+): "
    r"sent=(?P<prompt>\?|\d+) received=(?P<completion>\?|\d+) used=(?P<total>\?|\d+)"
    r"(?: cached=(?P<cached>\?|\d+))? \| running total \[(?P<cat2>[^\]]+)\]: "
    r"sent=(?P<run_prompt>\d+) received=(?P<run_completion>\d+) used=(?P<run_total>\d+)"
)


@dataclass
class Stage:
    name: str
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    message: Optional[str] = None


@dataclass
class Job:
    job_id: str
    payload: Dict[str, Any]
    project_name: str = ""
    owner: str = ""
    status: str = "queued"
    workflow: str = "project_solver"
    progress: int = 0
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    restart_count: int = 0
    log_path: str = ""
    events_path: str = ""
    output_paths: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    stages: List[Stage] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None
    pid: Optional[int] = None
    stop_requested: bool = False
    notify_email: Optional[str] = None
    notified_at: Optional[str] = None
    notified_via: Optional[str] = None
    notification_error: Optional[str] = None
    token_estimate: Optional[int] = None
    token_reserved: int = 0
    token_actual: int = 0
    token_debited: int = 0
    token_shortfall: int = 0
    token_status: str = "none"
    repo_info: Dict[str, Any] = field(default_factory=dict)
    refunds: List[Dict[str, Any]] = field(default_factory=list)
    archived: bool = False
    archived_at: Optional[str] = None
    meta_path: str = ""
    log_buffer: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=2000))
    log_listeners: List[queue.Queue] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)
    _last_persist_ts: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        payload_workflow = self.payload.get("workflow")
        if payload_workflow:
            self.workflow = payload_workflow
        if not self.project_name:
            self.project_name = self._derive_project_name(self.payload)
        if not self.owner:
            self.owner = self.payload.get("owner") or self.owner
        if not self.notify_email:
            candidate = self.payload.get("notify_email") or self.payload.get("notification_email") or ""
            if isinstance(candidate, str):
                candidate = candidate.strip()
            else:
                candidate = ""
            self.notify_email = candidate or None
        if not self.metrics:
            self.metrics = {
                "token_usage": {"prompt": 0, "completion": 0, "total": 0, "cached": None},
                "errors": 0,
                "resolved": 0,
                "warnings": 0,
                "queue_wait_sec": None,
                "runtime_sec": None,
            }
        else:
            self.metrics.setdefault("token_usage", {"prompt": 0, "completion": 0, "total": 0, "cached": None})
            self.metrics.setdefault("errors", 0)
            self.metrics.setdefault("resolved", 0)
            self.metrics.setdefault("warnings", 0)
            self.metrics.setdefault("queue_wait_sec", None)
            self.metrics.setdefault("runtime_sec", None)
        if not self.meta_path:
            self.meta_path = os.path.join(JOB_ROOT, self.job_id, JOB_META_FILENAME)
        if self.token_estimate is None:
            self.token_estimate = None
        self.token_reserved = int(self.token_reserved or 0)
        self.token_actual = int(self.token_actual or 0)
        self.token_debited = int(self.token_debited or 0)
        self.token_shortfall = int(self.token_shortfall or 0)
        if not self.token_status:
            self.token_status = "none"
        if self.refunds is None:
            self.refunds = []
        if self.archived is None:
            self.archived = False
        if not self.archived:
            self.archived_at = None

    @staticmethod
    def _derive_project_name(payload: Dict[str, Any]) -> str:
        name = (payload.get("project_name") or "").strip()
        if name:
            return name
        root = (payload.get("project_root") or payload.get("delivery_project_root") or "").strip()
        if root:
            return os.path.basename(root.rstrip("/")) or root
        space = (payload.get("space") or "").strip()
        if space:
            return f"Space {space}"
        projects = (payload.get("projects") or "").strip()
        if projects:
            return f"Projects {projects}"
        topic = (payload.get("topic_source") or payload.get("topic_research") or "").strip()
        if topic:
            return topic[:60] + ("…" if len(topic) > 60 else "")
        return "Untitled"

    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status
            self.updated_at = _now_iso()
        self.persist(force=True)

    def set_progress(self, progress: int) -> None:
        with self.lock:
            self.progress = max(0, min(100, int(progress)))
            self.updated_at = _now_iso()
        self.persist()

    def add_listener(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.log_listeners.append(q)
        return q

    def remove_listener(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.log_listeners:
                self.log_listeners.remove(q)

    def append_log(self, line: str, stream: str = "stdout") -> None:
        entry = {"ts": _now_iso(), "line": line.rstrip("\n"), "stream": stream}
        with self.lock:
            self.log_buffer.append(entry)
            listeners = list(self.log_listeners)
        for listener in listeners:
            try:
                listener.put_nowait(entry)
            except queue.Full:
                continue

    def get_log_tail(self, count: int) -> List[Dict[str, Any]]:
        with self.lock:
            buffer = list(self.log_buffer)
        if buffer:
            if count <= 0:
                return buffer
            return buffer[-count:]
        if self.log_path and os.path.exists(self.log_path):
            return _read_log_tail(self.log_path, count)
        return buffer

    def update_stage(self, name: str, status: str, message: Optional[str] = None) -> None:
        now = _now_iso()
        with self.lock:
            for stage in self.stages:
                if stage.name == name:
                    stage.status = status
                    if status == "running" and not stage.started_at:
                        stage.started_at = now
                    if status in {"completed", "failed", "skipped", "blocked"}:
                        stage.finished_at = now
                    if message:
                        stage.message = message
                    self.updated_at = now
                    return
            stage = Stage(name=name, status=status, started_at=now if status == "running" else None)
            if status in {"completed", "failed", "skipped", "blocked"}:
                stage.finished_at = now
            if message:
                stage.message = message
            self.stages.append(stage)
            self.updated_at = now
        self.persist()

    def to_persisted_dict(self) -> Dict[str, Any]:
        with self.lock:
            data = {
                "version": JOB_META_VERSION,
                "id": self.job_id,
                "workflow": self.workflow,
                "project_name": self.project_name,
                "owner": self.owner,
                "status": self.status,
                "progress": self.progress,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "exit_code": self.exit_code,
                "restart_count": self.restart_count,
                "output_paths": self.output_paths,
                "metrics": dict(self.metrics),
                "stages": [stage.__dict__ for stage in self.stages],
                "repo_info": self.repo_info,
                "refunds": list(self.refunds),
                "archived": bool(self.archived),
                "archived_at": self.archived_at,
                "payload": _sanitize_payload(self.payload),
                "notification": {
                    "email": self.notify_email,
                    "sent_at": self.notified_at,
                    "method": self.notified_via,
                    "error": self.notification_error,
                },
                "tokens": {
                    "estimate": self.token_estimate,
                    "reserved": self.token_reserved,
                    "actual": self.token_actual,
                    "debited": self.token_debited,
                    "shortfall": self.token_shortfall,
                    "status": self.token_status,
                },
            }
        return data

    def persist(self, force: bool = False) -> None:
        if not self.meta_path:
            self.meta_path = os.path.join(JOB_ROOT, self.job_id, JOB_META_FILENAME)
        now = time.time()
        if not force and now - self._last_persist_ts < 1.0:
            return
        try:
            _write_json_atomic(self.meta_path, self.to_persisted_dict())
            self._last_persist_ts = now
        except Exception:
            pass

    @classmethod
    def from_persisted(cls, data: Dict[str, Any], meta_path: str) -> "Job":
        job_id = data.get("id") or data.get("job_id") or os.path.basename(os.path.dirname(meta_path))
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        job_dir = os.path.dirname(meta_path)
        log_path = os.path.join(job_dir, "job.log")
        events_path = os.path.join(job_dir, "events.jsonl")
        stages_data = data.get("stages") or []
        stages: List[Stage] = []
        for entry in stages_data:
            if not isinstance(entry, dict):
                continue
            stages.append(
                Stage(
                    name=str(entry.get("name") or "stage"),
                    status=str(entry.get("status") or "unknown"),
                    started_at=_normalise_timestamp(entry.get("started_at")),
                    finished_at=_normalise_timestamp(entry.get("finished_at")),
                    message=entry.get("message"),
                )
            )
        notification = data.get("notification") if isinstance(data.get("notification"), dict) else {}
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        output_paths = data.get("output_paths") if isinstance(data.get("output_paths"), dict) else {}
        repo_info = data.get("repo_info") if isinstance(data.get("repo_info"), dict) else {}
        refunds = data.get("refunds") if isinstance(data.get("refunds"), list) else []
        archived = data.get("archived")
        archived_at = _normalise_timestamp(data.get("archived_at") or None)
        if refunds:
            for refund in refunds:
                if not isinstance(refund, dict):
                    continue
                for key in ("requested_at", "screened_at", "decided_at", "settled_at"):
                    if key in refund:
                        refund[key] = _normalise_timestamp(refund.get(key))
                history = refund.get("history")
                if isinstance(history, list):
                    for entry in history:
                        if isinstance(entry, dict) and entry.get("at"):
                            entry["at"] = _normalise_timestamp(entry.get("at"))
                admin_decision = refund.get("admin_decision")
                if isinstance(admin_decision, dict) and admin_decision.get("decided_at"):
                    admin_decision["decided_at"] = _normalise_timestamp(admin_decision.get("decided_at"))
                screening = refund.get("llm_screening")
                if isinstance(screening, dict) and screening.get("screened_at"):
                    screening["screened_at"] = _normalise_timestamp(screening.get("screened_at"))
        job = cls(
            job_id=job_id,
            payload=payload,
            project_name=data.get("project_name") or "",
            owner=data.get("owner") or "",
            status=data.get("status") or "queued",
            workflow=data.get("workflow") or "project_solver",
            progress=int(data.get("progress") or 0),
            created_at=_normalise_timestamp(data.get("created_at")) or _now_iso(),
            updated_at=_normalise_timestamp(data.get("updated_at")) or _now_iso(),
            started_at=_normalise_timestamp(data.get("started_at")),
            finished_at=_normalise_timestamp(data.get("finished_at")),
            exit_code=data.get("exit_code"),
            restart_count=int(data.get("restart_count") or 0),
            log_path=log_path,
            events_path=events_path,
            output_paths=output_paths,
            metrics=metrics,
            stages=stages,
            repo_info=repo_info,
            notify_email=notification.get("email") or data.get("notify_email"),
            notified_at=_normalise_timestamp(notification.get("sent_at")),
            notified_via=notification.get("method"),
            notification_error=notification.get("error"),
            token_estimate=tokens.get("estimate"),
            token_reserved=int(tokens.get("reserved") or 0),
            token_actual=int(tokens.get("actual") or 0),
            token_debited=int(tokens.get("debited") or 0),
            token_shortfall=int(tokens.get("shortfall") or 0),
            token_status=tokens.get("status") or "none",
            refunds=refunds,
            archived=bool(archived) if archived is not None else False,
            archived_at=archived_at,
            meta_path=meta_path,
        )
        return job

    def to_dict(self, include_logs: bool = False, log_tail: int = DEFAULT_TAIL) -> Dict[str, Any]:
        with self.lock:
            data = {
                "id": self.job_id,
                "workflow": self.workflow,
                "project_name": self.project_name,
                "owner": self.owner,
                "status": self.status,
                "progress": self.progress,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "exit_code": self.exit_code,
                "restart_count": self.restart_count,
                "output_paths": self.output_paths,
                "metrics": dict(self.metrics),
                "stages": [stage.__dict__ for stage in self.stages],
                "pid": self.pid,
                "repo_info": self.repo_info,
                "refunds": list(self.refunds),
                "archived": bool(self.archived),
                "archived_at": self.archived_at,
                "tokens": {
                    "estimate": self.token_estimate,
                    "reserved": self.token_reserved,
                    "actual": self.token_actual,
                    "debited": self.token_debited,
                    "shortfall": self.token_shortfall,
                    "status": self.token_status,
                },
            }
        if include_logs:
            data["logs"] = self.get_log_tail(log_tail)
        return data


class JobManager:
    def __init__(self, workers: int = DEFAULT_WORKERS):
        self.jobs: Dict[str, Job] = {}
        self.queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.workers: List[threading.Thread] = []
        _ensure_dirs()
        self._load_jobs_from_disk()
        for idx in range(max(1, workers)):
            t = threading.Thread(target=self._worker_loop, args=(idx,), daemon=True)
            t.start()
            self.workers.append(t)

    def submit_job(self, payload: Dict[str, Any], owner: str) -> Job:
        job_id = uuid.uuid4().hex
        job_dir = os.path.join(JOB_ROOT, job_id)
        os.makedirs(job_dir, exist_ok=True)
        log_path = os.path.join(job_dir, "job.log")
        events_path = os.path.join(job_dir, "events.jsonl")
        job = Job(job_id=job_id, payload=payload, owner=owner, log_path=log_path, events_path=events_path)
        job.output_paths = self._resolve_output_paths(job)
        job.meta_path = os.path.join(job_dir, JOB_META_FILENAME)
        notify_email = payload.get("notify_email") or payload.get("notification_email") or ""
        if isinstance(notify_email, str):
            notify_email = notify_email.strip()
        else:
            notify_email = ""
        if notify_email and EMAIL_RE.match(notify_email):
            job.notify_email = notify_email
        job.persist(force=True)
        with self.lock:
            self.jobs[job_id] = job
        self.queue.put(job_id)
        return job

    def get_job(self, job_id: str, owner: Optional[str] = None) -> Optional[Job]:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            return None
        if owner and job.owner != owner:
            return None
        return job

    def list_jobs(self, status: Optional[str] = None, owner: Optional[str] = None) -> List[Job]:
        with self.lock:
            jobs = list(self.jobs.values())
        if owner:
            jobs = [job for job in jobs if job.owner == owner]
        if status:
            jobs = [job for job in jobs if job.status == status]
        return sorted(jobs, key=lambda j: _timestamp_sort_key(j.created_at), reverse=True)

    def reserved_tokens(self, owner: Optional[str] = None) -> int:
        with self.lock:
            jobs = list(self.jobs.values())
        total = 0
        for job in jobs:
            if owner and job.owner != owner:
                continue
            if job.token_reserved and job.status in {"queued", "running", "paused"}:
                total += int(job.token_reserved or 0)
        return total

    def in_use_tokens(self, owner: Optional[str] = None) -> int:
        with self.lock:
            jobs = list(self.jobs.values())
        total = 0
        for job in jobs:
            if owner and job.owner != owner:
                continue
            if job.status in {"running", "paused"}:
                usage = job.metrics.get("token_usage") if isinstance(job.metrics, dict) else {}
                run_total = usage.get("total") if isinstance(usage, dict) else None
                if run_total:
                    total += int(run_total or 0)
        return total

    def reconcile_tokens(self) -> None:
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            if job.status in {"completed", "failed", "stopped"} and job.token_status != "settled":
                self._settle_tokens(job)

    def _read_job_meta(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _load_jobs_from_disk(self) -> None:
        if not os.path.isdir(JOB_ROOT):
            return
        skip_dirs = {"projects", "secrets", "workspaces"}
        for entry in os.listdir(JOB_ROOT):
            if entry in skip_dirs:
                continue
            job_dir = os.path.join(JOB_ROOT, entry)
            if not os.path.isdir(job_dir):
                continue
            meta_path = os.path.join(job_dir, JOB_META_FILENAME)
            if not os.path.exists(meta_path):
                continue
            data = self._read_job_meta(meta_path)
            if not data:
                continue
            try:
                job = Job.from_persisted(data, meta_path)
            except Exception:
                continue
            self.jobs[job.job_id] = job

        for job in self.jobs.values():
            if job.status in {"queued", "running", "paused"}:
                job.status = "stopped"
                job.updated_at = _now_iso()
                if not job.finished_at:
                    job.finished_at = job.updated_at
                job.persist(force=True)

    def pause_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if not job.process or job.status != "running":
            job.set_status("paused")
            return True
        if os.name != "nt":
            try:
                os.kill(job.process.pid, signal.SIGSTOP)
                job.set_status("paused")
                job.append_log("Paused by user")
                return True
            except Exception:
                return False
        return False

    def resume_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if job.process and job.process.poll() is not None:
            job.process = None
            job.pid = None
        if not job.process:
            if not isinstance(job.payload, dict):
                job.payload = {}
            job.payload["include_global_requirements"] = True
            if job.status == "completed":
                self._apply_project_limits(job)
                summary = _completion_summary_from_output(job.output_paths.get("primary"))
                if isinstance(summary, dict):
                    steps_applied = _safe_int(summary.get("steps_applied"))
                    needs_more = bool(summary.get("needs_more_iterations"))
                    max_steps_reached = bool(summary.get("max_steps_reached"))
                    iterations_exhausted = summary.get("iterations_exhausted_sources") or []
                    if max_steps_reached:
                        current_steps = _safe_int(job.payload.get("project_max_steps"))
                        bump = max(25, int(current_steps * 0.5)) if current_steps else 50
                        job.payload["project_max_steps"] = max(current_steps + bump, 50)
                        job.append_log(f"Auto-bump: project_max_steps -> {job.payload['project_max_steps']}")
                    if isinstance(iterations_exhausted, list) and iterations_exhausted:
                        current_iters = _safe_int(job.payload.get("project_iterations"))
                        bump = max(1, int(current_iters * 0.5)) if current_iters else 2
                        job.payload["project_iterations"] = max(current_iters + bump, 2)
                        job.append_log(f"Auto-bump: project_iterations -> {job.payload['project_iterations']}")
                    if steps_applied <= 0 and needs_more and not max_steps_reached and not iterations_exhausted:
                        current_tokens = _safe_int(job.payload.get("llm_max_tokens"), DEFAULT_LLM_MAX_TOKENS)
                        cap = RESUME_LLM_MAX_TOKENS_CAP if RESUME_LLM_MAX_TOKENS_CAP > 0 else current_tokens
                        target = max(current_tokens * 2, DEFAULT_LLM_MAX_TOKENS)
                        if cap:
                            target = min(target, cap)
                        if target > current_tokens:
                            job.payload["llm_max_tokens"] = target
                            job.append_log(f"Auto-bump: llm_max_tokens -> {target}")
                with job.lock:
                    job.stages = [stage for stage in job.stages if stage.name != "finalize"]
                    execute_stage = None
                    for stage in job.stages:
                        if stage.name == "execute":
                            execute_stage = stage
                            break
                    if execute_stage:
                        execute_stage.status = "queued"
                        execute_stage.started_at = None
                        execute_stage.finished_at = None
                        execute_stage.message = None
                    else:
                        job.stages.append(Stage(name="execute", status="queued"))
                    job.updated_at = _now_iso()
                job.persist(force=True)
            job.set_status("queued")
            self.queue.put(job_id)
            return True
        if os.name != "nt":
            try:
                os.kill(job.process.pid, signal.SIGCONT)
                job.set_status("running")
                job.append_log("Resumed by user")
                return True
            except Exception:
                return False
        return False

    def stop_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        job.stop_requested = True
        if job.process and job.process.poll() is None:
            try:
                job.process.terminate()
                job.append_log("Stop requested")
                return True
            except Exception:
                return False
        job.set_status("stopped")
        job.finished_at = _now_iso()
        job.persist(force=True)
        self._settle_tokens(job)
        return True

    def _derive_project_limits(self, job: "Job") -> Optional[Dict[str, int]]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        workflow = payload.get("workflow") or job.workflow
        if workflow not in {"project_solver", "project"}:
            return None
        req_count = 0
        req_text = payload.get("requirements_text") if isinstance(payload.get("requirements_text"), str) else ""
        if req_text:
            req_count = _count_req_lines(req_text)
        if req_count <= 0:
            req_path = payload.get("requirements_path")
            if isinstance(req_path, str) and req_path:
                req_count = _count_req_lines(_read_file_limited(req_path, 250_000))
        if payload.get("include_global_requirements"):
            req_count += _global_requirements_count()
        iterations = payload.get("project_iterations")
        max_steps = payload.get("project_max_steps")
        try:
            iterations = int(iterations) if iterations else 0
        except Exception:
            iterations = 0
        try:
            max_steps = int(max_steps) if max_steps else 0
        except Exception:
            max_steps = 0
        if iterations <= 0:
            iterations = req_count if req_count > 0 else 3
        if max_steps <= 0:
            max_steps = max(25, req_count * 4) if req_count > 0 else 25
        return {"project_iterations": iterations, "project_max_steps": max_steps}

    def _apply_project_limits(self, job: "Job") -> None:
        limits = self._derive_project_limits(job)
        if not limits:
            return
        if not isinstance(job.payload, dict):
            job.payload = {}
        job.payload.update(limits)

    def _wait_for_process(self, job: Job, timeout: float = 4.0) -> None:
        if not job.process:
            return
        try:
            job.process.wait(timeout=timeout)
        except Exception:
            try:
                job.process.kill()
                job.process.wait(timeout=1.0)
            except Exception:
                pass

    def set_archived(self, job_id: str, archived: bool) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        job.archived = bool(archived)
        job.archived_at = _now_iso() if job.archived else None
        job.updated_at = _now_iso()
        job.persist(force=True)
        return True

    def delete_job(self, job_id: str, owner: Optional[str] = None, stop_if_active: bool = False) -> bool:
        job = self.get_job(job_id, owner=owner)
        if not job:
            return False
        if job.status in {"queued", "running", "paused"}:
            if not stop_if_active:
                return False
            self.stop_job(job_id)
            self._wait_for_process(job)
        with self.lock:
            self.jobs.pop(job_id, None)
        job_dir = os.path.join(JOB_ROOT, job_id)
        try:
            shutil.rmtree(job_dir)
        except FileNotFoundError:
            pass
        except Exception:
            return False
        return True

    def restart_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if job.status in {"running", "paused"}:
            return False
        if not isinstance(job.payload, dict):
            job.payload = {}
        job.payload["include_global_requirements"] = True
        self._apply_project_limits(job)
        job.restart_count += 1
        job.stop_requested = False
        job.exit_code = None
        job.started_at = None
        job.finished_at = None
        job.progress = 0
        job.stages = []
        job.token_actual = 0
        job.token_debited = 0
        job.token_shortfall = 0
        if job.token_reserved:
            job.token_status = "reserved"
        job.metrics = {
            "token_usage": {"prompt": 0, "completion": 0, "total": 0, "cached": None},
            "errors": 0,
            "resolved": 0,
            "warnings": 0,
            "queue_wait_sec": None,
            "runtime_sec": None,
        }
        job.append_log(f"--- restart #{job.restart_count} ---")
        job.set_status("queued")
        self.queue.put(job_id)
        return True

    def _worker_loop(self, worker_id: int) -> None:
        while True:
            job_id = self.queue.get()
            if job_id is None:
                self.queue.task_done()
                break
            job = self.get_job(job_id)
            if not job:
                self.queue.task_done()
                continue
            if job.status in {"stopped", "completed", "failed"}:
                self.queue.task_done()
                continue
            if job.status == "paused":
                self.queue.put(job_id)
                self.queue.task_done()
                time.sleep(0.5)
                continue
            self._run_job(job)
            self.queue.task_done()

    def _run_job(self, job: Job) -> None:
        job.set_status("running")
        job.started_at = _now_iso()
        job.token_status = "running" if job.token_reserved else job.token_status
        job.persist()
        job.metrics["queue_wait_sec"] = self._compute_wait_seconds(job)
        job.set_progress(5)
        try:
            self._prepare_repo(job)
            self._guardrail_check(job)
        except Exception as exc:
            job.append_log(f"Job blocked: {exc}")
            job.exit_code = 1
            job.set_status("failed")
            job.set_progress(100)
            job.finished_at = _now_iso()
            job.persist(force=True)
            self._settle_tokens(job)
            self._maybe_notify(job)
            return
        try:
            command = self._build_command(job)
        except Exception as exc:
            job.append_log(f"Failed to build command: {exc}")
            job.exit_code = 1
            job.set_status("failed")
            job.set_progress(100)
            job.finished_at = _now_iso()
            job.persist(force=True)
            self._settle_tokens(job)
            self._maybe_notify(job)
            return
        job.append_log("Starting job: " + " ".join(command))
        try:
            env = os.environ.copy()
            use_defaults = job.payload.get("use_default_secrets", True)
            if use_defaults and job.owner:
                env.update(_get_secret_store(job.owner).get_env())
            job_secrets = job.payload.get("job_secrets") or {}
            if isinstance(job_secrets, list):
                for entry in job_secrets:
                    if not isinstance(entry, dict):
                        continue
                    name = (entry.get("name") or "").strip()
                    value = (entry.get("value") or "").strip()
                    if name and value:
                        env[name] = value
            elif isinstance(job_secrets, dict):
                for name, value in job_secrets.items():
                    if name and value:
                        env[str(name)] = str(value)
            job.process = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            job.pid = job.process.pid
        except Exception as exc:
            job.append_log(f"Failed to start process: {exc}")
            job.exit_code = 1
            job.set_status("failed")
            job.set_progress(100)
            job.finished_at = _now_iso()
            job.persist(force=True)
            self._settle_tokens(job)
            self._maybe_notify(job)
            return

        assert job.process.stdout is not None
        for line in job.process.stdout:
            if not line:
                continue
            if self._handle_event_line(job, line):
                continue
            self._update_metrics(job, line)
            job.append_log(line)

        job.exit_code = job.process.wait()
        job.process = None
        job.pid = None
        job.finished_at = _now_iso()
        job.metrics["runtime_sec"] = self._compute_runtime_seconds(job)
        completion_reason = None
        if job.exit_code == 0 and job.workflow in {"project_solver", "project"}:
            completion_reason = _completion_reason_from_output(job.output_paths.get("primary"))
        if job.exit_code == 0:
            try:
                self._finalize_repo(job)
            except Exception as exc:
                job.append_log(f"Repo finalize failed: {exc}")
        if job.stop_requested:
            job.set_status("stopped")
        elif job.exit_code == 0:
            job.set_status("completed")
        else:
            job.set_status("failed")
        job.set_progress(100)
        if completion_reason and job.status == "completed":
            job.update_stage("finalize", completion_reason)
        job.append_log(f"Job finished with exit code {job.exit_code}")
        job.persist(force=True)
        self._settle_tokens(job)
        if job.status == "completed" and not completion_reason and job.token_shortfall > 0:
            job.update_stage("finalize", "tokens")
        self._maybe_notify(job)

    def _handle_event_line(self, job: Job, line: str) -> bool:
        if not line.startswith("__RAG_EVENT__ "):
            return False
        payload = line[len("__RAG_EVENT__ "):].strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return False
        event_type = data.get("type")
        if event_type == "workflow_selected":
            workflow = data.get("workflow")
            if workflow:
                job.workflow = workflow
        elif event_type == "stage":
            stage_name = data.get("stage")
            status = data.get("status")
            progress = data.get("progress")
            message = data.get("message")
            if stage_name and status:
                job.update_stage(stage_name, status, message=message)
            if isinstance(progress, int):
                job.set_progress(progress)
        elif event_type == "workflow_complete":
            status = data.get("status")
            if status and job.status == "running":
                job.update_stage("finalize", status)
        return True

    def _update_metrics(self, job: Job, line: str) -> None:
        if "ERROR" in line or "Traceback" in line:
            job.metrics["errors"] += 1
        if "WARNING" in line:
            job.metrics["warnings"] += 1
        if re.search(r"\brecover(?:ed|y)\b|\bretry(?:ing|ed)\b|\bfallback\b", line, re.IGNORECASE):
            job.metrics["resolved"] += 1
        match = TOKEN_RE.search(line)
        if match:
            run_prompt = self._coerce_int(match.group("run_prompt"))
            run_completion = self._coerce_int(match.group("run_completion"))
            run_total = self._coerce_int(match.group("run_total"))
            cached = self._coerce_int(match.group("cached")) if match.group("cached") else None
            usage = job.metrics.get("token_usage") or {}
            usage["prompt"] = run_prompt or usage.get("prompt", 0)
            usage["completion"] = run_completion or usage.get("completion", 0)
            usage["total"] = run_total or usage.get("total", 0)
            if cached is not None:
                usage["cached"] = cached
            job.metrics["token_usage"] = usage
            if run_total is not None:
                job.token_actual = run_total

    def _reserve_tokens(self, job: Job, estimate: int) -> None:
        job.token_estimate = int(estimate or 0)
        job.token_reserved = int(estimate or 0)
        job.token_status = "reserved"
        job.append_log(f"Reserved {job.token_reserved} tokens for estimate.")
        token_ledger.record(
            job.owner,
            "reserve",
            0,
            {"job_id": job.job_id, "estimate": job.token_estimate, "workflow": job.workflow},
        )
        job.persist(force=True)

    def _release_tokens(self, job: Job, reason: str = "release") -> None:
        if job.token_reserved <= 0:
            return
        token_ledger.record(
            job.owner,
            "release",
            0,
            {"job_id": job.job_id, "reserved": job.token_reserved, "reason": reason},
        )
        job.token_reserved = 0
        job.persist(force=True)

    def _settle_tokens(self, job: Job) -> None:
        if job.token_status == "settled":
            return
        actual = int(job.metrics.get("token_usage", {}).get("total") or 0)
        job.token_actual = actual
        if job.token_reserved:
            self._release_tokens(job, reason="settle")
        if actual > 0:
            entry = token_ledger.record(
                job.owner,
                "debit",
                -actual,
                {"job_id": job.job_id, "estimate": job.token_estimate, "workflow": job.workflow},
            )
            shortfall = int(entry.get("shortfall") or 0)
            job.token_debited = actual - shortfall
            job.token_shortfall = shortfall
            job.append_log(f"Debited {job.token_debited} tokens (shortfall {job.token_shortfall}).")
        job.token_status = "settled"
        job.persist(force=True)
        _notify_portal_usage(job)

    def _maybe_notify(self, job: Job) -> None:
        if job.status not in {"completed", "failed"}:
            return
        if job.notified_at or job.notification_error:
            return
        if _is_user_active(job.owner):
            return
        recipient = job.notify_email or user_store.get_email(job.owner)
        if not recipient:
            job.notification_error = "No notification email configured."
            job.persist(force=True)
            return
        if not EMAIL_RE.match(recipient):
            job.notification_error = "Invalid notification email."
            job.persist(force=True)
            return
        subject = f"Refiner job {job.status}: {job.project_name or job.job_id[:8]}"
        body = _job_notification_body(job)
        error = _send_email(recipient, subject, body)
        if error:
            job.notification_error = error
            job.append_log(error)
        else:
            job.notified_at = _now_iso()
            job.notified_via = "email"
            job.append_log(f"Notification email sent to {recipient}")
        job.persist(force=True)

    def _build_command(self, job: Job) -> List[str]:
        payload = job.payload
        workflow = payload.get("workflow") or "project_solver"
        if workflow in {"project_solver", "project"} and not payload.get("llm_max_tokens"):
            payload["llm_max_tokens"] = DEFAULT_LLM_MAX_TOKENS
        command = [
            os.getenv("REFINER_PYTHON", sys.executable),
            os.path.join(BASE_DIR, "run_refiner.py"),
            "--log-file",
            job.log_path,
            "--emit-events",
            "--events-file",
            job.events_path,
        ]
        if payload.get("verbose", True):
            command.append("--verbose")
        if payload.get("debug"):
            command.append("--debug")

        self._add_llm_args(command, payload)

        if workflow == "project_solver" or workflow == "project":
            project_root = self._resolve_project_root(job)
            command += ["--project", project_root]
            requirements_path = self._resolve_requirements(job)
            if requirements_path:
                command += ["--requirements", requirements_path]
            if payload.get("project_run"):
                command.append("--project-run")
            if payload.get("project_max_steps"):
                command += ["--project-max-steps", str(payload.get("project_max_steps"))]
            if payload.get("project_iterations"):
                command += ["--project-iterations", str(payload.get("project_iterations"))]
            if payload.get("project_output_dir"):
                command += ["--project-output-dir", str(payload.get("project_output_dir"))]
            if payload.get("codingagent"):
                command += ["--codingagent", payload.get("codingagent")]
            if payload.get("codingagent_fallback"):
                command += ["--codingagent-fallback", payload.get("codingagent_fallback")]
            if payload.get("codingagent_model"):
                command += ["--codingagent-model", payload.get("codingagent_model")]
            if payload.get("codingagent_reasoning_effort"):
                command += ["--codingagent-reasoning-effort", payload.get("codingagent_reasoning_effort")]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
        elif workflow == "topic_research":
            source_path = self._resolve_topic_source(job)
            command += ["--topic-research", source_path]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
            if payload.get("max_iterations"):
                command += ["--max-iterations", str(payload.get("max_iterations"))]
            for ctx in payload.get("context_sources", []) or []:
                command += ["--context", ctx]
            if payload.get("references_output"):
                command += ["--references-output", payload.get("references_output")]
        elif workflow == "jira_analysis":
            command.append("--analyze-jira")
            if payload.get("projects"):
                command += ["--projects", payload.get("projects")]
            if payload.get("jql"):
                command += ["--jql", payload.get("jql")]
            if payload.get("action_plan"):
                command.append("--action-plan")
            if payload.get("dry_run"):
                command.append("--dry-run")
            if payload.get("post_comments"):
                command.append("--post-comments")
            if payload.get("post_target"):
                command += ["--post-target", payload.get("post_target")]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
        elif workflow == "confluence_analysis":
            command.append("--analyze-confluence")
            if payload.get("space"):
                command += ["--space", payload.get("space")]
            if payload.get("use_rovo"):
                command.append("--use-rovo")
            if payload.get("action_plan"):
                command.append("--action-plan")
            if payload.get("dry_run"):
                command.append("--dry-run")
            if payload.get("post_comments"):
                command.append("--post-comments")
            if payload.get("post_target"):
                command += ["--post-target", payload.get("post_target")]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
        elif workflow == "delivery_pipeline":
            command.append("--delivery")
            project_root = self._resolve_project_root(job)
            command += ["--project", project_root]
            if payload.get("delivery_config"):
                command += ["--delivery-config", payload.get("delivery_config")]
            if payload.get("delivery_run"):
                command.append("--delivery-run")
            if payload.get("delivery_allow_unfinished"):
                command.append("--delivery-allow-unfinished")
            if payload.get("delivery_enable_interim"):
                command.append("--delivery-enable-interim")
            if payload.get("delivery_project_solution"):
                command += ["--delivery-project-solution", payload.get("delivery_project_solution")]
            if job.output_paths.get("primary"):
                command += ["--output", job.output_paths["primary"]]
        elif workflow == "jira_stats":
            pass

        if payload.get("disable_jira"):
            command.append("--disable-jira")
        if payload.get("disable_confluence"):
            command.append("--disable-confluence")

        extra_args = payload.get("extra_args")
        if isinstance(extra_args, str) and extra_args.strip():
            command += shlex.split(extra_args)
        return command

    def _resolve_output_paths(self, job: Job) -> Dict[str, str]:
        job_dir = os.path.join(JOB_ROOT, job.job_id)
        workflow = job.payload.get("workflow") or "project_solver"
        override = job.payload.get("output_path") or job.payload.get("output")
        if override:
            return {"primary": override}
        if workflow in {"project_solver", "project"}:
            return {"primary": os.path.join(job_dir, "project_solution.json")}
        if workflow == "topic_research":
            return {"primary": job.payload.get("topic_output") or os.path.join(job_dir, "researched_document.md")}
        if workflow == "jira_analysis":
            return {"primary": job.payload.get("jira_output") or os.path.join(job_dir, "jira_report.html")}
        if workflow == "confluence_analysis":
            return {"primary": job.payload.get("confluence_output") or os.path.join(job_dir, "confluence_report.html")}
        if workflow == "delivery_pipeline":
            return {"primary": job.payload.get("pipeline_output") or os.path.join(job_dir, "pipeline_report.json")}
        return {}

    def _resolve_project_root(self, job: Job) -> str:
        project_root = job.payload.get("project_root") or job.payload.get("delivery_project_root")
        project_name = job.payload.get("project_name")
        create_project = bool(job.payload.get("create_project"))
        if project_root:
            if create_project and not os.path.exists(project_root):
                os.makedirs(project_root, exist_ok=True)
            return project_root
        if project_name:
            slug = _slugify(project_name)
            project_root = os.path.join(PROJECTS_ROOT, f"{slug}-{job.job_id[:8]}")
            os.makedirs(project_root, exist_ok=True)
            return project_root
        raise ValueError("Project root or project name is required for this workflow")

    def _prepare_repo(self, job: Job) -> None:
        payload = job.payload
        repo_input = (payload.get("repo_url") or payload.get("repo") or "").strip()
        if not repo_input:
            if self._maybe_create_starter_repo(job):
                return
            return
        workflow = payload.get("workflow") or "project_solver"
        if workflow not in {"project_solver", "project", "delivery_pipeline"}:
            return
        owner, repo = self._parse_repo_input(repo_input)
        if not owner or not repo:
            raise ValueError("Invalid GitHub repo input. Use owner/repo or full URL.")
        fork_org = (payload.get("fork_org") or DEFAULT_FORK_ORG).strip()
        token = self._get_github_token(job)
        skip_fork = bool(payload.get("skip_fork")) or owner == fork_org
        fork_repo = None
        fork = None
        if skip_fork:
            job.append_log(f"Using existing repo without fork: {owner}/{repo}")
            default_branch = payload.get("repo_branch") or "main"
            fork_org = owner
            fork_repo = repo
        else:
            if not token:
                raise ValueError("Missing GitHub token. Add GITHUB_TOKEN in Credentials Vault or per-job secrets.")
            fork_repo = self._build_fork_repo_name(repo, job.owner or "user", job.project_name or repo)
            job.append_log(f"Preparing GitHub workspace for {owner}/{repo} -> {fork_org}/{fork_repo}")
            fork = self._ensure_fork(owner, repo, fork_org, fork_repo, token, job)
            default_branch = fork.get("default_branch") or payload.get("repo_branch") or "main"
        workspace = os.path.join(WORKSPACE_ROOT, job.job_id, repo)
        if os.path.exists(workspace):
            try:
                shutil.rmtree(workspace)
            except Exception:
                pass
        os.makedirs(os.path.dirname(workspace), exist_ok=True)

        fork_name = fork.get("name") if fork else fork_repo
        clone_url = f"https://github.com/{fork_org}/{fork_name}.git"
        self._git_clone(clone_url, workspace, default_branch, token, job)
        branch_name = (payload.get("work_branch") or f"refiner/{job.job_id[:8]}").strip()
        self._git_checkout(workspace, branch_name, job)

        project_subdir = (payload.get("repo_subdir") or "").strip()
        project_root = os.path.join(workspace, project_subdir) if project_subdir else workspace
        if not os.path.isdir(project_root):
            raise ValueError(f"Project subdir does not exist: {project_subdir or '.'}")

        requirements_rel = (payload.get("requirements_relpath") or "").strip()
        if requirements_rel:
            req_path = os.path.join(workspace, requirements_rel)
            if not os.path.exists(req_path):
                raise ValueError(f"Requirements path not found: {requirements_rel}")
            payload["requirements_path"] = req_path
        elif payload.get("requirements_text"):
            req_path = os.path.join(workspace, "requirements.md")
            with open(req_path, "w", encoding="utf-8") as handle:
                handle.write(payload.get("requirements_text"))
            payload["requirements_path"] = req_path

        payload["project_root"] = project_root
        job.repo_info = {
            "source": "github",
            "owner": owner,
            "repo": repo,
            "fork_org": fork_org,
            "fork_repo": fork_name,
            "branch": branch_name,
            "workspace": workspace,
            "clone_url": clone_url,
            "skip_fork": skip_fork,
        }
        if not job.project_name:
            job.project_name = repo

    def _guardrail_check(self, job: Job) -> None:
        payload = job.payload
        text = (payload.get("requirements_text") or "").strip()
        if text:
            reason = _guardrail_scan(text)
            if reason:
                raise ValueError(f"Guardrail blocked requirements: {reason}")
            return
        req_path = (payload.get("requirements_path") or "").strip()
        if req_path and os.path.exists(req_path):
            content = _read_file_limited(req_path, REQUIREMENTS_MAX_BYTES)
            reason = _guardrail_scan(content)
            if reason:
                raise ValueError(f"Guardrail blocked requirements file: {reason}")

    def _maybe_create_starter_repo(self, job: Job) -> bool:
        payload = job.payload
        workflow = payload.get("workflow") or "project_solver"
        if workflow not in {"project_solver", "project"}:
            return False
        if payload.get("project_root"):
            return False
        requirements_text = (payload.get("requirements_text") or "").strip()
        requirements_path = (payload.get("requirements_path") or "").strip()
        if not requirements_text and not requirements_path:
            return False
        project_name = job.project_name or payload.get("project_name") or ""
        if not project_name:
            raise ValueError("Project name is required to create a starter repo.")

        fork_org = (payload.get("fork_org") or DEFAULT_FORK_ORG).strip()
        token = self._get_github_token(job)
        if not token:
            raise ValueError("Missing GitHub token. Add GITHUB_TOKEN in Credentials Vault or per-job secrets.")

        repo_name = self._build_fork_repo_name(_slugify(project_name), job.owner or "user", project_name)
        job.append_log(f"Creating starter repo {fork_org}/{repo_name}")
        repo_data = self._ensure_repo_exists(fork_org, repo_name, token, job, payload)
        default_branch = repo_data.get("default_branch") or "main"
        workspace = os.path.join(WORKSPACE_ROOT, job.job_id, repo_name)
        if os.path.exists(workspace):
            try:
                shutil.rmtree(workspace)
            except Exception:
                pass
        os.makedirs(os.path.dirname(workspace), exist_ok=True)

        clone_url = f"https://github.com/{fork_org}/{repo_name}.git"
        self._git_clone(clone_url, workspace, default_branch, token, job)
        branch_name = (payload.get("work_branch") or f"refiner/{job.job_id[:8]}").strip()
        self._git_checkout(workspace, branch_name, job)

        requirements_rel = (payload.get("requirements_relpath") or "requirements.md").strip()
        req_path = os.path.join(workspace, requirements_rel)
        os.makedirs(os.path.dirname(req_path), exist_ok=True)
        if requirements_text:
            with open(req_path, "w", encoding="utf-8") as handle:
                handle.write(requirements_text)
        else:
            if not os.path.exists(requirements_path):
                raise ValueError("Requirements path not found on server.")
            shutil.copyfile(requirements_path, req_path)
        payload["requirements_path"] = req_path
        payload["project_root"] = workspace

        job.repo_info = {
            "source": "starter",
            "owner": fork_org,
            "repo": repo_name,
            "fork_org": fork_org,
            "fork_repo": repo_name,
            "branch": branch_name,
            "workspace": workspace,
            "clone_url": clone_url,
        }
        if not job.project_name:
            job.project_name = project_name
        return True

    def _finalize_repo(self, job: Job) -> None:
        if not job.repo_info:
            return
        workspace = job.repo_info.get("workspace")
        branch = job.repo_info.get("branch")
        if not workspace or not branch:
            return
        if not self._git_has_changes(workspace, job):
            job.append_log("No git changes detected; skipping commit/push.")
            return
        author_name = job.payload.get("git_author_name") or "refiner-bot"
        author_email = job.payload.get("git_author_email") or "automation@neuralmimicry.ai"
        commit_message = job.payload.get("commit_message") or f"Refiner updates ({job.job_id[:8]})"
        self._git_config(workspace, author_name, author_email, job)
        self._git_add_all(workspace, job)
        self._git_commit(workspace, commit_message, job)
        token = self._get_github_token(job)
        self._git_push(workspace, branch, token, job)
        fork_org = job.repo_info.get("fork_org")
        fork_repo = job.repo_info.get("fork_repo") or job.repo_info.get("repo")
        if fork_org and fork_repo:
            repo_url = f"https://github.com/{fork_org}/{fork_repo}"
            job.append_log(f"Repo ready: {repo_url} (branch: {branch})")
            job.repo_info["repo_url"] = repo_url

    def _get_github_token(self, job: Job) -> Optional[str]:
        job_secrets = job.payload.get("job_secrets") or []
        candidates = ["GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"]
        if isinstance(job_secrets, list):
            for entry in job_secrets:
                if not isinstance(entry, dict):
                    continue
                name = (entry.get("name") or "").strip()
                if name in candidates:
                    return (entry.get("value") or "").strip()
        elif isinstance(job_secrets, dict):
            for key in candidates:
                if key in job_secrets:
                    return str(job_secrets.get(key) or "").strip()
        if job.owner:
            env = _get_secret_store(job.owner).get_env()
            for key in candidates:
                if env.get(key):
                    return env.get(key)
        return None

    @staticmethod
    def _parse_repo_input(value: str) -> Optional[tuple]:
        value = value.strip()
        if value.startswith("http://") or value.startswith("https://"):
            match = re.match(r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\\.git)?$", value)
            if not match:
                return None, None
            return match.group("owner"), match.group("repo")
        if value.count("/") == 1:
            owner, repo = value.split("/", 1)
            repo = repo.strip()
            if repo.endswith(".git"):
                repo = repo[:-4]
            return owner.strip(), repo
        return None, None

    def _ensure_fork(
        self,
        owner: str,
        repo: str,
        fork_org: str,
        fork_repo: str,
        token: str,
        job: Job,
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        fork_url = f"https://api.github.com/repos/{fork_org}/{fork_repo}"
        parent_full_name = f"{owner}/{repo}"
        resp = requests.get(fork_url, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            parent = data.get("parent") or {}
            if parent.get("full_name") and parent.get("full_name") != parent_full_name:
                raise ValueError(f"Existing fork {fork_org}/{fork_repo} is not based on {parent_full_name}.")
            return data
        create_url = f"https://api.github.com/repos/{owner}/{repo}/forks"
        payload = {"organization": fork_org}
        create_resp = requests.post(create_url, headers=headers, json=payload, timeout=20)
        if create_resp.status_code not in (202, 201):
            raise ValueError(f"Fork failed: {create_resp.status_code} {create_resp.text}")
        for _ in range(20):
            time.sleep(2)
            resp = requests.get(fork_url, headers=headers, timeout=20)
            if resp.status_code == 200:
                return resp.json()
        # Fork may exist under default name; attempt rename if needed
        default_fork_url = f"https://api.github.com/repos/{fork_org}/{repo}"
        resp = requests.get(default_fork_url, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            parent = data.get("parent") or {}
            if parent.get("full_name") != parent_full_name:
                raise ValueError(f"Existing fork {fork_org}/{repo} is not based on {parent_full_name}.")
            if fork_repo != repo:
                rename_url = f"https://api.github.com/repos/{fork_org}/{repo}"
                rename_resp = requests.patch(rename_url, headers=headers, json={"name": fork_repo}, timeout=20)
                if rename_resp.status_code not in (200, 201):
                    raise ValueError(f"Fork rename failed: {rename_resp.status_code} {rename_resp.text}")
                return rename_resp.json()
            return data
        raise ValueError("Fork not ready yet. Try again shortly.")

    def _ensure_repo_exists(
        self,
        org: str,
        repo_name: str,
        token: str,
        job: Job,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        repo_url = f"https://api.github.com/repos/{org}/{repo_name}"
        resp = requests.get(repo_url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.json()
        private_flag = payload.get("repo_private")
        if private_flag is None:
            private_flag = DEFAULT_REPO_PRIVATE
        create_url = f"https://api.github.com/orgs/{org}/repos"
        body = {
            "name": repo_name,
            "private": bool(private_flag),
            "auto_init": True,
            "description": payload.get("repo_description") or "Refiner starter repo",
        }
        create_resp = requests.post(create_url, headers=headers, json=body, timeout=20)
        if create_resp.status_code not in (201, 202):
            raise ValueError(f"Repo create failed: {create_resp.status_code} {create_resp.text}")
        return create_resp.json()

    @staticmethod
    def _build_fork_repo_name(repo: str, owner: str, project_name: str) -> str:
        repo_slug = _slugify(repo)
        owner_slug = _slugify(owner)
        project_slug = _slugify(project_name)
        if project_slug and project_slug != repo_slug:
            name = f"{repo_slug}-{owner_slug}-{project_slug}"
        else:
            name = f"{repo_slug}-{owner_slug}"
        if len(name) > 90:
            trim = name[:90]
            return trim.rstrip("-")
        return name

    def _git_env(self, token: Optional[str], askpass_dir: str) -> Dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if token:
            os.makedirs(askpass_dir, exist_ok=True)
            askpass_path = os.path.join(askpass_dir, ".git-askpass.sh")
            script = (
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "*Username*) echo \"x-access-token\" ;;\n"
                "*Password*) echo \"" + token.replace('"', '\\"') + "\" ;;\n"
                "*) echo \"" + token.replace('"', '\\"') + "\" ;;\n"
                "esac\n"
            )
            with open(askpass_path, "w", encoding="utf-8") as handle:
                handle.write(script)
            try:
                os.chmod(askpass_path, 0o700)
            except Exception:
                pass
            env["GIT_ASKPASS"] = askpass_path
        return env

    def _git_run(self, command: List[str], cwd: str, job: Job, token: Optional[str] = None) -> subprocess.CompletedProcess:
        askpass_dir = os.path.dirname(job.log_path) if job.log_path else cwd
        env = self._git_env(token, askpass_dir)
        job.append_log(f"git: {' '.join(command)}")
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True, env=env, check=False)

    def _git_clone(self, clone_url: str, workspace: str, branch: str, token: Optional[str], job: Job) -> None:
        result = self._git_run(
            ["git", "clone", "--depth", "1", "--branch", branch, clone_url, workspace],
            cwd=BASE_DIR,
            job=job,
            token=token,
        )
        if result.returncode != 0:
            raise ValueError(f"git clone failed: {result.stderr.strip()}")

    def _git_checkout(self, workspace: str, branch: str, job: Job) -> None:
        result = self._git_run(["git", "checkout", "-B", branch], cwd=workspace, job=job)
        if result.returncode != 0:
            raise ValueError(f"git checkout failed: {result.stderr.strip()}")

    def _git_has_changes(self, workspace: str, job: Job) -> bool:
        result = self._git_run(["git", "status", "--porcelain"], cwd=workspace, job=job)
        return bool(result.stdout.strip())

    def _git_config(self, workspace: str, name: str, email: str, job: Job) -> None:
        self._git_run(["git", "config", "user.name", name], cwd=workspace, job=job)
        self._git_run(["git", "config", "user.email", email], cwd=workspace, job=job)

    def _git_add_all(self, workspace: str, job: Job) -> None:
        result = self._git_run(["git", "add", "-A"], cwd=workspace, job=job)
        if result.returncode != 0:
            raise ValueError(f"git add failed: {result.stderr.strip()}")

    def _git_commit(self, workspace: str, message: str, job: Job) -> None:
        result = self._git_run(["git", "commit", "-m", message], cwd=workspace, job=job)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "nothing to commit" in stderr.lower():
                return
            raise ValueError(f"git commit failed: {stderr}")

    def _git_push(self, workspace: str, branch: str, token: Optional[str], job: Job) -> None:
        result = self._git_run(["git", "push", "origin", branch], cwd=workspace, job=job, token=token)
        if result.returncode != 0:
            raise ValueError(f"git push failed: {result.stderr.strip()}")

    def _resolve_requirements(self, job: Job) -> Optional[str]:
        requirements_path = job.payload.get("requirements_path")
        requirements_text = job.payload.get("requirements_text")
        if requirements_path:
            return requirements_path
        if requirements_text:
            job_dir = os.path.join(JOB_ROOT, job.job_id)
            path = os.path.join(job_dir, "requirements.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(requirements_text)
            return path
        return None

    def _resolve_topic_source(self, job: Job) -> str:
        source = job.payload.get("topic_source") or job.payload.get("topic_research")
        if not source:
            raise ValueError("Topic source is required for topic research")
        if source.startswith("http://") or source.startswith("https://"):
            return source
        job_dir = os.path.join(JOB_ROOT, job.job_id)
        path = os.path.join(job_dir, "topic.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        return path

    def _add_llm_args(self, command: List[str], payload: Dict[str, Any]) -> None:
        if payload.get("llm_provider"):
            command += ["--llm-provider", payload.get("llm_provider")]
        if payload.get("llm_model"):
            command += ["--llm-model", payload.get("llm_model")]
        if payload.get("llm_reasoning_effort"):
            command += ["--llm-reasoning-effort", payload.get("llm_reasoning_effort")]
        if payload.get("fallback_llm_provider"):
            command += ["--fallback-llm-provider", payload.get("fallback_llm_provider")]
        if payload.get("fallback_llm_model"):
            command += ["--fallback-llm-model", payload.get("fallback_llm_model")]
        if payload.get("ollama_base_url"):
            command += ["--ollama-base-url", payload.get("ollama_base_url")]
        if payload.get("llm_max_tokens"):
            command += ["--llm-max-tokens", str(payload.get("llm_max_tokens"))]
        if payload.get("llm_chunk_size"):
            command += ["--llm-chunk-size", str(payload.get("llm_chunk_size"))]
        if payload.get("llm_temperature") is not None:
            command += ["--llm-temperature", str(payload.get("llm_temperature"))]
        if payload.get("llm_timeout"):
            command += ["--llm-timeout", str(payload.get("llm_timeout"))]
        if payload.get("llm_inter_request_gap"):
            command += ["--llm-inter-request-gap", str(payload.get("llm_inter_request_gap"))]

    def _compute_wait_seconds(self, job: Job) -> Optional[float]:
        if not job.started_at:
            return None
        created = _parse_timestamp(job.created_at)
        started = _parse_timestamp(job.started_at)
        if not created or not started:
            return None
        return started.timestamp() - created.timestamp()

    def _compute_runtime_seconds(self, job: Job) -> Optional[float]:
        if not (job.started_at and job.finished_at):
            return None
        started = _parse_timestamp(job.started_at)
        finished = _parse_timestamp(job.finished_at)
        if not started or not finished:
            return None
        return finished.timestamp() - started.timestamp()

    @staticmethod
    def _coerce_int(value: Optional[str]) -> Optional[int]:
        if value is None or value == "?":
            return None
        try:
            return int(value)
        except Exception:
            return None


manager = JobManager()
user_store = UserStore(USERS_PATH)
user_store.ensure_admin_from_env()
_secret_stores: Dict[str, SecretStore] = {}
_user_activity: Dict[str, float] = {}
_user_activity_lock = threading.Lock()
token_ledger = TokenLedger(LEDGER_ROOT)
rag_store = RagStore(RAG_STORE_ROOT)
mcp_store = MCPServerStore(MCP_STORE_ROOT)


def _get_secret_store(user: str) -> SecretStore:
    key = user or "default"
    store = _secret_stores.get(key)
    if store:
        return store
    path = os.path.join(SECRET_STORE_ROOT, f"{key}.json")
    store = SecretStore(path)
    _secret_stores[key] = store
    return store


def _get_github_api_token(user: Optional[str]) -> Optional[str]:
    if not user:
        return None
    env = _get_secret_store(user).get_env()
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        if env.get(key):
            return env.get(key)
    return None


def _load_llm_config() -> Dict[str, Any]:
    path = os.path.join(BASE_DIR, "config.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_llm_settings(
    user: str,
    provider_hint: Optional[str] = None,
    model_hint: Optional[str] = None,
) -> Dict[str, Any]:
    env = _get_secret_store(user).get_env()
    cfg = _load_llm_config()
    providers = cfg.get("llm_providers") if isinstance(cfg.get("llm_providers"), list) else []

    provider_type = None
    model = model_hint
    base_url = None

    if provider_hint:
        match = next((p for p in providers if p.get("name") == provider_hint), None)
        if match:
            provider_type = match.get("type") or provider_hint
            model = model or match.get("model")
            base_url = match.get("base_url")
        else:
            provider_type = provider_hint

    if not provider_type:
        if env.get("OPENAI_API_KEY"):
            provider_type = "openai"
        elif env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or env.get("GOOGLE_GENERATIVE_AI_API_KEY"):
            provider_type = "gemini"
        else:
            provider_type = "ollama"

    if provider_type in {"gpt", "chatgpt", "openai"}:
        api_key = env.get("OPENAI_API_KEY")
    elif provider_type in {"gemini", "google"}:
        api_key = (
            env.get("GEMINI_API_KEY")
            or env.get("GOOGLE_API_KEY")
            or env.get("GOOGLE_GENERATIVE_AI_API_KEY")
        )
    else:
        api_key = None

    if not base_url:
        base_url = env.get("OLLAMA_BASE_URL") if provider_type == "ollama" else None

    return {
        "provider": provider_type,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


def _guardrail_scan(text: str) -> Optional[str]:
    if not text:
        return None
    lower = text.lower()
    for category, patterns in GUARDRAIL_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, lower, flags=re.IGNORECASE | re.DOTALL):
                return f"Detected {category} content via pattern: {pat}"
    return None


def _read_file_limited(path: str, max_bytes: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(max_bytes)
    except Exception:
        return ""


def _read_json_file(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _iso_from_mtime(path: str) -> Optional[str]:
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return None
    return dt.datetime.fromtimestamp(mtime, UK_TZ).strftime(UK_DATETIME_FORMAT)


def _mtime_dt(path: str) -> Optional[dt.datetime]:
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return None
    return dt.datetime.fromtimestamp(mtime, UK_TZ)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _requirements_progress_from_solution(solution: Dict[str, Any]) -> Dict[str, Any]:
    total = 0
    completed = 0
    in_progress = 0
    remaining = 0
    source = "none"
    trace = solution.get("requirement_traceability") if isinstance(solution, dict) else None
    summary = trace.get("summary") if isinstance(trace, dict) else None
    if isinstance(summary, dict):
        total = _safe_int(summary.get("total"))
        completed = _safe_int(summary.get("with_changes"))
        in_progress = _safe_int(summary.get("planned_only"))
        remaining = _safe_int(summary.get("unmapped"))
        if total <= 0:
            total = completed + in_progress + remaining
        if total > 0:
            remaining = max(total - completed - in_progress, 0)
        source = "traceability"
    elif isinstance(trace, dict):
        reqs = trace.get("requirements")
        if isinstance(reqs, list) and reqs:
            for entry in reqs:
                if not isinstance(entry, dict):
                    continue
                status = str(entry.get("status") or "").lower()
                total += 1
                if status == "covered":
                    completed += 1
                elif status == "planned":
                    in_progress += 1
                else:
                    remaining += 1
            source = "traceability"
    if total == 0:
        register = solution.get("requirements_register") if isinstance(solution, dict) else None
        if isinstance(register, dict):
            reqs = register.get("requirements")
            if isinstance(reqs, list) and reqs:
                total = len([entry for entry in reqs if entry is not None])
                remaining = total
                source = "register"
    return {
        "total": total,
        "completed": completed,
        "in_progress": in_progress,
        "remaining": remaining,
        "source": source,
    }


def _requirements_progress_from_requirements_file(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    text = _read_file_limited(path, 250_000)
    if not text:
        return None
    total = _count_req_lines(text)
    if total <= 0:
        return None
    return {
        "total": total,
        "completed": 0,
        "in_progress": 0,
        "remaining": total,
        "source": "requirements_file",
    }


def _normalize_summary_text(text: str, max_len: int = 240) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _requirements_summary_from_register(solution: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    register = solution.get("requirements_register") if isinstance(solution, dict) else None
    if not isinstance(register, dict):
        return None
    reqs = register.get("requirements")
    if not isinstance(reqs, list) or not reqs:
        return None
    items = []
    for entry in reqs:
        if not isinstance(entry, dict):
            continue
        req_id = entry.get("id") or ""
        title = entry.get("title") or ""
        desc = entry.get("description") or ""
        source = entry.get("source")
        title = _normalize_summary_text(str(title)) if title else ""
        desc = _normalize_summary_text(str(desc), max_len=280) if desc else ""
        if title and desc and title.lower() in desc.lower():
            text = desc
        elif title and desc:
            text = f"{title}. {desc}"
        else:
            text = title or desc
        if text:
            items.append({"id": req_id, "text": text, "source": source})
    if not items:
        return None
    return {"items": items, "source": "register", "total": len(items)}


def _requirements_summary_from_text(text: str, max_items: int = 12) -> Dict[str, Any]:
    paragraphs = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    lead = paragraphs[0] if paragraphs else ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullet_re = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
    bullets = [bullet_re.sub("", line).strip() for line in lines if bullet_re.match(line)]
    items = []
    if bullets:
        for line in bullets[:max_items]:
            if line:
                items.append({"id": "", "text": _normalize_summary_text(line, max_len=280)})
    else:
        sentences = []
        for para in paragraphs:
            for sentence in re.split(r"(?<=[.!?])\s+", para):
                cleaned = sentence.strip()
                if len(cleaned) >= 24:
                    sentences.append(cleaned)
        for sentence in sentences[:max_items]:
            items.append({"id": "", "text": _normalize_summary_text(sentence, max_len=280)})
    return {
        "summary": _normalize_summary_text(lead, max_len=320),
        "items": items,
        "source": "requirements_file",
        "total": len(items),
    }


def _completion_reason_from_output(output_path: Optional[str]) -> Optional[str]:
    if not output_path or not os.path.exists(output_path):
        return None
    data = _read_json_file(output_path)
    if not isinstance(data, dict):
        return None
    summary = data.get("completion_summary")
    if not isinstance(summary, dict):
        return None
    if summary.get("max_steps_reached"):
        return "steps"
    exhausted = summary.get("iterations_exhausted_sources")
    if isinstance(exhausted, list) and exhausted:
        return "iterations"
    return None


def _completion_summary_from_output(output_path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not output_path or not os.path.exists(output_path):
        return None
    data = _read_json_file(output_path)
    if not isinstance(data, dict):
        return None
    summary = data.get("completion_summary")
    if isinstance(summary, dict):
        return summary
    return None


_GLOBAL_REQUIREMENTS_CACHE: Optional[List[Dict[str, str]]] = None
_GLOBAL_REQUIREMENTS_TITLES: Optional[List[str]] = None


def _load_global_requirements() -> List[Dict[str, str]]:
    global _GLOBAL_REQUIREMENTS_CACHE
    if _GLOBAL_REQUIREMENTS_CACHE is not None:
        return _GLOBAL_REQUIREMENTS_CACHE
    items: List[Dict[str, str]] = []
    try:
        from project_solver import GLOBAL_REQUIREMENTS
    except Exception:
        _GLOBAL_REQUIREMENTS_CACHE = items
        return items
    if isinstance(GLOBAL_REQUIREMENTS, list):
        for entry in GLOBAL_REQUIREMENTS:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            desc = str(entry.get("description") or "").strip()
            if not title and not desc:
                continue
            items.append({"title": title, "description": desc})
    _GLOBAL_REQUIREMENTS_CACHE = items
    return items


def _global_requirements_titles() -> List[str]:
    global _GLOBAL_REQUIREMENTS_TITLES
    if _GLOBAL_REQUIREMENTS_TITLES is not None:
        return _GLOBAL_REQUIREMENTS_TITLES
    titles: List[str] = []
    for entry in _load_global_requirements():
        title = str(entry.get("title") or "").strip()
        if title:
            titles.append(title.lower())
    titles = sorted(set(titles), key=len, reverse=True)
    _GLOBAL_REQUIREMENTS_TITLES = titles
    return titles


def _global_requirements_count() -> int:
    return len(_load_global_requirements())


def _normalise_requirement_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _global_requirements_summary_items(max_items: int = 0) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for idx, entry in enumerate(_load_global_requirements(), start=1):
        title = entry.get("title") or ""
        desc = entry.get("description") or ""
        if title and desc and title.lower() in desc.lower():
            text = desc
        elif title and desc:
            text = f"{title}. {desc}"
        else:
            text = title or desc
        text = _normalize_summary_text(text, max_len=280) if text else ""
        if not text:
            continue
        items.append({"id": f"GLOBAL-{idx:02d}", "text": text, "source": ["global"]})
        if max_items and len(items) >= max_items:
            break
    return items


def _append_global_requirements_summary(summary: Dict[str, Any], redact: bool = False) -> Dict[str, Any]:
    items = summary.get("items")
    if not isinstance(items, list):
        items = []
    existing = {_normalise_requirement_text(item.get("text")) for item in items if isinstance(item, dict)}
    redacted_any = False
    for item in _global_requirements_summary_items():
        norm = _normalise_requirement_text(item.get("text"))
        if not norm or norm in existing:
            continue
        if redact:
            redacted = dict(item)
            redacted["text"] = "[redacted]"
            items.append(redacted)
            redacted_any = True
        else:
            items.append(item)
        existing.add(norm)
    summary["items"] = items
    summary["total"] = len(items)
    source = summary.get("source") or "requirements_file"
    if "global" not in str(source):
        summary["source"] = f"{source}+global"
    if redact and redacted_any:
        summary["redacted"] = True
    return summary


def _is_admin_user(user: Optional[str]) -> bool:
    return bool(user) and user_store.get_role(user) == "admin"


def _is_global_summary_item(item: Dict[str, Any]) -> bool:
    req_id = str(item.get("id") or "").strip().upper()
    if req_id.startswith("GLOBAL-"):
        return True
    source = item.get("source")
    if isinstance(source, list):
        for entry in source:
            if "global" in str(entry).lower():
                return True
    elif isinstance(source, str) and "global" in source.lower():
        return True
    return False


def _redact_global_requirements_summary(summary: Dict[str, Any], is_admin: bool) -> Dict[str, Any]:
    if is_admin or not isinstance(summary, dict):
        return summary
    items = summary.get("items")
    if not isinstance(items, list) or not items:
        return summary
    redacted_any = False
    redacted_items: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        redacted = dict(item)
        if _is_global_summary_item(item):
            redacted["text"] = "[redacted]"
            redacted_any = True
        redacted.pop("source", None)
        redacted_items.append(redacted)
    summary["items"] = redacted_items
    summary["total"] = len(redacted_items)
    if redacted_any:
        summary["redacted"] = True
    return summary


def _redact_requirement_titles_in_line(line: str) -> str:
    if "REQ-" not in line or ":" not in line:
        return line
    titles = _global_requirements_titles()
    if not titles:
        return line
    parts = line.split("; ")
    redacted_parts: List[str] = []
    for part in parts:
        match = re.match(r"(REQ-\d+\s*):\s*(.+)", part.strip(), re.IGNORECASE)
        if not match:
            redacted_parts.append(part)
            continue
        prefix = match.group(1)
        title = match.group(2).strip().lower()
        if any(t in title for t in titles):
            redacted_parts.append(f"{prefix} [redacted]")
        else:
            redacted_parts.append(part)
    return "; ".join(redacted_parts)


def _redact_requirements_table_row(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("|") or "global" not in stripped.lower():
        return line
    parts = [p.strip() for p in stripped.strip("|").split("|")]
    if len(parts) < 2:
        return line
    req_id = parts[0]
    if not re.match(r"REQ-\d+", req_id, re.IGNORECASE):
        return line
    source = parts[-1] or "global"
    redacted_parts = [req_id] + ["[redacted]" for _ in parts[1:-1]] + [source]
    return "| " + " | ".join(redacted_parts) + " |"


def _redact_global_requirement_titles_in_line(line: str) -> str:
    titles = _global_requirements_titles()
    if not titles:
        return line
    lowered = line.lower()
    updated = line
    for title in titles:
        if title and title in lowered:
            updated = re.sub(re.escape(title), "[redacted]", updated, flags=re.IGNORECASE)
            lowered = updated.lower()
    return updated


def _redact_global_requirement_line(line: str, is_admin: bool) -> str:
    if is_admin or not line:
        return line
    redacted = _redact_requirements_table_row(line)
    redacted = _redact_requirement_titles_in_line(redacted)
    redacted = _redact_global_requirement_titles_in_line(redacted)
    return redacted


def _redact_log_entries(entries: List[Dict[str, Any]], is_admin: bool) -> List[Dict[str, Any]]:
    if is_admin:
        return entries
    cleaned: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        line = entry.get("line") or ""
        redacted_line = _redact_global_requirement_line(line, is_admin)
        if redacted_line == line:
            cleaned.append(entry)
            continue
        updated = dict(entry)
        updated["line"] = redacted_line
        cleaned.append(updated)
    return cleaned


UK_WORD_MAP = {
    "color": "colour",
    "colors": "colours",
    "colorful": "colourful",
    "favorite": "favourite",
    "favorites": "favourites",
    "behavior": "behaviour",
    "behaviors": "behaviours",
    "center": "centre",
    "centers": "centres",
    "organize": "organise",
    "organizes": "organises",
    "organizing": "organising",
    "organized": "organised",
    "organization": "organisation",
    "organizations": "organisations",
    "math": "maths",
    "canceled": "cancelled",
    "canceling": "cancelling",
}


def _match_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _to_uk_english(text: str) -> str:
    if not text:
        return text
    result = text
    for us, uk in UK_WORD_MAP.items():
        pattern = r"\b" + re.escape(us) + r"\b"
        result = re.sub(pattern, lambda m: _match_case(m.group(0), uk), result, flags=re.IGNORECASE)
    return result


def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    cleaned = dict(payload)
    if "job_secrets" in cleaned:
        secrets = cleaned.get("job_secrets")
        if isinstance(secrets, list):
            masked_list = []
            for entry in secrets:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if name:
                    masked_list.append({"name": name, "value": "***"})
            cleaned["job_secrets"] = masked_list
        elif isinstance(secrets, dict):
            cleaned["job_secrets"] = {str(k): "***" for k in secrets.keys()}
        else:
            cleaned["job_secrets"] = "***"
    if "requirements_text" in cleaned:
        cleaned["requirements_text"] = "[redacted]"
    return cleaned




def _read_log_tail(path: str, count: int) -> List[Dict[str, Any]]:
    try:
        if count <= 0:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
        else:
            lines = deque(maxlen=count)
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    lines.append(line)
        return [{"ts": "--", "line": line.rstrip("\n"), "stream": "logfile"} for line in list(lines)]
    except Exception:
        return []


def _smtp_config() -> Optional[Dict[str, Any]]:
    host = (os.getenv("REFINER_SMTP_HOST") or "").strip()
    if not host:
        return None
    port = int(os.getenv("REFINER_SMTP_PORT", "587"))
    user = (os.getenv("REFINER_SMTP_USER") or "").strip()
    password = os.getenv("REFINER_SMTP_PASS") or ""
    sender = (os.getenv("REFINER_SMTP_FROM") or user or "refiner@localhost").strip()
    use_tls = _env_flag("REFINER_SMTP_TLS", True)
    use_ssl = _env_flag("REFINER_SMTP_SSL", False)
    timeout = int(os.getenv("REFINER_SMTP_TIMEOUT", "20"))
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "sender": sender,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "timeout": timeout,
    }


def _send_email(recipient: str, subject: str, body: str) -> Optional[str]:
    cfg = _smtp_config()
    if not cfg:
        return "SMTP not configured (set REFINER_SMTP_HOST)."
    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=cfg["timeout"])
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=cfg["timeout"])
        with server:
            server.ehlo()
            if cfg["use_tls"] and not cfg["use_ssl"]:
                server.starttls()
                server.ehlo()
            if cfg["user"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
    except Exception as exc:
        return f"Email send failed: {exc}"
    return None


def _job_notification_body(job: "Job") -> str:
    status = job.status
    project = job.project_name or "Untitled"
    workflow = job.workflow
    finished = job.finished_at or _now_iso()
    lines = [
        f"Refiner job update:",
        f"- Job ID: {job.job_id}",
        f"- Project: {project}",
        f"- Workflow: {workflow}",
        f"- Status: {status}",
        f"- Finished: {finished}",
    ]
    if job.exit_code is not None:
        lines.append(f"- Exit Code: {job.exit_code}")
    return "\\n".join(lines)


def _notify_portal_usage(job: "Job") -> None:
    if not PORTAL_WEBHOOK_URL:
        return
    spent = int(job.token_debited or 0)
    if spent <= 0 and not job.token_shortfall:
        return
    summary = token_ledger.get_summary(job.owner)
    payload = {
        "event": "job_tokens_settled",
        "user": job.owner,
        "job_id": job.job_id,
        "workflow": job.workflow,
        "status": job.status,
        "estimate": job.token_estimate,
        "actual": job.token_actual,
        "debited": job.token_debited,
        "shortfall": job.token_shortfall,
        "balance": summary.get("balance"),
        "ts": _now_iso(),
    }
    headers = {"Content-Type": "application/json"}
    if PORTAL_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {PORTAL_WEBHOOK_TOKEN}"
    try:
        requests.post(PORTAL_WEBHOOK_URL, json=payload, headers=headers, timeout=PORTAL_WEBHOOK_TIMEOUT)
    except Exception:
        pass


manager.reconcile_tokens()


def _estimate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)

REQ_LINE_RE = re.compile(r"^\s*(?:[-*+]\s*)?(REQ-\d{3,})\b", re.I)


def _count_req_lines(text: str) -> int:
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if REQ_LINE_RE.match(line))


def _estimate_repo_tokens(root: Optional[str]) -> Dict[str, Any]:
    if not root or not isinstance(root, str):
        return {"tokens": 0, "file_count": 0, "sampled": False}
    root = root.strip()
    if not root or not os.path.isdir(root):
        return {"tokens": 0, "file_count": 0, "sampled": False}
    key = os.path.abspath(root)
    now = time.time()
    with _estimate_repo_cache_lock:
        cached = _estimate_repo_cache.get(key)
        if cached and (now - cached.get("ts", 0)) < ESTIMATE_REPO_TTL_SEC:
            return cached["data"]
    start = time.time()
    total_files = 0
    text_bytes = 0
    sampled = False
    for dirpath, dirs, files in os.walk(key):
        dirs[:] = [d for d in dirs if d not in ESTIMATE_IGNORED_DIRS]
        for filename in files:
            total_files += 1
            if total_files >= ESTIMATE_REPO_MAX_FILES or (time.time() - start) > ESTIMATE_REPO_MAX_SEC:
                sampled = True
                break
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ESTIMATE_TEXT_EXTS:
                continue
            abs_path = os.path.join(dirpath, filename)
            try:
                size = os.path.getsize(abs_path)
            except Exception:
                continue
            if size > ESTIMATE_REPO_MAX_FILE_BYTES:
                continue
            text_bytes += size
        if sampled:
            break
    tokens = int(text_bytes // 4)
    if sampled and tokens:
        tokens = int(tokens * ESTIMATE_REPO_SAMPLE_MULTIPLIER)
    data = {"tokens": tokens, "file_count": total_files, "sampled": sampled}
    with _estimate_repo_cache_lock:
        _estimate_repo_cache[key] = {"ts": now, "data": data}
    return data


def _estimate_job_tokens_raw(payload: Dict[str, Any], *, include_repo: bool = True) -> int:
    workflow = (payload.get("workflow") or "project_solver").strip()
    input_text = ""
    for key in ("requirements_text", "topic_source", "topic_research", "jql", "projects", "space", "extra_args"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            input_text += value.strip() + "\\n"
    req_path = payload.get("requirements_path")
    if isinstance(req_path, str) and req_path.strip() and os.path.exists(req_path):
        input_text += _read_file_limited(req_path, REQUIREMENTS_MAX_BYTES)

    input_tokens = _estimate_tokens_from_text(input_text)
    req_count = _count_req_lines(input_text)
    if req_count:
        input_tokens += req_count * 120

    max_tokens = payload.get("llm_max_tokens")
    try:
        max_tokens = int(max_tokens) if max_tokens is not None else None
    except Exception:
        max_tokens = None

    repo_tokens = 0
    if include_repo and workflow in {"project_solver", "project", "delivery_pipeline"}:
        repo_root = payload.get("project_root") or payload.get("delivery_project_root")
        repo_tokens = _estimate_repo_tokens(repo_root).get("tokens", 0)

    if workflow in {"project_solver", "project"}:
        iterations = payload.get("project_iterations") or 1
        try:
            iterations = int(iterations)
        except Exception:
            iterations = 1
        max_tokens_eff = max_tokens if max_tokens is not None else DEFAULT_LLM_MAX_TOKENS
        calls = 3.2 * max(1, iterations)
        estimate = 900 + input_tokens * 1.8 + max_tokens_eff * calls + repo_tokens * 0.25
        return int(estimate)
    if workflow == "topic_research":
        max_iters = payload.get("max_iterations") or 3
        try:
            max_iters = int(max_iters)
        except Exception:
            max_iters = 3
        max_tokens_eff = max_tokens if max_tokens is not None else 2000
        calls = 2.2 * max(1, max_iters)
        estimate = 800 + input_tokens * 2.2 + max_tokens_eff * calls
        return int(estimate)
    if workflow in {"jira_analysis", "confluence_analysis"}:
        max_tokens_eff = max_tokens if max_tokens is not None else 1600
        calls = 1.6
        estimate = 700 + input_tokens * 1.2 + max_tokens_eff * calls
        return int(estimate)
    if workflow == "delivery_pipeline":
        max_tokens_eff = max_tokens if max_tokens is not None else 2400
        calls = 2.4
        estimate = 1000 + input_tokens * 1.4 + max_tokens_eff * calls + repo_tokens * 0.2
        return int(estimate)
    max_tokens_eff = max_tokens if max_tokens is not None else 1500
    estimate = 600 + input_tokens * 1.4 + max_tokens_eff * 1.2
    return int(estimate)


def _estimate_calibration() -> Dict[str, Dict[str, float]]:
    if "manager" not in globals():
        return {}
    now = time.time()
    job_count = len(manager.jobs)
    cached = _estimate_calibration_cache.get("data") or {}
    if (
        cached
        and (now - _estimate_calibration_cache.get("ts", 0)) < ESTIMATE_CALIBRATION_TTL_SEC
        and _estimate_calibration_cache.get("job_count") == job_count
    ):
        return cached
    ratios: Dict[str, List[float]] = {}
    for job in manager.list_jobs():
        actual = int(job.token_actual or 0)
        if actual <= 0:
            continue
        payload = job.payload if isinstance(job.payload, dict) else {}
        base = _estimate_job_tokens_raw(payload, include_repo=False)
        if base <= 0:
            continue
        ratio = actual / float(base)
        ratio = min(4.0, max(0.35, ratio))
        ratios.setdefault(job.workflow or "unknown", []).append(ratio)
    data: Dict[str, Dict[str, float]] = {}
    for workflow, values in ratios.items():
        if not values:
            continue
        data[workflow] = {"ratio": float(statistics.median(values))}
    _estimate_calibration_cache.update({"ts": now, "data": data, "job_count": job_count})
    return data


def _estimate_job_tokens(payload: Dict[str, Any]) -> int:
    workflow = (payload.get("workflow") or "project_solver").strip()
    estimate = _estimate_job_tokens_raw(payload, include_repo=True)
    calibration = _estimate_calibration()
    if workflow in calibration:
        ratio = calibration[workflow].get("ratio")
        if ratio:
            estimate = int(estimate * ratio)
    return max(300, int(estimate))


REQ_HEADER_ALIASES = {
    "id": {"id", "req", "req id", "requirement id", "requirement", "requirement_id", "req_id"},
    "title": {"title", "summary", "name", "requirement title"},
    "description": {"description", "details", "detail", "notes", "desc", "statement"},
}


def _normalize_header_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[\s_-]+", " ", text)
    return text


def _detect_req_header(row: List[Any]) -> Optional[Dict[str, int]]:
    mapping: Dict[str, int] = {}
    for idx, cell in enumerate(row):
        key = _normalize_header_cell(cell)
        if not key:
            continue
        for field, aliases in REQ_HEADER_ALIASES.items():
            if key in aliases and field not in mapping:
                mapping[field] = idx
    return mapping or None


def _extract_req_id(text: str) -> Tuple[Optional[str], str]:
    if not text:
        return None, ""
    match = re.search(r"\bREQ-\d{3,}\b", text, re.I)
    if not match:
        return None, text.strip()
    req_id = match.group(0).upper()
    remaining = (text[: match.start()] + text[match.end() :]).strip(" :-–")
    return req_id, remaining or text.strip()


def _coerce_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return str(value).strip()


def _row_to_requirement(row: List[Any], header: Optional[Dict[str, int]]) -> Optional[Dict[str, str]]:
    if not row:
        return None
    values = [_coerce_cell(cell) for cell in row]
    if header:
        req_id = values[header.get("id", -1)] if "id" in header and header.get("id", -1) < len(values) else ""
        title = values[header.get("title", -1)] if "title" in header and header.get("title", -1) < len(values) else ""
        desc = (
            values[header.get("description", -1)]
            if "description" in header and header.get("description", -1) < len(values)
            else ""
        )
    else:
        req_id = values[0] if len(values) > 0 else ""
        title = values[1] if len(values) > 1 else ""
        desc = values[2] if len(values) > 2 else ""
        if len(values) == 2:
            req_id = ""
            title = values[0]
            desc = values[1]
        if len(values) == 1:
            req_id = ""
            title = values[0]
            desc = ""

    req_id = req_id.strip().upper()
    title = title.strip()
    desc = desc.strip()
    if not req_id and title:
        extracted, remaining = _extract_req_id(title)
        if extracted:
            req_id = extracted
            title = remaining
    if not req_id and desc:
        extracted, remaining = _extract_req_id(desc)
        if extracted:
            req_id = extracted
            desc = remaining
    if not title and desc:
        title = desc
        desc = ""
    if not any((req_id, title, desc)):
        return None
    return {"id": req_id, "title": title, "description": desc}


def _rows_to_requirements(rows: List[List[Any]]) -> List[Dict[str, str]]:
    if not rows:
        return []
    header = _detect_req_header(rows[0])
    start_idx = 1 if header else 0
    items: List[Dict[str, str]] = []
    for row in rows[start_idx:]:
        if not row or not any(_coerce_cell(cell) for cell in row):
            continue
        item = _row_to_requirement(row, header)
        if item:
            items.append(item)
    return items


def _parse_csv_rows(data: bytes) -> List[List[str]]:
    try:
        text = data.decode("utf-8-sig")
    except Exception:
        text = data.decode("latin-1", errors="ignore")
    reader = csv.reader(io.StringIO(text))
    return [row for row in reader if row and any(cell.strip() for cell in row)]


def _parse_xlsx_rows(data: bytes) -> List[List[Any]]:
    try:
        from openpyxl import load_workbook  # type: ignore
    except Exception as exc:
        raise RuntimeError("openpyxl is required to import .xlsx files") from exc
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows: List[List[Any]] = []
    for row in ws.iter_rows(values_only=True):
        rows.append(list(row))
    return rows


def _parse_xls_rows(data: bytes) -> List[List[Any]]:
    try:
        import xlrd  # type: ignore
    except Exception as exc:
        raise RuntimeError("xlrd is required to import .xls files") from exc
    book = xlrd.open_workbook(file_contents=data)
    sheet = book.sheet_by_index(0)
    rows: List[List[Any]] = []
    for idx in range(sheet.nrows):
        rows.append(sheet.row_values(idx))
    return rows


def _parse_ods_rows(data: bytes) -> List[List[Any]]:
    try:
        from odf.opendocument import load  # type: ignore
        from odf.table import Table, TableRow, TableCell  # type: ignore
        from odf.text import P  # type: ignore
    except Exception as exc:
        raise RuntimeError("odfpy is required to import .ods files") from exc
    with tempfile.NamedTemporaryFile(suffix=".ods", delete=False) as handle:
        handle.write(data)
        temp_path = handle.name
    try:
        doc = load(temp_path)
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass
    tables = doc.spreadsheet.getElementsByType(Table)
    if not tables:
        return []
    table = tables[0]
    rows: List[List[Any]] = []
    for row in table.getElementsByType(TableRow):
        values: List[Any] = []
        for cell in row.getElementsByType(TableCell):
            repeat = cell.getAttribute("numbercolumnsrepeated")
            try:
                repeat_count = int(repeat) if repeat else 1
            except Exception:
                repeat_count = 1
            text_parts: List[str] = []
            for p in cell.getElementsByType(P):
                if p.firstChild:
                    text_parts.append(str(p.firstChild.data))
            value = " ".join(text_parts).strip()
            if not value:
                string_val = cell.getAttribute("stringvalue")
                if string_val:
                    value = str(string_val)
                else:
                    raw_val = cell.getAttribute("value")
                    if raw_val is not None:
                        value = str(raw_val)
            for _ in range(repeat_count):
                values.append(value)
        rows.append(values)
    return rows


def _export_csv(items: List[Dict[str, str]]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Title", "Description"])
    for item in items:
        writer.writerow([item.get("id", ""), item.get("title", ""), item.get("description", "")])
    return output.getvalue().encode("utf-8")


def _export_xlsx(items: List[Dict[str, str]]) -> bytes:
    try:
        from openpyxl import Workbook  # type: ignore
    except Exception as exc:
        raise RuntimeError("openpyxl is required to export .xlsx files") from exc
    wb = Workbook()
    ws = wb.active
    ws.title = "Requirements"
    ws.append(["ID", "Title", "Description"])
    for item in items:
        ws.append([item.get("id", ""), item.get("title", ""), item.get("description", "")])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _export_xls(items: List[Dict[str, str]]) -> bytes:
    try:
        import xlwt  # type: ignore
    except Exception as exc:
        raise RuntimeError("xlwt is required to export .xls files") from exc
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Requirements")
    headers = ["ID", "Title", "Description"]
    for col, name in enumerate(headers):
        ws.write(0, col, name)
    for row_idx, item in enumerate(items, start=1):
        ws.write(row_idx, 0, item.get("id", ""))
        ws.write(row_idx, 1, item.get("title", ""))
        ws.write(row_idx, 2, item.get("description", ""))
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _export_ods(items: List[Dict[str, str]]) -> bytes:
    try:
        from odf.opendocument import OpenDocumentSpreadsheet  # type: ignore
        from odf.table import Table, TableRow, TableCell  # type: ignore
        from odf.text import P  # type: ignore
    except Exception as exc:
        raise RuntimeError("odfpy is required to export .ods files") from exc
    doc = OpenDocumentSpreadsheet()
    table = Table(name="Requirements")
    doc.spreadsheet.addElement(table)
    for row in [["ID", "Title", "Description"]]:
        row_el = TableRow()
        for value in row:
            cell = TableCell(valuetype="string")
            cell.addElement(P(text=str(value)))
            row_el.addElement(cell)
        table.addElement(row_el)
    for item in items:
        row_el = TableRow()
        for value in [item.get("id", ""), item.get("title", ""), item.get("description", "")]:
            cell = TableCell(valuetype="string")
            cell.addElement(P(text=str(value)))
            row_el.addElement(cell)
        table.addElement(row_el)
    with tempfile.NamedTemporaryFile(suffix=".ods", delete=False) as handle:
        temp_path = handle.name
    output_path = temp_path
    try:
        doc.save(temp_path)
        if not os.path.exists(output_path) and os.path.exists(f"{temp_path}.ods"):
            output_path = f"{temp_path}.ods"
        with open(output_path, "rb") as handle:
            return handle.read()
    finally:
        for path in {temp_path, output_path}:
            try:
                os.remove(path)
            except Exception:
                pass


def _normalize_requirements_items(items: Any) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    if not isinstance(items, list):
        return normalized
    for item in items:
        if not isinstance(item, dict):
            continue
        req_id = str(item.get("id") or "").strip().upper()
        title = str(item.get("title") or "").strip()
        desc = str(item.get("description") or "").strip()
        if not any((req_id, title, desc)):
            continue
        normalized.append({"id": req_id, "title": title, "description": desc})
    return normalized


REFUND_STATUSES = {
    "requested",
    "awaiting approval",
    "approved",
    "settled",
    "rejected",
    "partial-refund",
}


def _find_refund_request(job: "Job", request_id: str) -> Optional[Dict[str, Any]]:
    if not request_id:
        return None
    for refund in job.refunds:
        if refund.get("id") == request_id:
            return refund
    return None


def _latest_refund_request(job: "Job") -> Optional[Dict[str, Any]]:
    if not job.refunds:
        return None
    return max(job.refunds, key=lambda entry: _timestamp_sort_key(entry.get("requested_at")))


def _refund_job_snapshot(job: "Job") -> Dict[str, Any]:
    tokens = job.metrics.get("token_usage") if isinstance(job.metrics, dict) else {}
    return {
        "job_id": job.job_id,
        "project_name": job.project_name,
        "workflow": job.workflow,
        "status": job.status,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "token_estimate": job.token_estimate,
        "token_actual": job.token_actual,
        "token_debited": job.token_debited,
        "token_shortfall": job.token_shortfall,
        "token_usage_total": tokens.get("total") if isinstance(tokens, dict) else None,
    }


def _refund_max_amount(job: "Job") -> int:
    actual = int(job.token_debited or 0)
    if actual <= 0:
        actual = int(job.token_actual or 0)
    return max(0, actual)


def _safe_amount(value: Any) -> Optional[int]:
    try:
        amount = int(float(value))
    except Exception:
        return None
    if amount <= 0:
        return None
    return amount


def _store_refund_files(job_id: str, request_id: str, files: List[Any]) -> List[Dict[str, str]]:
    stored: List[Dict[str, str]] = []
    if not files:
        return stored
    job_dir = os.path.join(JOB_ROOT, job_id)
    refund_dir = os.path.join(job_dir, "refunds", request_id)
    os.makedirs(refund_dir, exist_ok=True)
    for idx, file in enumerate(files, start=1):
        filename = secure_filename(file.filename or "")
        if not filename:
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in REFUND_ALLOWED_EXTS:
            continue
        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        file.stream.seek(0)
        if size > REFUND_SCREENSHOT_MAX_BYTES:
            continue
        if idx > REFUND_MAX_FILES:
            break
        base, ext = os.path.splitext(filename)
        safe_name = filename
        if os.path.exists(os.path.join(refund_dir, safe_name)):
            safe_name = f"{base}_{idx}{ext}"
        abs_path = os.path.join(refund_dir, safe_name)
        file.save(abs_path)
        stored.append(
            {
                "filename": safe_name,
                "path": os.path.join("refunds", request_id, safe_name),
                "uploaded_at": _now_iso(),
            }
        )
    return stored


def _extract_json_payload(text: str) -> Optional[Any]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    start = None
    end = None
    for i, ch in enumerate(cleaned):
        if ch in "{[":
            start = i
            break
    for i in range(len(cleaned) - 1, -1, -1):
        if cleaned[i] in "]}":
            end = i + 1
            break
    if start is not None and end is not None and end > start:
        try:
            return json.loads(cleaned[start:end])
        except Exception:
            return None
    return None


def _current_user() -> Optional[str]:
    return session.get("user")


def _touch_user_activity(user: Optional[str]) -> None:
    if not user:
        return
    with _user_activity_lock:
        _user_activity[user] = time.time()


def _is_user_active(user: Optional[str], window_seconds: Optional[int] = None) -> bool:
    if not user:
        return False
    window = window_seconds if window_seconds is not None else ACTIVE_WINDOW_SEC
    with _user_activity_lock:
        last_seen = _user_activity.get(user)
    if last_seen is None:
        return False
    return (time.time() - last_seen) <= window


def _clear_user_activity(user: Optional[str]) -> None:
    if not user:
        return
    with _user_activity_lock:
        _user_activity.pop(user, None)


def _active_users_snapshot(window_seconds: Optional[int] = None) -> List[Dict[str, object]]:
    window = window_seconds if window_seconds is not None else ACTIVE_WINDOW_SEC
    now = time.time()
    with _user_activity_lock:
        items = list(_user_activity.items())
    active: List[Dict[str, object]] = []
    for user, last_seen in items:
        age = now - last_seen
        if age <= window:
            active.append({"user": user, "last_seen_sec": int(age)})
    active.sort(key=lambda entry: entry.get("last_seen_sec", 0))
    return active


def _token_snapshot(user: str) -> Dict[str, Any]:
    summary = token_ledger.get_summary(user)
    reserved = manager.reserved_tokens(user)
    in_use = manager.in_use_tokens(user)
    balance = int(summary.get("balance") or 0)
    paid_balance = int(summary.get("paid_balance") or balance)
    free_balance = int(summary.get("free_balance") or 0)
    available = max(0, balance - reserved)
    capacity = int(summary.get("last_topup_tokens") or balance or 0)
    display_capacity = max(1, capacity, balance)
    low_threshold = int(round(capacity * 0.2)) if capacity else 0
    status = "low" if capacity and balance <= low_threshold else "ok"
    return {
        "balance": balance,
        "tokens": balance,
        "paid_balance": paid_balance,
        "free_balance": free_balance,
        "available": available,
        "reserved": reserved,
        "in_use": in_use,
        "capacity": capacity,
        "display_capacity": display_capacity,
        "low_threshold": low_threshold,
        "status": status,
        "btc_rate": TOKEN_BTC_RATE,
        "last_topup_at": summary.get("last_topup_at"),
        "updated_at": summary.get("updated_at"),
    }


def _is_secure_request() -> bool:
    if request.is_secure:
        return True
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto:
        proto = forwarded_proto.split(",")[0].strip().lower()
        if proto == "https":
            return True
    return False


def _enforce_https() -> Optional[Response]:
    if not ENFORCE_HTTPS:
        return None
    if _is_secure_request():
        return None
    if request.method == "GET":
        https_url = request.url.replace("http://", "https://", 1)
        return redirect(https_url, code=308)
    return jsonify({"error": "https_required"}), 403


def _allowed_origin() -> Optional[str]:
    origin = request.headers.get("Origin")
    if not origin:
        return None
    normalized = origin.rstrip("/")
    if normalized in CORS_ORIGINS:
        return normalized
    return None


def _add_vary(response: Response, value: str) -> None:
    current = response.headers.get("Vary")
    if not current:
        response.headers["Vary"] = value
        return
    existing = [item.strip() for item in current.split(",") if item.strip()]
    if value not in existing:
        response.headers["Vary"] = ", ".join(existing + [value])


def _apply_cors(response: Response) -> Response:
    origin = _allowed_origin()
    if not origin:
        return response
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = ", ".join(CORS_ALLOW_HEADERS)
    response.headers["Access-Control-Allow-Methods"] = ", ".join(CORS_ALLOW_METHODS)
    response.headers["Access-Control-Max-Age"] = str(CORS_MAX_AGE)
    _add_vary(response, "Origin")
    _add_vary(response, "Access-Control-Request-Method")
    _add_vary(response, "Access-Control-Request-Headers")
    if ENFORCE_HTTPS and _is_secure_request():
        response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    return response


def _metrics_path_label() -> str:
    if request.url_rule and request.url_rule.rule:
        return request.url_rule.rule
    path = request.path or ""
    if path.startswith("/static/"):
        return "/static/*"
    if path.startswith("/public/"):
        return "/public/*"
    return path or "unknown"


def _require_login() -> Optional[Response]:
    path = request.path or ""
    if path.startswith("/static/") or path.startswith("/public/") or path.startswith("/favicon"):
        return None
    if path == METRICS_PATH:
        return None
    if path in {
        "/login",
        "/sso",
        "/setup",
        "/api/login",
        "/api/logout",
        "/api/session",
        "/api/setup",
    } or path.startswith("/api/health"):
        return None
    is_api = path.startswith("/api/")
    if not user_store.has_users():
        if is_api:
            return jsonify({"error": "unauthorized"}), 401
        if path != "/setup":
            return redirect(url_for("setup"))
        return None
    if not _current_user():
        if is_api:
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login"))
    return None


@app.before_request
def _before_request() -> Optional[Response]:
    if METRICS_ENABLED:
        g.metrics_start = time.time()
        INFLIGHT.inc()
    https_block = _enforce_https()
    if https_block:
        return https_block
    if request.method == "OPTIONS":
        return Response(status=204)
    auth_block = _require_login()
    if auth_block:
        return auth_block
    _touch_user_activity(_current_user())
    return None


@app.after_request
def _after_request(response: Response) -> Response:
    if METRICS_ENABLED:
        try:
            path_label = _metrics_path_label()
            elapsed = time.time() - getattr(g, "metrics_start", time.time())
            REQUEST_LATENCY.labels(request.method, path_label).observe(elapsed)
            REQUEST_COUNT.labels(request.method, path_label, response.status_code).inc()
        finally:
            INFLIGHT.dec()
    return _apply_cors(response)


@app.route("/")
def index() -> str:
    user = _current_user()
    return render_template(
        "index.html",
        current_user=user,
        user_role=user_store.get_role(user) if user else None,
        api_base=API_BASE,
        site_base=SITE_BASE,
    )


@app.route("/playground")
def playground() -> str:
    user = _current_user()
    return render_template(
        "playground.html",
        current_user=user,
        user_role=user_store.get_role(user) if user else None,
        api_base=API_BASE,
        site_base=SITE_BASE,
    )


@app.route("/admin")
def admin_dashboard() -> Response:
    user = _current_user()
    role = user_store.get_role(user) if user else None
    if role != "admin":
        return Response("forbidden", status=403)
    return render_template(
        "admin.html",
        current_user=user,
        user_role=role,
        api_base=API_BASE,
        site_base=SITE_BASE,
        active_window_sec=ACTIVE_WINDOW_SEC,
    )


@app.route("/public/<path:filename>")
def public_asset(filename: str) -> Response:
    return send_from_directory(PUBLIC_DIR, filename)


@app.route("/favicon.ico")
def favicon() -> Response:
    return send_from_directory(PUBLIC_DIR, "favicon.ico")


@app.route(METRICS_PATH)
def metrics() -> Response:
    if not METRICS_ENABLED:
        return jsonify({"error": "metrics_disabled"}), 404

    try:
        WORKER_COUNT.set(len(manager.workers))
        JOB_QUEUE_DEPTH.set(manager.queue.qsize())
        UPTIME.set(time.time() - APP_START_TIME)

        status_counts: Dict[str, int] = {}
        with manager.lock:
            jobs_snapshot = list(manager.jobs.values())
        for job in jobs_snapshot:
            status_counts[job.status] = status_counts.get(job.status, 0) + 1

        for status in ("queued", "running", "paused", "stopped", "completed", "failed"):
            JOBS_BY_STATUS.labels(status=status).set(status_counts.get(status, 0))
    except Exception:
        pass

    payload = generate_latest()
    return Response(payload, mimetype=CONTENT_TYPE_LATEST)


@app.route("/login", methods=["GET", "POST"])
def login() -> Response:
    if not user_store.has_users():
        return redirect(url_for("setup"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if user_store.verify(username, password):
            session["user"] = username
            return redirect(url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error, api_base=API_BASE, site_base=SITE_BASE)


@app.route("/sso")
def sso_login() -> Response:
    token = (request.args.get("token") or "").strip()
    next_path = _safe_next_path(request.args.get("next"))
    user = _consume_sso_token(token)
    if not user:
        return redirect(url_for("login"))
    session["user"] = user
    return redirect(next_path)


@app.route("/logout")
def logout() -> Response:
    user = _current_user()
    _clear_user_activity(user)
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/api/login", methods=["POST"])
def api_login() -> Response:
    if not user_store.has_users():
        return jsonify({"error": "setup_required"}), 400
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict):
        payload = {}
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "username_and_password_required"}), 400
    if not user_store.verify(username, password):
        return jsonify({"error": "invalid_credentials"}), 401
    session["user"] = username
    sso_token = _issue_sso_token(username)
    return jsonify(
        {
            "status": "ok",
            "user": username,
            "role": user_store.get_role(username),
            "sso_token": sso_token,
            "sso_expires_in": SSO_TTL_SECONDS,
        }
    )


@app.route("/api/setup", methods=["POST"])
def api_setup() -> Response:
    if user_store.has_users():
        return jsonify({"error": "setup_not_allowed"}), 409
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict):
        payload = {}
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    confirm = (payload.get("confirm") or "").strip()
    email = (payload.get("email") or "").strip()
    if not username or not password:
        return jsonify({"error": "username_and_password_required"}), 400
    if not USERNAME_RE.match(username):
        return jsonify(
            {
                "error": "invalid_username",
                "details": "Username must be 3-32 chars (letters, numbers, underscore, dash).",
            }
        ), 400
    if len(password) < 8:
        return jsonify({"error": "password_too_short", "details": "Password must be at least 8 characters."}), 400
    if confirm and password != confirm:
        return jsonify({"error": "password_mismatch", "details": "Passwords do not match."}), 400
    if email and not EMAIL_RE.match(email):
        return jsonify({"error": "invalid_email", "details": "Enter a valid email address."}), 400
    user_store.create_user(username, password, role="admin", email=email or None)
    session["user"] = username
    sso_token = _issue_sso_token(username)
    return (
        jsonify(
            {
                "status": "ok",
                "user": username,
                "role": user_store.get_role(username),
                "sso_token": sso_token,
                "sso_expires_in": SSO_TTL_SECONDS,
            }
        ),
        201,
    )


@app.route("/api/sso/issue", methods=["POST"])
def api_sso_issue() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    sso_token = _issue_sso_token(user)
    return jsonify(
        {
            "status": "ok",
            "token": sso_token,
            "expires_in": SSO_TTL_SECONDS,
            "user": user,
            "role": user_store.get_role(user),
        }
    )


@app.route("/api/logout", methods=["POST"])
def api_logout() -> Response:
    user = _current_user()
    _clear_user_activity(user)
    session.pop("user", None)
    return jsonify({"status": "ok"})


@app.route("/api/session")
def api_session() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"authenticated": False, "user": None}), 200
    return jsonify({"authenticated": True, "user": user, "role": user_store.get_role(user)})


@app.route("/api/profile", methods=["GET", "POST"])
def api_profile() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "GET":
        return jsonify(
            {
                "user": user,
                "role": user_store.get_role(user),
                "email": user_store.get_email(user),
            }
        )
    payload = request.get_json(force=True, silent=True) or {}
    email = str(payload.get("email") or "").strip()
    if email and not EMAIL_RE.match(email):
        return jsonify({"error": "invalid_email", "details": "Enter a valid email address."}), 400
    user_store.set_email(user, email or None)
    return jsonify({"status": "ok", "email": user_store.get_email(user)})


@app.route("/setup", methods=["GET", "POST"])
def setup() -> Response:
    if user_store.has_users():
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        confirm = (request.form.get("confirm") or "").strip()
        email = (request.form.get("email") or "").strip()
        if not USERNAME_RE.match(username):
            error = "Username must be 3-32 chars (letters, numbers, underscore, dash)."
        elif email and not EMAIL_RE.match(email):
            error = "Please enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            user_store.create_user(username, password, role="admin", email=email or None)
            session["user"] = username
            return redirect(url_for("index"))
    return render_template("setup.html", error=error, api_base=API_BASE, site_base=SITE_BASE)


@app.route("/api/health")
def health() -> Response:
    return jsonify(
        {
            "status": "ok",
            "jobs": len(manager.jobs),
            "workers": len(manager.workers),
            "sso": _sso_store_health(),
        }
    )


@app.route("/api/capabilities")
def capabilities_report() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    refresh = str(request.args.get("refresh") or "").strip().lower()
    force_refresh = refresh in {"1", "true", "yes", "y"}
    report = get_capabilities(force_refresh=force_refresh)
    return jsonify(report)


@app.route("/api/admin/stats")
def admin_stats() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    with user_store.lock:
        total_users = len(user_store.users)
    jobs_snapshot = manager.list_jobs()
    jobs_by_status: Dict[str, int] = {}
    for job in jobs_snapshot:
        status = job.status or "unknown"
        jobs_by_status[status] = jobs_by_status.get(status, 0) + 1
    active_users = _active_users_snapshot()
    return jsonify(
        {
            "active_users": active_users,
            "active_users_count": len(active_users),
            "active_window_sec": ACTIVE_WINDOW_SEC,
            "total_users": total_users,
            "workers": len(manager.workers),
            "jobs_total": len(jobs_snapshot),
            "jobs_running": jobs_by_status.get("running", 0),
            "jobs_queued": jobs_by_status.get("queued", 0),
            "jobs_failed": jobs_by_status.get("failed", 0),
            "jobs_completed": jobs_by_status.get("completed", 0),
            "jobs_paused": jobs_by_status.get("paused", 0),
            "jobs_by_status": jobs_by_status,
            "uptime_sec": int(time.time() - APP_START_TIME),
        }
    )


@app.route("/api/jobs/estimate", methods=["POST"])
def job_estimate() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    estimate = _estimate_job_tokens(payload)
    snapshot = _token_snapshot(user)
    return jsonify(
        {
            "estimate": estimate,
            "available": snapshot["available"],
            "balance": snapshot["balance"],
            "reserved": snapshot["reserved"],
            "in_use": snapshot["in_use"],
            "capacity": snapshot["capacity"],
            "display_capacity": snapshot["display_capacity"],
            "low_threshold": snapshot["low_threshold"],
            "status": snapshot["status"],
        }
    )


@app.route("/api/requirements/import", methods=["POST"])
def import_requirements() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "file_required"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in {".csv", ".xls", ".xlsx", ".ods"}:
        return jsonify({"error": "unsupported_format"}), 400
    data = file.read()
    if len(data) > REQUIREMENTS_IMPORT_MAX_BYTES:
        return jsonify({"error": "file_too_large"}), 413
    try:
        if ext == ".csv":
            rows = _parse_csv_rows(data)
        elif ext == ".xlsx":
            rows = _parse_xlsx_rows(data)
        elif ext == ".xls":
            rows = _parse_xls_rows(data)
        else:
            rows = _parse_ods_rows(data)
    except Exception as exc:
        return jsonify({"error": "import_failed", "details": str(exc)}), 400
    items = _rows_to_requirements(rows)
    return jsonify({"items": items, "count": len(items)})


@app.route("/api/requirements/export", methods=["POST"])
def export_requirements() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    fmt = str(payload.get("format") or "csv").lower()
    if fmt not in {"csv", "xls", "xlsx", "ods"}:
        return jsonify({"error": "unsupported_format"}), 400
    items = _normalize_requirements_items(payload.get("items"))
    try:
        if fmt == "csv":
            data = _export_csv(items)
            mime = "text/csv"
        elif fmt == "xlsx":
            data = _export_xlsx(items)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "xls":
            data = _export_xls(items)
            mime = "application/vnd.ms-excel"
        else:
            data = _export_ods(items)
            mime = "application/vnd.oasis.opendocument.spreadsheet"
    except Exception as exc:
        return jsonify({"error": "export_failed", "details": str(exc)}), 400
    filename = f"requirements_register.{fmt}"
    response = Response(data, mimetype=mime)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@app.route("/api/rag/indexes", methods=["GET"])
def rag_indexes() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    indexes = rag_store.list_indexes(user)
    return jsonify({"indexes": indexes})


@app.route("/api/rag/index", methods=["POST"])
def rag_index_create() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name") or "default").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    chunk_size = _safe_int(payload.get("chunk_size"), RAG_DEFAULT_CHUNK_SIZE) or RAG_DEFAULT_CHUNK_SIZE
    chunk_overlap = _safe_int(payload.get("chunk_overlap"), RAG_DEFAULT_CHUNK_OVERLAP) or RAG_DEFAULT_CHUNK_OVERLAP
    max_chunks = _safe_int(payload.get("max_chunks"), RAG_DEFAULT_MAX_CHUNKS) or RAG_DEFAULT_MAX_CHUNKS
    sources = _coerce_rag_sources(payload)
    if not sources:
        return jsonify({"error": "sources_required"}), 400
    docs = _build_rag_documents(
        sources,
        max_docs=RAG_MAX_DOCS,
        max_doc_bytes=RAG_MAX_DOC_BYTES,
    )
    if not docs:
        return jsonify({"error": "no_documents"}), 400
    index = RagIndex.build(
        name=name,
        documents=docs,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        max_chunks=max_chunks,
    )
    rag_store.save_index(user, index)
    return jsonify(
        {
            "name": name,
            "documents": len(docs),
            "chunks": len(index.chunks),
        }
    )


@app.route("/api/rag/index/<name>", methods=["DELETE"])
def rag_index_delete(name: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if not name:
        return jsonify({"error": "name_required"}), 400
    deleted = rag_store.delete_index(user, name)
    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "deleted", "name": name})


@app.route("/api/rag/query", methods=["POST"])
def rag_query() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name") or "default").strip()
    query = str(payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query_required"}), 400
    index = rag_store.load_index(user, name)
    if not index:
        return jsonify({"error": "index_not_found"}), 404
    top_k = _safe_int(payload.get("top_k"), 5) or 5
    min_score = float(payload.get("min_score") or 0.0)
    matches = index.search(query, limit=top_k, min_score=min_score)
    context = "\n\n".join([f"[{m.source}]\n{m.text}" for m in matches])
    return jsonify(
        {
            "name": name,
            "query": query,
            "matches": [
                {
                    "chunk_id": m.chunk_id,
                    "source": m.source,
                    "score": round(m.score, 4),
                    "text": m.text,
                    "metadata": m.metadata,
                }
                for m in matches
            ],
            "context": context,
        }
    )


@app.route("/api/assistant/rag-mcp", methods=["POST"])
def assistant_rag_mcp() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt_required"}), 400

    skills = select_skills(prompt, limit=4)
    skills_hint = format_skill_brief(skills)
    capabilities_hint = capability_summary(max_items=4)

    rag_cfg = payload.get("rag") if isinstance(payload.get("rag"), dict) else {}
    rag_index_name = str(rag_cfg.get("index") or "").strip()
    rag_matches = []
    rag_context = ""
    if rag_index_name:
        index = rag_store.load_index(user, rag_index_name)
        if not index:
            return jsonify({"error": "rag_index_not_found"}), 404
        top_k = _safe_int(rag_cfg.get("top_k"), 4) or 4
        matches = index.search(prompt, limit=top_k, min_score=0.0)
        rag_matches = [
            {
                "chunk_id": m.chunk_id,
                "source": m.source,
                "score": round(m.score, 4),
                "text": m.text,
                "metadata": m.metadata,
            }
            for m in matches
        ]
        rag_context = "\n\n".join([f"[{m.source}]\n{m.text}" for m in matches])

    mcp_cfg = payload.get("mcp") if isinstance(payload.get("mcp"), dict) else {}
    mcp_result = None
    if mcp_cfg:
        if user_store.get_role(user) != "admin":
            return jsonify({"error": "mcp_forbidden"}), 403
        server_name = str(mcp_cfg.get("server") or "").strip()
        tool_name = str(mcp_cfg.get("tool") or "").strip()
        if not server_name or not tool_name:
            return jsonify({"error": "mcp_server_and_tool_required"}), 400
        server = mcp_store.get_server(user, server_name)
        if not server:
            return jsonify({"error": "mcp_server_not_found"}), 404
        arguments = mcp_cfg.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            return jsonify({"error": "mcp_invalid_arguments"}), 400
        try:
            client = MCPClient(server)
            client.initialize({"name": "refiner"})
            mcp_result = client.call_tool(tool_name, arguments or {})
        except Exception as exc:
            return jsonify({"error": "mcp_request_failed", "details": str(exc)}), 400

    provider_hint = payload.get("provider") or payload.get("llm_provider") or "openai"
    model_hint = payload.get("model") or payload.get("llm_model") or "gpt-5.1"
    settings = _resolve_llm_settings(user=user, provider_hint=provider_hint, model_hint=model_hint)
    try:
        provider = get_provider(
            settings["provider"],
            model=settings.get("model"),
            base_url=settings.get("base_url"),
            api_key=settings.get("api_key"),
            inter_request_gap=0.0,
        )
    except Exception as exc:
        return jsonify({"error": "llm_init_failed", "details": str(exc)}), 400

    system_lines = [
        "You are a practical, concise assistant. Use UK British English spelling.",
        "Use the provided RAG context and MCP data where relevant.",
        "If the context is insufficient, state what is missing.",
        "Prefer RAG for stable unstructured context and MCP for live structured data/actions.",
    ]
    if capabilities_hint:
        system_lines.append("Capabilities summary:")
        system_lines.append(capabilities_hint)
    if skills_hint:
        system_lines.append("Relevant skills:")
        system_lines.append(skills_hint)
    system = "\n".join(system_lines)
    user_blocks = [f"User request:\n{prompt}"]
    if rag_context:
        user_blocks.append(f"RAG context:\n{rag_context}")
    if mcp_result is not None:
        user_blocks.append(f"MCP result:\n{json.dumps(mcp_result, ensure_ascii=True)}")
    user_text = "\n\n".join(user_blocks)

    try:
        response = provider.predict(
            messages=[{"role": "user", "content": user_text}],
            system=system,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens", 1200),
            reasoning_effort=payload.get("reasoning_effort"),
        )
    except Exception as exc:
        return jsonify({"error": "llm_request_failed", "details": str(exc)}), 400

    return jsonify(
        {
            "answer": response.text,
            "rag_matches": rag_matches,
            "mcp_result": mcp_result,
        }
    )


@app.route("/api/mcp/servers", methods=["GET", "POST"])
def mcp_servers() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    if request.method == "GET":
        servers = [server.masked() for server in mcp_store.list_servers(user)]
        return jsonify({"servers": servers})
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    if not name or not base_url:
        return jsonify({"error": "name_and_base_url_required"}), 400
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return jsonify({"error": "invalid_base_url"}), 400
    auth_type = str(payload.get("auth_type") or "bearer").strip().lower()
    if auth_type not in {"bearer", "oauth", "none"}:
        return jsonify({"error": "invalid_auth_type"}), 400
    auth_token = payload.get("auth_token") if auth_type != "none" else None
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else None
    timeout = _safe_int(payload.get("timeout"), 20) or 20
    config = MCPServerConfig(
        name=name,
        base_url=base_url,
        auth_type=auth_type,
        auth_token=auth_token,
        headers=headers,
        timeout=timeout,
    )
    mcp_store.save_server(user, config)
    return jsonify({"server": config.masked()})


@app.route("/api/mcp/servers/<name>", methods=["DELETE"])
def mcp_server_delete(name: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    deleted = mcp_store.delete_server(user, name)
    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "deleted", "name": name})


@app.route("/api/mcp/servers/<name>/tools", methods=["GET"])
def mcp_server_tools(name: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    server = mcp_store.get_server(user, name)
    if not server:
        return jsonify({"error": "not_found"}), 404
    try:
        client = MCPClient(server)
        client.initialize({"name": "refiner"})
        tools = client.list_tools()
        return jsonify({"tools": tools})
    except Exception as exc:
        return jsonify({"error": "mcp_request_failed", "details": str(exc)}), 400


@app.route("/api/mcp/servers/<name>/call", methods=["POST"])
def mcp_server_call(name: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    server = mcp_store.get_server(user, name)
    if not server:
        return jsonify({"error": "not_found"}), 404
    payload = request.get_json(force=True, silent=True) or {}
    tool = str(payload.get("tool") or "").strip()
    if not tool:
        return jsonify({"error": "tool_required"}), 400
    arguments = payload.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        return jsonify({"error": "invalid_arguments"}), 400
    try:
        client = MCPClient(server)
        client.initialize({"name": "refiner"})
        result = client.call_tool(tool, arguments or {})
        return jsonify({"result": result})
    except Exception as exc:
        return jsonify({"error": "mcp_request_failed", "details": str(exc)}), 400


@app.route("/api/mcp/servers/<name>/resources", methods=["GET"])
def mcp_server_resources(name: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    server = mcp_store.get_server(user, name)
    if not server:
        return jsonify({"error": "not_found"}), 404
    try:
        client = MCPClient(server)
        client.initialize({"name": "refiner"})
        resources = client.list_resources()
        return jsonify({"resources": resources})
    except Exception as exc:
        return jsonify({"error": "mcp_request_failed", "details": str(exc)}), 400


@app.route("/api/mcp/servers/<name>/resource", methods=["POST"])
def mcp_server_resource(name: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    server = mcp_store.get_server(user, name)
    if not server:
        return jsonify({"error": "not_found"}), 404
    payload = request.get_json(force=True, silent=True) or {}
    uri = str(payload.get("uri") or "").strip()
    if not uri:
        return jsonify({"error": "uri_required"}), 400
    try:
        client = MCPClient(server)
        client.initialize({"name": "refiner"})
        resource = client.read_resource(uri)
        return jsonify({"resource": resource})
    except Exception as exc:
        return jsonify({"error": "mcp_request_failed", "details": str(exc)}), 400


@app.route("/api/jobs/<job_id>/refunds", methods=["POST"])
def request_refund(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    latest = _latest_refund_request(job)
    if latest and latest.get("status") not in {"rejected", "settled", "partial-refund"}:
        return jsonify({"error": "refund_already_open"}), 409
    amount = _safe_amount(request.form.get("amount"))
    reason = (request.form.get("reason") or "").strip()
    details = (request.form.get("details") or "").strip()
    if amount is None:
        return jsonify({"error": "invalid_amount"}), 400
    if not reason:
        return jsonify({"error": "reason_required"}), 400
    max_refund = _refund_max_amount(job)
    if max_refund and amount > max_refund:
        return jsonify({"error": "amount_exceeds_max", "max_refund": max_refund}), 400
    files = request.files.getlist("screenshots")
    if not files:
        return jsonify({"error": "screenshots_required"}), 400
    if len(files) > REFUND_MAX_FILES:
        return jsonify({"error": "too_many_files"}), 400
    request_id = uuid.uuid4().hex
    stored_files = _store_refund_files(job.job_id, request_id, files)
    if not stored_files:
        return jsonify({"error": "invalid_screenshots"}), 400
    refund = {
        "id": request_id,
        "status": "requested",
        "requested_amount": amount,
        "requested_at": _now_iso(),
        "requested_by": user,
        "reason": reason,
        "details": details,
        "screenshots": stored_files,
        "job_snapshot": _refund_job_snapshot(job),
        "history": [
            {
                "status": "requested",
                "at": _now_iso(),
                "by": user,
                "note": reason,
            }
        ],
    }
    job.refunds.append(refund)
    job.updated_at = _now_iso()
    job.persist(force=True)
    return jsonify({"refund": refund})


@app.route("/api/refunds")
def list_refunds() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    requests: List[Dict[str, Any]] = []
    for job in manager.list_jobs():
        for refund in job.refunds:
            requests.append(
                {
                    "job_id": job.job_id,
                    "project_name": job.project_name,
                    "owner": job.owner,
                    "workflow": job.workflow,
                    "job_status": job.status,
                    "tokens": {
                        "estimate": job.token_estimate,
                        "actual": job.token_actual,
                        "debited": job.token_debited,
                        "shortfall": job.token_shortfall,
                    },
                    "refund": refund,
                }
            )
    requests.sort(
        key=lambda item: _timestamp_sort_key(item.get("refund", {}).get("requested_at")),
        reverse=True,
    )
    return jsonify({"requests": requests})


@app.route("/api/refunds/<job_id>/<request_id>/screen", methods=["POST"])
def screen_refund(job_id: str, request_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    refund = _find_refund_request(job, request_id)
    if not refund:
        return jsonify({"error": "refund not found"}), 404
    settings = _resolve_llm_settings(user=user)
    try:
        provider = get_provider(
            settings["provider"],
            model=settings.get("model"),
            base_url=settings.get("base_url"),
            api_key=settings.get("api_key"),
            inter_request_gap=0.0,
        )
    except Exception as exc:
        return jsonify({"error": "llm_unavailable", "details": str(exc)}), 400
    system = (
        "You are a refund screening assistant. Review the request and recommend a decision. "
        "Return JSON with keys: decision (approve/reject/partial), suggested_amount (integer), "
        "confidence (0-1), rationale."
    )
    job_snapshot = refund.get("job_snapshot") or _refund_job_snapshot(job)
    screenshots = refund.get("screenshots") or []
    prompt = (
        "Refund request:\n"
        f"- Requested amount: {refund.get('requested_amount')}\n"
        f"- Reason: {refund.get('reason')}\n"
        f"- Details: {refund.get('details')}\n"
        f"- Screenshots: {len(screenshots)}\n"
        "\nJob snapshot:\n"
        f"- Workflow: {job_snapshot.get('workflow')}\n"
        f"- Status: {job_snapshot.get('status')}\n"
        f"- Token estimate: {job_snapshot.get('token_estimate')}\n"
        f"- Token actual: {job_snapshot.get('token_actual')}\n"
        f"- Token debited: {job_snapshot.get('token_debited')}\n"
        f"- Token shortfall: {job_snapshot.get('token_shortfall')}\n"
    )
    try:
        response = provider.predict(messages=[{"role": "user", "content": prompt}], system=system)
    except Exception as exc:
        return jsonify({"error": "llm_request_failed", "details": str(exc)}), 400
    suggestion = _extract_json_payload(response.text) or {}
    if not isinstance(suggestion, dict):
        suggestion = {"raw": response.text}
    suggested_amount = _safe_amount(suggestion.get("suggested_amount")) if isinstance(suggestion, dict) else None
    if suggested_amount is not None:
        suggestion["suggested_amount"] = suggested_amount
    suggestion.update(
        {
            "provider": response.provider or settings.get("provider"),
            "model": response.model or settings.get("model"),
            "screened_at": _now_iso(),
        }
    )
    refund["llm_screening"] = suggestion
    job.persist(force=True)
    return jsonify({"suggestion": suggestion})


@app.route("/api/refunds/<job_id>/<request_id>/decision", methods=["POST"])
def decide_refund(job_id: str, request_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if user_store.get_role(user) != "admin":
        return jsonify({"error": "forbidden"}), 403
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    refund = _find_refund_request(job, request_id)
    if not refund:
        return jsonify({"error": "refund not found"}), 404
    payload = request.get_json(force=True, silent=True) or {}
    status = str(payload.get("status") or "").strip().lower()
    amount = _safe_amount(payload.get("amount")) if payload.get("amount") is not None else None
    note = (payload.get("note") or "").strip()
    if status not in REFUND_STATUSES:
        return jsonify({"error": "invalid_status"}), 400
    if status in {"approved", "partial-refund", "settled"} and amount is None:
        return jsonify({"error": "amount_required"}), 400
    max_refund = _refund_max_amount(job)
    if amount is not None and amount > max_refund:
        return jsonify({"error": "amount_exceeds_max", "max_refund": max_refund}), 400
    refund.setdefault("history", []).append(
        {
            "status": status,
            "at": _now_iso(),
            "by": user,
            "note": note,
            "amount": amount,
        }
    )
    refund["status"] = status
    if amount is not None:
        refund["approved_amount"] = amount
    refund["admin_decision"] = {
        "status": status,
        "amount": amount,
        "note": note,
        "admin": user,
        "decided_at": _now_iso(),
    }
    if status in {"settled", "partial-refund"}:
        if refund.get("settled_at"):
            return jsonify({"error": "already_settled"}), 409
        entry = token_ledger.record(
            job.owner,
            "refund",
            int(amount or 0),
            {
                "job_id": job.job_id,
                "refund_id": request_id,
                "decision": status,
                "requested_amount": refund.get("requested_amount"),
                "approved_amount": amount,
                "admin": user,
                "note": note,
            },
        )
        refund["settled_at"] = _now_iso()
        refund["ledger_entry"] = entry
    job.updated_at = _now_iso()
    job.persist(force=True)
    return jsonify({"refund": refund, "max_refund": max_refund})


@app.route("/api/refunds/<job_id>/<request_id>/file/<filename>")
def refund_file(job_id: str, request_id: str, filename: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if user_store.get_role(user) != "admin" and job.owner != user:
        return jsonify({"error": "forbidden"}), 403
    refund = _find_refund_request(job, request_id)
    if not refund:
        return jsonify({"error": "refund not found"}), 404
    allowed = {entry.get("filename") for entry in refund.get("screenshots", []) if entry.get("filename")}
    if filename not in allowed:
        return jsonify({"error": "file not found"}), 404
    refund_dir = os.path.join(JOB_ROOT, job_id, "refunds", request_id)
    return send_from_directory(refund_dir, filename)


@app.route("/api/jobs", methods=["GET", "POST"])
def jobs() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "POST":
        payload = request.get_json(force=True, silent=True) or {}
        payload["owner"] = user
        estimate = _estimate_job_tokens(payload)
        snapshot = _token_snapshot(user)
        if estimate > snapshot["available"]:
            return (
                jsonify(
                    {
                        "error": "insufficient_tokens",
                        "details": "Insufficient tokens to submit this job.",
                        "estimate": estimate,
                        "available": snapshot["available"],
                        "balance": snapshot["balance"],
                    }
                ),
                402,
            )
        job = manager.submit_job(payload, owner=user)
        manager._reserve_tokens(job, estimate)
        return jsonify(job.to_dict())
    status = request.args.get("status")
    jobs_list = [job.to_dict() for job in manager.list_jobs(status=status, owner=user)]
    return jsonify({"jobs": jobs_list})


@app.route("/api/jobs/<job_id>", methods=["GET", "DELETE"])
def job_detail(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    is_admin = _is_admin_user(user)
    if request.method == "DELETE":
        payload = request.get_json(force=True, silent=True) or {}
        stop = bool(payload.get("stop")) or request.args.get("stop") in {"1", "true", "yes"}
        if job.status in {"queued", "running", "paused"} and not stop:
            return jsonify({"error": "job_active", "details": "Stop the job before deleting."}), 409
        deleted = manager.delete_job(job_id, owner=user, stop_if_active=stop)
        if not deleted:
            return jsonify({"error": "delete_failed"}), 409
        return jsonify({"status": "deleted", "job_id": job_id})
    data = job.to_dict(include_logs=True, log_tail=DEFAULT_TAIL)
    data["logs"] = _redact_log_entries(data.get("logs", []), is_admin)
    return jsonify(data)


@app.route("/api/jobs/<job_id>/requirements/progress")
def job_requirements_progress(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if user_store.get_role(user) != "admin" and job.owner != user:
        return jsonify({"error": "forbidden"}), 403

    payload = {
        "total": 0,
        "completed": 0,
        "in_progress": 0,
        "remaining": 0,
        "status": "pending",
        "source": "none",
        "message": "Awaiting requirements output.",
        "updated_at": None,
    }
    req_path = None
    if isinstance(job.payload, dict):
        req_path = job.payload.get("requirements_path")
    include_global = bool(job.payload.get("include_global_requirements")) if isinstance(job.payload, dict) else False
    solution_path = None
    if isinstance(job.output_paths, dict):
        solution_path = job.output_paths.get("primary")
    if not solution_path or not os.path.exists(solution_path):
        fallback = _requirements_progress_from_requirements_file(req_path)
        if fallback:
            payload.update(fallback)
            payload["status"] = "partial"
            payload["message"] = "Using requirements file totals."
            if include_global:
                global_count = _global_requirements_count()
                if global_count:
                    payload["total"] = _safe_int(payload.get("total")) + global_count
                    payload["remaining"] = _safe_int(payload.get("remaining")) + global_count
                    source = payload.get("source") or "requirements_file"
                    if "global" not in str(source):
                        payload["source"] = f"{source}+global"
                    payload["message"] = f"{payload['message']} Includes global requirements."
        return jsonify(payload)

    solution_mtime = _mtime_dt(solution_path)
    payload["updated_at"] = _iso_from_mtime(solution_path)
    job_active = job.status in {"queued", "running", "paused"}
    job_marker = _parse_timestamp(job.started_at) or _parse_timestamp(job.updated_at)
    if job_active and solution_mtime and job_marker and solution_mtime < job_marker:
        fallback = _requirements_progress_from_requirements_file(req_path)
        if fallback:
            payload.update(fallback)
            payload["status"] = "running"
            payload["message"] = "Job is running; using requirements totals until new output is written."
            source = payload.get("source") or "requirements_file"
            if "stale" not in str(source):
                payload["source"] = f"{source}+stale"
        else:
            payload["status"] = "running"
            payload["message"] = "Job is running; requirements progress will update when output is written."
        return jsonify(payload)

    solution = _read_json_file(solution_path)
    if not isinstance(solution, dict):
        payload["status"] = "unreadable"
        payload["message"] = "Unable to read requirements output."
        fallback = _requirements_progress_from_requirements_file(req_path)
        if fallback:
            payload.update(fallback)
            payload["status"] = "partial"
            payload["message"] = "Using requirements file totals."
        return jsonify(payload)

    progress = _requirements_progress_from_solution(solution)
    payload.update(progress)
    if payload["total"] > 0:
        payload["message"] = ""
        payload["status"] = "ready"
        if payload["source"] == "register":
            payload["status"] = "partial"
            payload["message"] = "Traceability not yet generated; using register totals."
    return jsonify(payload)


@app.route("/api/jobs/<job_id>/requirements/summary")
def job_requirements_summary(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    if user_store.get_role(user) != "admin" and job.owner != user:
        return jsonify({"error": "forbidden"}), 403
    is_admin = _is_admin_user(user)

    payload = {
        "summary": "",
        "items": [],
        "total": 0,
        "source": "none",
        "updated_at": None,
        "message": "No requirements summary available.",
        "redacted": False,
    }

    req_path = None
    if isinstance(job.payload, dict):
        req_path = job.payload.get("requirements_path")
    include_global = bool(job.payload.get("include_global_requirements")) if isinstance(job.payload, dict) else False

    solution_path = None
    if isinstance(job.output_paths, dict):
        solution_path = job.output_paths.get("primary")

    if solution_path and os.path.exists(solution_path):
        solution = _read_json_file(solution_path)
        payload["updated_at"] = _iso_from_mtime(solution_path)
        if isinstance(solution, dict):
            summary = _requirements_summary_from_register(solution)
            if summary:
                summary = _redact_global_requirements_summary(summary, is_admin)
                payload.update(summary)
                if summary.get("redacted"):
                    payload["redacted"] = True
                payload["message"] = ""
                if req_path and os.path.exists(req_path):
                    text = _read_file_limited(req_path, 80_000)
                    if text:
                        lead = _requirements_summary_from_text(text).get("summary") or ""
                        if lead:
                            payload["summary"] = lead
                return jsonify(payload)
        payload["message"] = "Requirements summary unavailable in output."

    if req_path and os.path.exists(req_path):
        text = _read_file_limited(req_path, 250_000)
        if text:
            summary = _requirements_summary_from_text(text)
            if include_global:
                summary = _append_global_requirements_summary(summary, redact=not is_admin)
            summary = _redact_global_requirements_summary(summary, is_admin)
            payload.update(summary)
            if summary.get("redacted"):
                payload["redacted"] = True
            payload["updated_at"] = _iso_from_mtime(req_path)
            payload["message"] = "" if summary.get("items") else payload["message"]
            return jsonify(payload)

    return jsonify(payload)


@app.route("/api/jobs/<job_id>/logs")
def job_logs(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    is_admin = _is_admin_user(user)
    tail = request.args.get("tail")
    try:
        tail_count = int(tail) if tail else DEFAULT_TAIL
    except Exception:
        tail_count = DEFAULT_TAIL
    logs = job.get_log_tail(tail_count)
    return jsonify({"logs": _redact_log_entries(logs, is_admin)})


@app.route("/api/jobs/<job_id>/logs/stream")
def job_logs_stream(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    is_admin = _is_admin_user(user)

    def generate():
        q = job.add_listener()
        try:
            while True:
                try:
                    entry = q.get(timeout=1.0)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if not is_admin:
                    redacted_entries = _redact_log_entries([entry], is_admin)
                    if redacted_entries:
                        entry = redacted_entries[0]
                yield _sse(entry)
        finally:
            job.remove_listener(q)

    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.route("/api/jobs/<job_id>/actions", methods=["POST"])
def job_actions(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    payload = request.get_json(force=True, silent=True) or {}
    action = payload.get("action")
    success = False
    if action == "pause":
        success = manager.pause_job(job_id)
    elif action == "resume":
        success = manager.resume_job(job_id)
    elif action == "stop":
        success = manager.stop_job(job_id)
    elif action == "restart":
        estimate = job.token_estimate or _estimate_job_tokens(job.payload)
        snapshot = _token_snapshot(user)
        if estimate > snapshot["available"] and job.token_reserved <= 0:
            return (
                jsonify(
                    {
                        "error": "insufficient_tokens",
                        "details": "Insufficient tokens to restart this job.",
                        "estimate": estimate,
                        "available": snapshot["available"],
                    }
                ),
                402,
            )
        if job.token_reserved <= 0:
            manager._reserve_tokens(job, estimate)
        success = manager.restart_job(job_id)
    else:
        return jsonify({"error": "unknown action"}), 400
    if not success:
        return jsonify({"error": "action failed"}), 409
    return jsonify(job.to_dict())


@app.route("/api/jobs/<job_id>/archive", methods=["POST"])
def job_archive(job_id: str) -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    job = manager.get_job(job_id, owner=user)
    if not job:
        return jsonify({"error": "job not found"}), 404
    payload = request.get_json(force=True, silent=True) or {}
    archived = bool(payload.get("archived", True))
    stop = bool(payload.get("stop"))
    if archived and job.status in {"queued", "running", "paused"} and not stop:
        return jsonify({"error": "job_active", "details": "Stop the job before archiving."}), 409
    if archived and job.status in {"queued", "running", "paused"}:
        manager.stop_job(job_id)
    if not manager.set_archived(job_id, archived):
        return jsonify({"error": "archive_failed"}), 409
    return jsonify(job.to_dict())


@app.route("/api/jobs/bulk-delete", methods=["POST"])
def jobs_bulk_delete() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    scope = (payload.get("scope") or "queue").strip().lower()
    if scope not in {"queue", "archive"}:
        return jsonify({"error": "invalid_scope"}), 400
    stop = bool(payload.get("stop"))
    target_archived = scope == "archive"
    jobs_list = [job for job in manager.list_jobs(owner=user) if bool(job.archived) == target_archived]
    active_jobs = [job for job in jobs_list if job.status in {"queued", "running", "paused"}]
    if active_jobs and not stop:
        return jsonify({"error": "job_active", "details": "Stop active jobs before deleting."}), 409
    deleted: List[str] = []
    for job in jobs_list:
        if manager.delete_job(job.job_id, owner=user, stop_if_active=stop):
            deleted.append(job.job_id)
    return jsonify({"deleted": deleted, "count": len(deleted)})


@app.route("/api/tokens", methods=["GET", "POST"])
def tokens() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "GET":
        snapshot = _token_snapshot(user)
        return jsonify(snapshot)

    payload = request.get_json(force=True, silent=True) or {}
    action = (payload.get("action") or "review").strip().lower()
    username = (payload.get("username") or user).strip()
    if action != "grant" and username and username != user:
        return jsonify({"error": "invalid_user", "details": "Username mismatch."}), 403

    snapshot = _token_snapshot(user)
    if action == "review":
        return jsonify(snapshot)

    if action in {"add", "cashout", "grant"}:
        password = (payload.get("password") or "").strip()
        if not password or not user_store.verify(user, password):
            return jsonify({"error": "invalid_credentials", "details": "Password verification failed."}), 401

    if action == "add":
        tokens_raw = payload.get("token_amount")
        btc_raw = payload.get("btc_amount") or payload.get("btc_value")
        tokens = 0
        btc_value = None
        if tokens_raw not in (None, ""):
            try:
                tokens = int(float(tokens_raw))
            except Exception:
                tokens = 0
        elif btc_raw not in (None, ""):
            try:
                btc_value = float(btc_raw)
                tokens = int(round(btc_value / TOKEN_BTC_RATE))
            except Exception:
                tokens = 0
        if tokens <= 0:
            return jsonify({"error": "invalid_amount", "details": "Token amount must be positive."}), 400
        meta = {
            "tokens": tokens,
            "btc_amount": btc_value,
            "btc_rate": TOKEN_BTC_RATE,
            "btc_txid": payload.get("btc_txid"),
            "btc_address": payload.get("btc_address"),
            "source": payload.get("source") or "portal",
        }
        token_ledger.record(user, "topup", tokens, meta)
        snapshot = _token_snapshot(user)
        return jsonify({"message": "Tokens added.", **snapshot})

    if action == "cashout":
        tokens_raw = payload.get("token_amount")
        try:
            tokens = int(float(tokens_raw))
        except Exception:
            tokens = 0
        if tokens <= 0:
            return jsonify({"error": "invalid_amount", "details": "Token amount must be positive."}), 400
        if tokens > snapshot.get("paid_balance", snapshot["balance"]):
            return jsonify({"error": "insufficient_tokens", "details": "Not enough tokens to cash out."}), 409
        meta = {
            "tokens": tokens,
            "btc_address": payload.get("btc_address"),
            "source": payload.get("source") or "portal",
        }
        token_ledger.record(user, "cashout", -tokens, meta)
        snapshot = _token_snapshot(user)
        return jsonify({"message": "Cashout recorded.", **snapshot})

    if action == "grant":
        if user_store.get_role(user) != "admin":
            return jsonify({"error": "forbidden", "details": "Admin role required."}), 403
        target_user = (payload.get("target_user") or payload.get("recipient") or payload.get("username") or "").strip()
        if not target_user:
            return jsonify({"error": "target_required", "details": "Target user is required."}), 400
        tokens_raw = payload.get("token_amount")
        try:
            tokens = int(float(tokens_raw))
        except Exception:
            tokens = 0
        if tokens <= 0:
            return jsonify({"error": "invalid_amount", "details": "Token amount must be positive."}), 400
        meta = {
            "tokens": tokens,
            "granted_by": user,
            "note": payload.get("note") or payload.get("reason"),
            "source": payload.get("source") or "admin",
        }
        token_ledger.record(target_user, "grant", tokens, meta)
        snapshot = _token_snapshot(target_user)
        return jsonify({"message": "Free tokens granted.", "target": target_user, **snapshot})

    if action == "sync":
        target = payload.get("balance")
        if target is None:
            return jsonify({"error": "balance_required"}), 400
        try:
            target_balance = int(float(target))
        except Exception:
            return jsonify({"error": "invalid_balance"}), 400
        if target_balance < 0:
            return jsonify({"error": "invalid_balance"}), 400
        capacity = payload.get("capacity") or payload.get("last_topup_tokens")
        try:
            capacity_val = int(float(capacity)) if capacity not in (None, "") else None
        except Exception:
            capacity_val = None
        target_paid = payload.get("paid_balance")
        target_free = payload.get("free_balance")
        try:
            target_paid_val = int(float(target_paid)) if target_paid not in (None, "") else None
        except Exception:
            target_paid_val = None
        try:
            target_free_val = int(float(target_free)) if target_free not in (None, "") else None
        except Exception:
            target_free_val = None
        delta = target_balance - snapshot["balance"]
        status = "matched" if delta == 0 else "adjusted"
        token_ledger.record(
            user,
            "sync",
            delta,
            {
                "target_balance": target_balance,
                "capacity": capacity_val,
                "source": payload.get("source") or "portal",
                "sync_user": payload.get("user") or user,
                "sync_role": payload.get("role"),
                "target_paid_balance": target_paid_val,
                "target_free_balance": target_free_val,
            },
        )
        snapshot = _token_snapshot(user)
        return jsonify({"message": "Sync complete.", "status": status, **snapshot})

    return jsonify({"error": "invalid_action"}), 400


@app.route("/api/tokens/ledger")
def tokens_ledger() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    target = (request.args.get("user") or "").strip()
    if target:
        if user_store.get_role(user) != "admin":
            return jsonify({"error": "forbidden"}), 403
        user = target
    limit = request.args.get("limit")
    try:
        limit_val = int(limit) if limit else 50
    except Exception:
        limit_val = 50
    entries = token_ledger.list_entries(user, limit=limit_val)
    return jsonify({"entries": entries})


@app.route("/api/secrets", methods=["GET", "POST"])
def secrets() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    store = _get_secret_store(user)
    if request.method == "GET":
        return jsonify({"secrets": store.list_masked()})
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name") or "").strip()
    value = str(payload.get("value") or "").strip()
    if not name or not value:
        return jsonify({"error": "name and value are required"}), 400
    if not SECRET_NAME_RE.match(name):
        return jsonify({"error": "invalid secret name"}), 400
    store.set(name, value)
    return jsonify({"name": name, "masked": SecretStore._mask(value), "updated_at": _now_iso()})


@app.route("/api/secrets/<name>", methods=["DELETE"])
def delete_secret(name: str) -> Response:
    if not SECRET_NAME_RE.match(name):
        return jsonify({"error": "invalid secret name"}), 400
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    store = _get_secret_store(user)
    deleted = store.delete(name)
    if not deleted:
        return jsonify({"error": "secret not found"}), 404
    return jsonify({"status": "deleted"})


@app.route("/api/github/tree", methods=["POST"])
def github_tree() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    repo_input = str(payload.get("repo_url") or payload.get("repo") or "").strip()
    branch = str(payload.get("branch") or "").strip()
    if not repo_input:
        return jsonify({"error": "repo is required"}), 400
    owner, repo = JobManager._parse_repo_input(repo_input)
    if not owner or not repo:
        return jsonify({"error": "invalid repo input"}), 400
    token = _get_github_api_token(user)
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    repo_url = f"https://api.github.com/repos/{owner}/{repo}"
    repo_resp = requests.get(repo_url, headers=headers, timeout=20)
    if repo_resp.status_code != 200:
        return jsonify({"error": "repo lookup failed", "details": repo_resp.text}), 400
    repo_data = repo_resp.json()
    default_branch = repo_data.get("default_branch") or "main"
    branch = branch or default_branch

    tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}"
    tree_resp = requests.get(tree_url, headers=headers, params={"recursive": "1"}, timeout=30)
    if tree_resp.status_code != 200:
        return jsonify({"error": "tree lookup failed", "details": tree_resp.text}), 400
    tree_data = tree_resp.json()
    items = tree_data.get("tree") or []
    max_entries = int(payload.get("max_entries") or 4000)
    trimmed = items[:max_entries]
    response_items = [
        {
            "path": item.get("path"),
            "type": item.get("type"),
            "size": item.get("size"),
        }
        for item in trimmed
        if item.get("path") and item.get("type") in {"blob", "tree"}
    ]
    return jsonify({"items": response_items, "branch": branch, "owner": owner, "repo": repo})


REQ_DRAFT_LINE_RE = re.compile(r"^\s*(?:[-*+]\s*)?(REQ-\d{3,})\s*(?:[:\-–]\s*)?(.*)$", re.I)
REQ_DRAFT_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*)$")
REQ_DRAFT_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$")


def _has_req_draft_lines(text: str) -> bool:
    for line in text.splitlines():
        if REQ_DRAFT_LINE_RE.match(line):
            return True
    return False


def _strip_req_register(text: str) -> str:
    lines = text.splitlines()
    output: List[str] = []
    skipping = False
    for line in lines:
        heading = REQ_DRAFT_HEADING_RE.match(line)
        if heading:
            title = heading.group(1).strip().lower()
            if title.startswith("requirements register"):
                skipping = True
                continue
            if skipping:
                skipping = False
        if not skipping:
            output.append(line)
    return "\n".join(output).strip()


def _collect_req_sections(text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {}
    current = None
    for line in text.splitlines():
        heading = REQ_DRAFT_HEADING_RE.match(line)
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _extract_bullets(lines: List[str]) -> List[str]:
    items: List[str] = []
    for line in lines:
        match = REQ_DRAFT_BULLET_RE.match(line)
        if match:
            item = match.group(1).strip()
            if item:
                items.append(item)
    return items


def _extract_requirement_candidates(text: str) -> List[str]:
    sections = _collect_req_sections(text)
    preferred_sections = [
        key
        for key in sections
        if "functional requirements" in key or "non-functional requirements" in key
    ]
    candidates: List[str] = []
    for key in preferred_sections:
        candidates.extend(_extract_bullets(sections.get(key, [])))
    if not candidates:
        candidates = _extract_bullets(text.splitlines())
    if not candidates:
        content = ""
        for key in preferred_sections:
            content += " ".join(sections.get(key, [])) + " "
        if not content.strip():
            content = text
        content = re.sub(r"\s+", " ", content).strip()
        if content:
            for sentence in re.split(r"(?<=[.!?])\s+", content):
                cleaned = sentence.strip()
                if cleaned:
                    candidates.append(cleaned)
                if len(candidates) >= 8:
                    break
    seen = set()
    unique: List[str] = []
    for item in candidates:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= 12:
            break
    return unique


def _build_requirements_register(items: List[str]) -> str:
    lines = ["## Requirements Register"]
    for idx, item in enumerate(items, start=1):
        lines.append(f"- REQ-{idx:03d}: {item}")
    return "\n".join(lines)


def _ensure_req_register_in_draft(text: str) -> str:
    if _has_req_draft_lines(text):
        return text
    candidates = _extract_requirement_candidates(text)
    if not candidates:
        candidates = ["Review draft and extract requirements from the sections above."]
    register = _build_requirements_register(candidates)
    stripped = _strip_req_register(text)
    if not stripped:
        return register
    return f"{stripped}\n\n{register}"


@app.route("/api/assistant/requirements", methods=["POST"])
def assistant_requirements() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    mode = (payload.get("mode") or "ask").strip().lower()
    prompt = (payload.get("prompt") or "").strip()
    requirements_text = (payload.get("requirements_text") or "").strip()
    messages = payload.get("messages") or []

    if mode not in {"ask", "draft"}:
        return jsonify({"error": "invalid mode"}), 400

    guard_text = "\n".join([requirements_text, prompt]).strip()
    if guard_text:
        reason = _guardrail_scan(guard_text)
        if reason:
            return jsonify({"error": "guardrail_blocked", "details": reason}), 400

    provider_hint = payload.get("provider") or payload.get("llm_provider") or "openai"
    model_hint = payload.get("model") or payload.get("llm_model") or "gpt-5.1"
    reasoning_effort = payload.get("reasoning_effort") or payload.get("llm_reasoning_effort") or "medium"

    settings = _resolve_llm_settings(
        user=user,
        provider_hint=provider_hint,
        model_hint=model_hint,
    )
    try:
        provider = get_provider(
            settings["provider"],
            model=settings.get("model"),
            base_url=settings.get("base_url"),
            api_key=settings.get("api_key"),
            inter_request_gap=0.0,
        )
    except LLMError as exc:
        return jsonify({"error": "llm_unavailable", "details": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": "llm_init_failed", "details": str(exc)}), 400

    if not provider:
        return jsonify({"error": "llm_unavailable"}), 400

    system = (
        "You are a requirements assistant. Help the user craft clear, testable requirements. "
        "Ask concise clarifying questions when needed. "
        "When drafting, output structured Markdown with sections: Overview, Goals, Non-Goals, "
        "Functional Requirements, Non-Functional Requirements, Acceptance Criteria, Risks. "
        "Include a 'Requirements Register' section with one requirement per line in the format "
        "'- REQ-001: Short title' (zero-padded, unique IDs). Add any detail as indented bullets "
        "beneath each REQ line so the register can be parsed."
    )
    capabilities_hint = capability_summary(max_items=3)
    if capabilities_hint:
        system = f"{system}\n\nCapabilities summary:\n{capabilities_hint}"

    chat_messages: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            chat_messages.append({"role": role, "content": content})

    if mode == "draft":
        user_text = "Draft a complete requirements document."
        if requirements_text:
            user_text += f"\n\nCurrent notes:\n{requirements_text}"
        chat_messages.append({"role": "user", "content": user_text})
    else:
        if not prompt:
            return jsonify({"error": "prompt_required"}), 400
        if requirements_text:
            prompt = f"Current requirements notes:\\n{requirements_text}\\n\\nUser question: {prompt}"
        chat_messages.append({"role": "user", "content": prompt})

    temperature = payload.get("temperature", 0.2)
    max_tokens = payload.get("max_tokens")
    reasoning_effort = payload.get("reasoning_effort")
    try:
        response = provider.predict(
            messages=chat_messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:
        return jsonify({"error": "llm_request_failed", "details": str(exc)}), 400

    reply_text = response.text if isinstance(response.text, str) else str(response.text)
    if mode == "draft":
        reply_text = _ensure_req_register_in_draft(reply_text)

    return jsonify(
        {
            "reply": reply_text,
            "provider": response.provider or settings.get("provider"),
            "model": response.model or settings.get("model"),
        }
    )


@app.route("/api/assistant/form-fill", methods=["POST"])
def assistant_form_fill() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    fields = payload.get("fields") or []
    prompt = (payload.get("prompt") or "").strip()
    workflow = (payload.get("workflow") or "").strip()
    scope = (payload.get("scope") or "").strip()
    if not isinstance(fields, list) or not fields:
        return jsonify({"error": "fields_required"}), 400

    reason = _guardrail_scan(prompt)
    if reason:
        return jsonify({"error": "guardrail_blocked", "details": reason}), 400

    provider_hint = payload.get("provider") or payload.get("llm_provider") or "openai"
    model_hint = payload.get("model") or payload.get("llm_model") or "gpt-5.1"
    reasoning_effort = payload.get("reasoning_effort") or payload.get("llm_reasoning_effort") or "medium"

    settings = _resolve_llm_settings(
        user=user,
        provider_hint=provider_hint,
        model_hint=model_hint,
    )
    try:
        provider = get_provider(
            settings["provider"],
            model=settings.get("model"),
            base_url=settings.get("base_url"),
            api_key=settings.get("api_key"),
            inter_request_gap=0.0,
        )
    except Exception as exc:
        return jsonify({"error": "llm_init_failed", "details": str(exc)}), 400
    if not provider:
        return jsonify({"error": "llm_unavailable"}), 400

    field_descriptions = []
    allowed_ids = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        field_id = field.get("id") or field.get("field_id")
        if not field_id:
            continue
        allowed_ids.append(field_id)
        label = field.get("label") or field_id
        ftype = field.get("type") or "text"
        value = field.get("value")
        options = field.get("options")
        desc = field.get("description") or ""
        entry = {
            "id": field_id,
            "label": label,
            "type": ftype,
            "value": value,
            "options": options,
            "description": desc,
        }
        field_descriptions.append(entry)

    system = (
        "You are a form assistant. Return ONLY valid JSON. "
        "Output an array of objects with keys: field_id, value, rationale (optional). "
        "Only use field_id values from the allowed list. "
        "Do not include markdown or extra text."
    )
    capabilities_hint = capability_summary(max_items=3)
    if capabilities_hint:
        system = f"{system}\n\nCapabilities summary:\n{capabilities_hint}"

    user_text = {
        "goal": prompt,
        "workflow": workflow,
        "scope": scope,
        "allowed_fields": allowed_ids,
        "fields": field_descriptions,
    }

    try:
        response = provider.predict(
            messages=[{"role": "user", "content": json.dumps(user_text)}],
            system=system,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens"),
        )
    except Exception as exc:
        return jsonify({"error": "llm_request_failed", "details": str(exc)}), 400

    parsed = _extract_json_payload(response.text)
    if not isinstance(parsed, list):
        return jsonify({"error": "invalid_llm_response", "details": response.text[:500]}), 400

    cleaned = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        field_id = item.get("field_id") or item.get("id")
        if not field_id or field_id not in allowed_ids:
            continue
        cleaned.append(
            {
                "field_id": field_id,
                "value": item.get("value"),
                "rationale": item.get("rationale"),
            }
        )

    if not cleaned:
        return jsonify({"error": "no_suggestions"}), 400

    return jsonify({"suggestions": cleaned})


@app.route("/api/playground/plan", methods=["POST"])
def playground_plan() -> Response:
    user = _current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt_required"}), 400

    reason = _guardrail_scan(prompt)
    if reason:
        return jsonify({"error": "guardrail_blocked", "details": reason}), 400

    provider_hint = payload.get("provider") or payload.get("llm_provider") or "openai"
    model_hint = payload.get("model") or payload.get("llm_model") or "gpt-5.1"
    reasoning_effort = payload.get("reasoning_effort") or payload.get("llm_reasoning_effort") or "medium"

    settings = _resolve_llm_settings(
        user=user,
        provider_hint=provider_hint,
        model_hint=model_hint,
    )
    try:
        provider = get_provider(
            settings["provider"],
            model=settings.get("model"),
            base_url=settings.get("base_url"),
            api_key=settings.get("api_key"),
            inter_request_gap=0.0,
        )
    except Exception as exc:
        return jsonify({"error": "llm_init_failed", "details": str(exc)}), 400
    if not provider:
        return jsonify({"error": "llm_unavailable"}), 400

    system = (
        "You are School Monitor, a friendly assistant for non-technical pupils. "
        "Use UK British English spelling and phrasing. "
        "Keep responses short, simple, and upbeat. Return ONLY valid JSON with keys: "
        "summary (string), steps (array of strings), requirements_text (string), project_name (string). "
        "Summary should be 1-2 sentences. Steps should be 4-7 short, easy-to-follow items. "
        "Requirements text should be brief and practical, include a short overview and a "
        "'Requirements Register' section with 6-10 lines formatted like '- REQ-001: ...'. "
        "Keep the scope small and fast to build. "
        "If the project is a web app, prefer Node.js and a playful, colourful UI with cards, "
        "levels, and rewards similar to a child-friendly dashboard."
    )

    user_text = {
        "prompt": prompt,
        "constraints": {
            "speed": "quick",
            "scope": "small",
        },
    }

    try:
        response = provider.predict(
            messages=[{"role": "user", "content": json.dumps(user_text)}],
            system=system,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens", 900),
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:
        return jsonify({"error": "llm_request_failed", "details": str(exc)}), 400

    parsed = _extract_json_payload(response.text)
    if not isinstance(parsed, dict):
        return jsonify({"error": "invalid_llm_response", "details": str(response.text)[:400]}), 400

    summary = str(parsed.get("summary") or "").strip()
    steps_raw = parsed.get("steps") or []
    steps = [str(item).strip() for item in steps_raw if isinstance(item, (str, int, float)) and str(item).strip()]
    requirements_text = str(parsed.get("requirements_text") or "").strip()
    project_name = str(parsed.get("project_name") or "").strip()

    summary = _to_uk_english(summary)
    steps = [_to_uk_english(step) for step in steps]
    requirements_text = _to_uk_english(requirements_text)

    if not project_name:
        project_name = (summary or prompt).strip()
    project_name = project_name[:60] if project_name else "Playground Project"

    if not requirements_text:
        steps_block = "\n".join([f"- {step}" for step in steps]) if steps else ""
        requirements_text = f"{summary or prompt}\n\nKey Features:\n{steps_block}\n\nRequirements Register:\n- REQ-001: Provide a simple web app\n- REQ-002: Keep the scope small and fast to build\n- REQ-003: Include clear, child-friendly UI copy\n- REQ-004: Add basic navigation between screens\n- REQ-005: Provide simple progress feedback\n- REQ-006: Use a clean, responsive layout\n"
        requirements_text = _to_uk_english(requirements_text)

    req_count = sum(1 for line in requirements_text.splitlines() if REQ_LINE_RE.match(line))
    if req_count <= 0:
        req_count = max(1, len(steps)) if steps else 6

    job_payload = {
        "workflow": "project_solver",
        "project_name": project_name,
        "requirements_text": requirements_text,
        "project_run": True,
        "project_max_steps": 100,
        "project_iterations": req_count,
        "llm_provider": settings.get("provider") or provider_hint,
        "llm_model": settings.get("model") or model_hint,
        "llm_reasoning_effort": reasoning_effort,
        "llm_temperature": 0.2,
        "llm_max_tokens": DEFAULT_LLM_MAX_TOKENS,
        "disable_jira": True,
        "disable_confluence": True,
        "action_plan": False,
        "dry_run": False,
    }

    return jsonify(
        {
            "summary": summary,
            "steps": steps,
            "project_name": project_name,
            "requirements_text": requirements_text,
            "job_payload": job_payload,
            "provider": response.provider or settings.get("provider"),
            "model": response.model or settings.get("model"),
        }
    )


def _sse(entry: Dict[str, Any]) -> str:
    payload = json.dumps(entry)
    return f"data: {payload}\n\n"


if __name__ == "__main__":
    host = os.getenv("REFINER_HOST", "127.0.0.1")
    port = int(os.getenv("REFINER_PORT", "5001"))
    debug = os.getenv("REFINER_DEBUG", "0") in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug, threaded=True)
