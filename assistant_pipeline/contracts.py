"""Shared contracts for assistant pipeline services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ServiceResult:
    """Normalised service output for HTTP route handlers."""

    payload: Any
    status_code: int = 200


@dataclass(frozen=True)
class TraceSpanResult:
    """In-memory representation of one recorded pipeline span."""

    stage: str
    status: str = "success"
    duration_ms: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ServiceError(RuntimeError):
    """Typed service failure that preserves existing API error semantics."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int = 400,
        details: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(details or code)
        self.code = str(code or "service_error").strip() or "service_error"
        self.status_code = int(status_code or 400)
        self.details = str(details).strip() if details not in (None, "") else None
        self.payload = dict(payload or {})

    def to_payload(self) -> Dict[str, Any]:
        payload = {"error": self.code}
        if self.details:
            payload["details"] = self.details
        payload.update(self.payload)
        return payload
