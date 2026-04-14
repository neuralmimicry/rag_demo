"""Persistent episodic memory for Refiner's project solver.

The solver already tracks per-run progress in memory and can resume from the
previous JSON output file. That still leaves a gap: once a run finishes, the
next run has no compact, queryable record of what previously worked or failed
for a given requirement source. This module closes that gap with a small,
dependency-light JSONL store.

The store is intentionally lexical rather than embedding-based:

- no extra services or heavy dependencies,
- deterministic scoring that works offline,
- straightforward auditing of what was stored, and
- easy truncation/compaction when the history grows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import calendar
import json
import math
import os
import re
import tempfile
import time
import uuid
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from security_utils import ensure_dir_permissions, ensure_file_permissions


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{1,}")
_MAX_SEARCH_RESULTS = 6


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_text(value: object, max_chars: int = 400) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "...(truncated)"


def _safe_list(values: Iterable[object], *, max_items: int = 8, max_chars: int = 240) -> List[str]:
    items: List[str] = []
    for value in values:
        text = _safe_text(value, max_chars=max_chars)
        if not text:
            continue
        if text not in items:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _created_at_timestamp(value: str) -> float:
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return time.time()


@dataclass
class SolverEpisode:
    """Compact record of one solver iteration or source outcome."""

    episode_id: str
    source_path: str
    iteration: int
    created_at: str
    outcome: str
    summary: str
    requirement_ids: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    verification_failures: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "source_path": self.source_path,
            "iteration": self.iteration,
            "created_at": self.created_at,
            "outcome": self.outcome,
            "summary": self.summary,
            "requirement_ids": list(self.requirement_ids),
            "modified_files": list(self.modified_files),
            "commands": list(self.commands),
            "verification_failures": list(self.verification_failures),
            "notes": list(self.notes),
        }

    @classmethod
    def from_record(cls, record: Dict[str, object]) -> Optional["SolverEpisode"]:
        if not isinstance(record, dict):
            return None
        source_path = _safe_text(record.get("source_path"), max_chars=512)
        created_at = _safe_text(record.get("created_at"), max_chars=64)
        outcome = _safe_text(record.get("outcome"), max_chars=32)
        if not source_path or not created_at or not outcome:
            return None
        try:
            iteration = int(record.get("iteration") or 0)
        except Exception:
            iteration = 0
        return cls(
            episode_id=_safe_text(record.get("episode_id"), max_chars=128) or uuid.uuid4().hex,
            source_path=source_path,
            iteration=max(0, iteration),
            created_at=created_at,
            outcome=outcome,
            summary=_safe_text(record.get("summary"), max_chars=1200),
            requirement_ids=_safe_list(record.get("requirement_ids") or [], max_items=16, max_chars=48),
            modified_files=_safe_list(record.get("modified_files") or [], max_items=12, max_chars=240),
            commands=_safe_list(record.get("commands") or [], max_items=8, max_chars=240),
            verification_failures=_safe_list(
                record.get("verification_failures") or [], max_items=6, max_chars=280
            ),
            notes=_safe_list(record.get("notes") or [], max_items=8, max_chars=280),
        )

    def search_blob(self) -> str:
        parts = [
            self.source_path,
            self.summary,
            " ".join(self.requirement_ids),
            " ".join(self.modified_files),
            " ".join(self.commands),
            " ".join(self.verification_failures),
            " ".join(self.notes),
            self.outcome,
        ]
        return "\n".join(part for part in parts if part).strip()

    def prompt_line(self) -> str:
        bits = [f"[{self.outcome}] {self.source_path} iter {self.iteration}"]
        if self.requirement_ids:
            bits.append("reqs=" + ", ".join(self.requirement_ids[:6]))
        if self.modified_files:
            bits.append("files=" + ", ".join(self.modified_files[:4]))
        if self.commands:
            bits.append("commands=" + "; ".join(self.commands[:2]))
        if self.verification_failures:
            bits.append("failures=" + "; ".join(self.verification_failures[:2]))
        if self.summary:
            bits.append("summary=" + self.summary)
        return ". ".join(bit for bit in bits if bit).strip()


class SolverEpisodeStore:
    """Append-only JSONL store with bounded compaction and lexical retrieval."""

    def __init__(self, path: str, *, max_entries: int = 300, compact_every: int = 25):
        self.path = path
        self.max_entries = max(1, int(max_entries))
        self.compact_every = max(1, int(compact_every))
        self._entries: List[SolverEpisode] = []
        self._append_count = 0
        self._load()

    def _load(self) -> None:
        self._entries = []
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    episode = SolverEpisode.from_record(payload)
                    if episode is not None:
                        self._entries.append(episode)
        except Exception:
            self._entries = []
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]

    def _ensure_path(self) -> None:
        parent = os.path.dirname(self.path) or "."
        ensure_dir_permissions(parent, mode=0o700)
        if not os.path.exists(self.path):
            with open(self.path, "a", encoding="utf-8"):
                pass
        ensure_file_permissions(self.path, mode=0o600)

    def _compact(self) -> None:
        entries = self._entries[-self.max_entries :]
        self._entries = entries
        parent = os.path.dirname(self.path) or "."
        ensure_dir_permissions(parent, mode=0o700)
        fd, temp_path = tempfile.mkstemp(prefix=".solver_memory_", suffix=".jsonl", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry.to_record(), sort_keys=True) + "\n")
            os.replace(temp_path, self.path)
            ensure_file_permissions(self.path, mode=0o600)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass

    def record(self, episode: SolverEpisode) -> None:
        if not isinstance(episode, SolverEpisode):
            return
        self._ensure_path()
        self._entries.append(episode)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(episode.to_record(), sort_keys=True) + "\n")
        ensure_file_permissions(self.path, mode=0o600)
        self._append_count += 1
        if len(self._entries) > self.max_entries or self._append_count >= self.compact_every:
            self._append_count = 0
            self._compact()

    def recent(self, *, source_path: Optional[str] = None, limit: int = 3) -> List[SolverEpisode]:
        selected = self._entries
        if source_path:
            selected = [entry for entry in selected if entry.source_path == source_path]
        if not selected:
            return []
        limit = max(1, min(limit, _MAX_SEARCH_RESULTS))
        return list(reversed(selected[-limit:]))

    def search(
        self,
        query_text: str,
        *,
        source_path: Optional[str] = None,
        requirement_ids: Optional[Sequence[str]] = None,
        limit: int = 3,
    ) -> List[SolverEpisode]:
        if not self._entries:
            return []

        query_tokens = set(_tokenize(query_text))
        requirement_ids = [item for item in (requirement_ids or []) if item]
        scored: List[Tuple[float, SolverEpisode]] = []
        now = time.time()

        for entry in self._entries:
            score = 0.0
            if source_path and entry.source_path == source_path:
                score += 6.0
            elif source_path and os.path.basename(entry.source_path) == os.path.basename(source_path):
                score += 2.0

            if requirement_ids:
                overlap = len(set(entry.requirement_ids) & set(requirement_ids))
                score += overlap * 2.5

            entry_tokens = set(_tokenize(entry.search_blob()))
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
            return self.recent(source_path=source_path, limit=limit)

        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [entry for _, entry in scored[: max(1, min(limit, _MAX_SEARCH_RESULTS))]]

    def format_for_prompt(
        self,
        query_text: str,
        *,
        source_path: Optional[str] = None,
        requirement_ids: Optional[Sequence[str]] = None,
        limit: int = 3,
        max_chars: int = 2400,
    ) -> str:
        entries = self.search(
            query_text,
            source_path=source_path,
            requirement_ids=requirement_ids,
            limit=limit,
        )
        if not entries:
            return ""
        lines = ["Relevant solver memory from earlier runs (reuse wins and avoid repeated failures):"]
        for entry in entries:
            lines.append(f"- {entry.prompt_line()}")
        text = "\n".join(lines).strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 15].rstrip() + "...(truncated)"
