"""Reusable plan/act/verify/reflect workflow primitives.

The module models a deterministic agentic loop with:
- phase-level status semantics (ok/retry/halt/error),
- event history for traceability, and
- optional progress-path tracking for dead-end avoidance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import logging
import time


_STATUS_ORDER = {"ok": 0, "retry": 1, "halt": 2, "error": 3}


@dataclass
class PhaseResult:
    """Normalized outcome produced by one workflow phase handler."""

    status: str = "ok"
    notes: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, notes: str = "", data: Optional[Dict[str, Any]] = None) -> "PhaseResult":
        """Convenience constructor for successful phase completion."""
        return cls(status="ok", notes=notes, data=data or {})

    @classmethod
    def retry(cls, notes: str = "", data: Optional[Dict[str, Any]] = None) -> "PhaseResult":
        """Signal that the workflow should retry another cycle."""
        return cls(status="retry", notes=notes, data=data or {})

    @classmethod
    def halt(cls, notes: str = "", data: Optional[Dict[str, Any]] = None) -> "PhaseResult":
        """Signal a graceful stop without treating the run as an error."""
        return cls(status="halt", notes=notes, data=data or {})

    @classmethod
    def error(cls, notes: str = "", data: Optional[Dict[str, Any]] = None) -> "PhaseResult":
        """Signal terminal failure for the workflow."""
        return cls(status="error", notes=notes, data=data or {})

    def normalized_status(self) -> str:
        """Return a supported status token, defaulting unknown values to ``ok``."""
        status = (self.status or "ok").strip().lower()
        return status if status in _STATUS_ORDER else "ok"


@dataclass
class PhaseEvent:
    """Recorded execution event for one phase within one cycle."""

    cycle: int
    phase: str
    status: str
    notes: str
    data: Dict[str, Any]
    timestamp: float
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowState:
    """Aggregated workflow state and phase history."""

    label: str
    context: Dict[str, Any] = field(default_factory=dict)
    events: List[PhaseEvent] = field(default_factory=list)

    def record(self, event: PhaseEvent) -> None:
        """Append an event to workflow history."""
        self.events.append(event)

    def last_event(self, phase: Optional[str] = None) -> Optional[PhaseEvent]:
        """Return the latest event overall or for a specific phase."""
        if not self.events:
            return None
        if not phase:
            return self.events[-1]
        for event in reversed(self.events):
            if event.phase == phase:
                return event
        return None


@dataclass
class PathEntry:
    """One node in the progress tracker path history."""

    id: int
    label: str
    source: str
    iteration: int
    status: str
    notes: str
    data: Dict[str, Any]
    timestamp: float
    parent_id: Optional[int] = None
    retrace_to: Optional[int] = None


class ProgressTracker:
    """Tracks explored solution paths and dead ends across iterations."""

    def __init__(
        self,
        *,
        label: str,
        logger: Optional[logging.Logger] = None,
        entries: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.label = label
        self.logger = logger or logging.getLogger(__name__)
        self.entries: List[PathEntry] = []
        self._next_id = 1
        if entries:
            self._load(entries)

    def _load(self, entries: List[Dict[str, Any]]) -> None:
        """Rehydrate tracker entries from serialized history."""
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                pid = int(entry.get("id") or 0)
            except Exception:
                pid = 0
            if not pid:
                continue
            path = PathEntry(
                id=pid,
                label=str(entry.get("label") or self.label),
                source=str(entry.get("source") or ""),
                iteration=int(entry.get("iteration") or 0),
                status=str(entry.get("status") or ""),
                notes=str(entry.get("notes") or ""),
                data=entry.get("data") or {},
                timestamp=float(entry.get("timestamp") or time.time()),
                parent_id=entry.get("parent_id"),
                retrace_to=entry.get("retrace_to"),
            )
            self.entries.append(path)
            if pid >= self._next_id:
                self._next_id = pid + 1

    def record(
        self,
        *,
        source: str,
        iteration: int,
        status: str,
        notes: str = "",
        data: Optional[Dict[str, Any]] = None,
        parent_id: Optional[int] = None,
    ) -> int:
        """Create a new path entry and return its generated ID."""
        entry = PathEntry(
            id=self._next_id,
            label=self.label,
            source=source,
            iteration=iteration,
            status=status,
            notes=notes,
            data=data or {},
            timestamp=time.time(),
            parent_id=parent_id,
            retrace_to=None,
        )
        self._next_id += 1
        self.entries.append(entry)
        return entry.id

    def update(
        self,
        entry_id: int,
        *,
        status: Optional[str] = None,
        notes: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        retrace_to: Optional[int] = None,
    ) -> None:
        """Update status/metadata for an existing entry."""
        entry = self._get(entry_id)
        if not entry:
            return
        if status:
            entry.status = status
        if notes:
            entry.notes = notes
        if data:
            entry.data.update(data)
        if retrace_to is not None:
            entry.retrace_to = retrace_to

    def mark_dead_end(self, entry_id: int, reason: str, *, retrace_to: Optional[int] = None) -> None:
        """Mark an entry as a dead end with optional retrace target."""
        self.update(entry_id, status="dead_end", notes=reason, retrace_to=retrace_to)

    def _get(self, entry_id: int) -> Optional[PathEntry]:
        """Return one path entry by ID."""
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def summarize(self, source: Optional[str] = None, max_items: int = 6) -> str:
        """Render a compact path summary for prompts/logs."""
        entries = self.entries
        if source:
            entries = [e for e in entries if e.source == source]
        if not entries:
            return ""
        tail = entries[-max_items:] if max_items and len(entries) > max_items else entries
        lines = ["Path history (most recent first; avoid repeating dead ends):"]
        for entry in reversed(tail):
            note = entry.notes.strip()
            if len(note) > 160:
                note = note[:160].rstrip() + "..."
            retrace = f"; retrace_to={entry.retrace_to}" if entry.retrace_to else ""
            lines.append(
                f"- id {entry.id} iter {entry.iteration}: {entry.status}{retrace} ({note or 'no notes'})"
            )
        return "\n".join(lines).strip()

    def export(self) -> Dict[str, Any]:
        """Serialize tracker state for persistence."""
        return {
            "label": self.label,
            "entries": [
                {
                    "id": entry.id,
                    "label": entry.label,
                    "source": entry.source,
                    "iteration": entry.iteration,
                    "status": entry.status,
                    "notes": entry.notes,
                    "data": entry.data,
                    "timestamp": entry.timestamp,
                    "parent_id": entry.parent_id,
                    "retrace_to": entry.retrace_to,
                }
                for entry in self.entries
            ],
        }


class AgenticCycle:
    """Mutable per-cycle context passed to phase handlers."""

    def __init__(
        self,
        workflow: "AgenticWorkflow",
        cycle_index: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.workflow = workflow
        self.state = workflow.state
        self.cycle = cycle_index
        self.context = context or {}
        self.data: Dict[str, Any] = {}
        self.decision = "continue"

    def set(self, key: str, value: Any) -> None:
        """Store phase-local data on the cycle."""
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Read phase-local data from the cycle."""
        return self.data.get(key, default)

    def record(self, phase: str, result: Optional[PhaseResult]) -> PhaseEvent:
        """Record a phase result and update control-flow decision state."""
        if result is None:
            result = PhaseResult.ok()
        status = result.normalized_status()
        event = PhaseEvent(
            cycle=self.cycle,
            phase=phase,
            status=status,
            notes=result.notes or "",
            data=result.data or {},
            timestamp=time.time(),
            context=dict(self.context),
        )
        self.state.record(event)
        self.workflow._log_event(event)
        self._update_decision(status)
        return event

    def _update_decision(self, status: str) -> None:
        """Promote cycle decision severity based on observed status."""
        if status not in _STATUS_ORDER:
            return
        current = _STATUS_ORDER.get(self.decision, 0)
        incoming = _STATUS_ORDER[status]
        if incoming > current:
            if status == "retry":
                self.decision = "retry"
            elif status in ("halt", "error"):
                self.decision = status


