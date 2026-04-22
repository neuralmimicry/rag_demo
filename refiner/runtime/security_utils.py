"""Security and audit helpers used across Refiner services.

This module centralizes:
- log redaction of common secret formats,
- URL safety policy checks (allowlist/blocklist/private-host guards), and
- lightweight append-only audit logging.

The helpers are intentionally dependency-light so they can be reused by both
CLI and web entry points.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


_DEFAULT_REDACT_KEYS = [
    "API_KEY",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "KEY",
]

_DEFAULT_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{16,}",
    r"ghp_[A-Za-z0-9]{20,}",
    r"gho_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"ya29\.[A-Za-z0-9\-_]+",
    r"AIza[0-9A-Za-z\-_]{20,}",
]


def _split_csv(value: str) -> List[str]:
    """Split a comma-separated env value into normalized non-empty entries."""
    return [item.strip() for item in value.split(",") if item and item.strip()]


def _redaction_patterns() -> List[re.Pattern]:
    """Compile redaction patterns from env overrides or secure defaults."""
    patterns: List[str] = []
    env_keys = _split_csv(os.getenv("REFINER_REDACT_KEYS", ""))
    env_patterns = _split_csv(os.getenv("REFINER_REDACT_PATTERNS", ""))
    keys = env_keys or _DEFAULT_REDACT_KEYS
    for key in keys:
        key = key.strip()
        if not key:
            continue
        patterns.append(rf"({re.escape(key)}\s*[:=]\s*)([^\s,;]+)")
    patterns.extend(env_patterns or _DEFAULT_SECRET_PATTERNS)
    compiled: List[re.Pattern] = []
    for pat in patterns:
        try:
            compiled.append(re.compile(pat))
        except re.error:
            continue
    return compiled


_REDACT_PATTERNS = _redaction_patterns()


def redact_text(text: str) -> str:
    """Redact likely secret values from a free-form text string."""
    if not text:
        return text
    redacted = text
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub(lambda m: m.group(1) + "***" if m.groups() else "***", redacted)
    return redacted


class RedactionFilter(logging.Filter):
    """Logging filter that redacts secrets in message templates and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Apply in-place redaction and keep the record."""
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            if record.args:
                redacted_args = []
                for arg in record.args:
                    if isinstance(arg, str):
                        redacted_args.append(redact_text(arg))
                    else:
                        redacted_args.append(arg)
                record.args = tuple(redacted_args)
        except Exception:
            pass
        return True


def attach_redaction_filter(logger_obj: Optional[logging.Logger] = None) -> None:
    """Attach :class:`RedactionFilter` to each handler on the target logger."""
    target = logger_obj or logging.getLogger()
    for handler in target.handlers:
        handler.addFilter(RedactionFilter())


def ensure_file_permissions(path: str, mode: int = 0o600) -> None:
    """Best-effort chmod for sensitive files."""
    try:
        os.chmod(path, mode)
    except Exception:
        return


def ensure_dir_permissions(path: str, mode: int = 0o700) -> None:
    """Create a directory and enforce restrictive permissions."""
    try:
        os.makedirs(path, exist_ok=True)
        os.chmod(path, mode)
    except Exception:
        return


def is_private_host(hostname: str) -> bool:
    """Return ``True`` when hostname resolves to local/private network ranges."""
    if not hostname:
        return True
    lowered = hostname.lower()
    if lowered in {"localhost"}:
        return True
    if lowered.endswith(".local") or lowered.endswith(".internal"):
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ip_address(addr)
        except Exception:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def url_allowed(url: str) -> bool:
    """Validate a URL against scheme, allowlist, blocklist, and private-host policy."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname or ""
    if not host:
        return False
    allowlist = _split_csv(os.getenv("REFINER_URL_ALLOWLIST", ""))
    allowed_by_list = False
    if allowlist:
        allowed_by_list = any(host == entry or host.endswith("." + entry) for entry in allowlist)
        if not allowed_by_list:
            return False
    allow_private = os.getenv("REFINER_ALLOW_PRIVATE_URLS", "").strip().lower() in {"1", "true", "yes", "y"}
    if not allow_private and not allowed_by_list and is_private_host(host):
        return False
    blocklist = _split_csv(os.getenv("REFINER_URL_BLOCKLIST", ""))
    if blocklist:
        if any(host == entry or host.endswith("." + entry) for entry in blocklist):
            return False
    return True


@dataclass
class AuditEvent:
    """Structured audit record written by :class:`AuditLogger`."""

    action: str
    actor: Optional[str]
    status: str
    timestamp: float
    details: Dict[str, object]

    def to_record(self) -> Dict[str, object]:
        """Serialize the event into a JSON-ready dictionary."""
        return {
            "action": self.action,
            "actor": self.actor,
            "status": self.status,
            "ts": self.timestamp,
            "details": self.details,
        }


class AuditLogger:
    """Append-only JSONL audit logger with secure file-permission defaults."""

    def __init__(self, path: str):
        """Create the target file and ensure restrictive permissions."""
        self.path = path
        ensure_dir_permissions(os.path.dirname(path), mode=0o700)
        try:
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("")
            ensure_file_permissions(path, 0o600)
        except Exception:
            pass

    def log(self, action: str, *, actor: Optional[str], status: str, details: Optional[Dict[str, object]] = None) -> None:
        """Write one audit event; failures are non-fatal and debug logged."""
        payload = AuditEvent(
            action=action,
            actor=actor,
            status=status,
            timestamp=time.time(),
            details=details or {},
        ).to_record()
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except Exception as exc:
            logger.debug("Failed to write audit log: %s", exc)


def hash_identifier(value: str) -> str:
    """Return a stable SHA-256 hash for pseudonymous identifiers."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
