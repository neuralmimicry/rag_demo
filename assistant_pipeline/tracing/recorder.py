"""Trace recording helpers for the assistant pipeline.

The recorder remains best-effort so assistant/RAG requests do not fail when the
optional central trace store is unavailable.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from assistant_pipeline.dependencies import AssistantPipelineDependencies


@dataclass
class TraceRecorder:
    """Best-effort stage recorder backed by the optional Postgres trace store."""

    deps: AssistantPipelineDependencies
    owner: str
    route: str
    intent: str = ""
    conversation_id: Optional[str] = None
    request_meta: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.trace_id = self.deps.new_trace_id()
        self._store = self.deps.get_assistant_trace_store()
        if self._store is None or not self.owner:
            return
        try:
            self._store.start_trace(
                self.trace_id,
                self.owner,
                route=self.route,
                intent=self.intent,
                conversation_id=self.conversation_id,
                request_meta=self.request_meta,
            )
        except Exception as exc:  # pragma: no cover - best effort only
            self.deps.logger.debug("Assistant trace start skipped for %s: %s", self.route, exc)
            self._store = None

    def record_span(
        self,
        stage: str,
        started_at: float,
        *,
        status: str = "success",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record one trace span with a derived duration in milliseconds."""

        if self._store is None:
            return
        try:
            duration_ms = max(0, int(round((time.monotonic() - started_at) * 1000.0)))
            span_kwargs = {
                "status": status,
                "duration_ms": duration_ms,
            }
            record_span = self._store.record_span
            try:
                parameters = inspect.signature(record_span).parameters
            except (TypeError, ValueError):
                parameters = {}
            supports_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
            metadata_key = "metadata" if supports_kwargs or "metadata" in parameters else "meta"
            span_kwargs[metadata_key] = metadata
            record_span(self.trace_id, stage, **span_kwargs)
        except Exception as exc:  # pragma: no cover - best effort only
            self.deps.logger.debug("Assistant trace span skipped for %s/%s: %s", self.route, stage, exc)

    def finish(
        self,
        *,
        status: str,
        provider: str = "",
        model: str = "",
        cache_hit: Optional[bool] = None,
        response_meta: Optional[Dict[str, Any]] = None,
        error_code: str = "",
        error_detail: str = "",
    ) -> None:
        """Mark the trace as finished without surfacing storage failures."""

        if self._store is None:
            return
        try:
            self._store.finish_trace(
                self.trace_id,
                status=status,
                provider=provider,
                model=model,
                cache_hit=cache_hit,
                response_meta=response_meta,
                error_code=error_code,
                error_detail=error_detail,
            )
        except Exception as exc:  # pragma: no cover - best effort only
            self.deps.logger.debug("Assistant trace finish skipped for %s: %s", self.route, exc)