class AgenticWorkflow:
    """Coordinator for deterministic multi-phase, multi-cycle execution."""

    def __init__(
        self,
        *,
        phases: Optional[List[str]] = None,
        max_cycles: int = 1,
        logger: Optional[logging.Logger] = None,
        label: str = "agentic_workflow",
    ) -> None:
        self.phases = phases or ["plan", "act", "verify", "reflect"]
        self.max_cycles = max_cycles
        self.logger = logger or logging.getLogger(__name__)
        self.label = label
        self.state = WorkflowState(label=label)

    def start_cycle(self, cycle_index: int, context: Optional[Dict[str, Any]] = None) -> AgenticCycle:
        """Create a new cycle wrapper bound to workflow state."""
        return AgenticCycle(self, cycle_index, context=context)

    def run(
        self,
        handlers: Dict[str, Callable[[AgenticCycle], PhaseResult]],
        *,
        max_cycles: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
        on_cycle_end: Optional[Callable[[AgenticCycle], None]] = None,
    ) -> WorkflowState:
        """Execute configured phases until completion, retry, or terminal stop."""
        cycles = max_cycles or self.max_cycles
        if not cycles or cycles <= 0:
            return self.state
        for cycle_idx in range(1, cycles + 1):
            cycle = self.start_cycle(cycle_idx, context=context)
            for phase in self.phases:
                handler = handlers.get(phase)
                if not handler:
                    continue
                result = handler(cycle)
                cycle.record(phase, result)
                if cycle.decision in ("retry", "halt", "error"):
                    break
            if on_cycle_end:
                on_cycle_end(cycle)
            if cycle.decision == "retry":
                continue
            if cycle.decision in ("halt", "error"):
                break
        return self.state

    def export(self) -> Dict[str, Any]:
        """Serialize workflow events for downstream diagnostics."""
        return {
            "label": self.state.label,
            "events": [
                {
                    "cycle": event.cycle,
                    "phase": event.phase,
                    "status": event.status,
                    "notes": event.notes,
                    "data": event.data,
                    "timestamp": event.timestamp,
                    "context": event.context,
                }
                for event in self.state.events
            ],
        }

    def _log_event(self, event: PhaseEvent) -> None:
        """Emit a debug log for each recorded phase event."""
        if not self.logger:
            return
        self.logger.debug(
            "AgenticWorkflow[%s] cycle=%s phase=%s status=%s",
            self.label,
            event.cycle,
            event.phase,
            event.status,
        )
