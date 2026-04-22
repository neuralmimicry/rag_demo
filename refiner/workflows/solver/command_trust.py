"""Persistent trust scoring layered on top of the solver command policy.

The hard safety boundary remains in ``solver_command_policy``. A blocked
command stays blocked. This module only records how *already-allowed* command
shapes behave over time so the solver can distinguish:

- repeatably safe command patterns that keep succeeding,
- newly seen commands that deserve scrutiny, and
- previously allowed commands that are becoming unreliable.

The storage format is a small JSON file under the solver state directory. It is
deliberately deterministic and audit-friendly rather than probabilistic.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

from refiner.security_utils import ensure_dir_permissions, ensure_file_permissions
from refiner.solver_command_policy import CommandPolicyDecision


_SAFE_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,40}$")
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "blocked": 3}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _safe_text(value: object, max_chars: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "...(truncated)"


def _safe_token(value: object) -> str:
    token = str(value or "").strip().lower()
    if _SAFE_TOKEN_RE.match(token):
        return token
    return "<arg>"


def _normalize_command_shape(decision: CommandPolicyDecision) -> str:
    """Collapse concrete argv into a stable command family identifier."""

    argv = list(decision.argv or [])
    if not argv:
        return ""

    executable = os.path.basename(str(argv[0])).lower()
    parts: List[str] = [executable]
    flag_start = 1

    if executable.startswith("python") and len(argv) > 2 and argv[1] == "-m":
        parts.extend(["-m", _safe_token(argv[2])])
        flag_start = 3
    else:
        subcommands: List[str] = []
        for token in argv[1:]:
            cleaned = str(token or "").strip()
            if not cleaned or cleaned.startswith("-"):
                continue
            subcommands.append(_safe_token(cleaned))
            if executable in {"npm", "pnpm", "yarn", "uv"} and subcommands and subcommands[0] in {"run", "exec"}:
                if len(subcommands) >= 2:
                    break
                continue
            break
        parts.extend(item for item in subcommands if item)

    flags: List[str] = []
    for token in argv[flag_start:]:
        cleaned = str(token or "").strip()
        if not cleaned.startswith("-"):
            continue
        flag = cleaned.split("=", 1)[0].lower()
        if flag in flags:
            continue
        flags.append(flag)
        if len(flags) >= 3:
            break
    parts.extend(flags)
    return " ".join(part for part in parts if part).strip()


def _base_trust_score(policy_risk: str) -> float:
    return {
        "low": 0.70,
        "medium": 0.56,
        "high": 0.40,
        "blocked": 0.0,
    }.get(policy_risk, 0.50)


def _elevate_risk(policy_risk: str) -> str:
    if policy_risk == "low":
        return "medium"
    if policy_risk == "medium":
        return "high"
    return policy_risk or "high"


@dataclass(frozen=True)
class CommandTrustAssessment:
    """Compact trust verdict for one allowed command shape."""

    shape: str
    known: bool
    total_runs: int
    successes: int
    failures: int
    consecutive_failures: int
    success_rate: float
    score: float
    level: str
    category: str
    policy_risk: str
    effective_risk: str
    note: str


class CommandTrustStore:
    """JSON-backed trust history keyed by normalized command shape."""

    def __init__(self, path: str, *, max_shapes: int = 300):
        self.path = path
        self.max_shapes = max(20, int(max_shapes))
        self._payload: Dict[str, Any] = {"version": 1, "updated_at": None, "commands": {}}
        self._load()

    def _ensure_path(self) -> None:
        parent = os.path.dirname(self.path) or "."
        ensure_dir_permissions(parent, mode=0o700)
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self._payload, handle, indent=2, sort_keys=True)
        ensure_file_permissions(self.path, mode=0o600)

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8", errors="ignore") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                commands = payload.get("commands")
                self._payload = {
                    "version": int(payload.get("version") or 1),
                    "updated_at": payload.get("updated_at"),
                    "commands": commands if isinstance(commands, dict) else {},
                }
        except Exception:
            self._payload = {"version": 1, "updated_at": None, "commands": {}}

    def _write(self) -> None:
        self._ensure_path()
        parent = os.path.dirname(self.path) or "."
        fd, temp_path = tempfile.mkstemp(prefix=".command_trust_", suffix=".json", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._payload, handle, indent=2, sort_keys=True)
            os.replace(temp_path, self.path)
            ensure_file_permissions(self.path, mode=0o600)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass

    def _compact(self) -> None:
        commands = self._payload.get("commands")
        if not isinstance(commands, dict) or len(commands) <= self.max_shapes:
            return
        ranked = sorted(
            commands.items(),
            key=lambda item: (
                str((item[1] or {}).get("last_seen_at") or ""),
                int((item[1] or {}).get("total_runs") or 0),
            ),
            reverse=True,
        )
        self._payload["commands"] = dict(ranked[: self.max_shapes])

    def _entry(self, shape: str) -> Dict[str, Any]:
        commands = self._payload.setdefault("commands", {})
        if not isinstance(commands, dict):
            commands = {}
            self._payload["commands"] = commands
        entry = commands.get(shape)
        if isinstance(entry, dict):
            return entry
        entry = {
            "shape": shape,
            "category": "",
            "policy_risk": "",
            "first_seen_at": None,
            "last_seen_at": None,
            "last_outcome": None,
            "last_exit_code": None,
            "total_runs": 0,
            "successes": 0,
            "failures": 0,
            "consecutive_successes": 0,
            "consecutive_failures": 0,
        }
        commands[shape] = entry
        return entry

    def assess(self, decision: CommandPolicyDecision) -> CommandTrustAssessment:
        """Return the current trust verdict for an allowed command."""

        shape = _normalize_command_shape(decision)
        if not shape:
            return CommandTrustAssessment(
                shape="",
                known=False,
                total_runs=0,
                successes=0,
                failures=0,
                consecutive_failures=0,
                success_rate=0.0,
                score=0.0,
                level="unknown",
                category=decision.category,
                policy_risk=decision.risk,
                effective_risk=decision.risk,
                note="Unable to derive a stable command shape for trust scoring.",
            )

        commands = self._payload.get("commands")
        raw = commands.get(shape) if isinstance(commands, dict) else None
        if not isinstance(raw, dict):
            base = _base_trust_score(decision.risk)
            return CommandTrustAssessment(
                shape=shape,
                known=False,
                total_runs=0,
                successes=0,
                failures=0,
                consecutive_failures=0,
                success_rate=0.0,
                score=round(_clamp(base - 0.10), 4),
                level="new",
                category=decision.category,
                policy_risk=decision.risk,
                effective_risk=decision.risk,
                note="No prior executions recorded for this command shape.",
            )

        total_runs = max(0, int(raw.get("total_runs") or 0))
        successes = max(0, int(raw.get("successes") or 0))
        failures = max(0, int(raw.get("failures") or 0))
        consecutive_failures = max(0, int(raw.get("consecutive_failures") or 0))
        success_rate = (successes / total_runs) if total_runs else 0.0
        base = _base_trust_score(decision.risk)
        experience_bonus = min(0.12, math.log1p(total_runs) / 12.0)
        success_adjustment = (success_rate - 0.5) * 0.5
        failure_penalty = min(0.26, consecutive_failures * 0.08)
        score = round(
            _clamp(base + experience_bonus + success_adjustment - failure_penalty),
            4,
        )

        level = "caution"
        if consecutive_failures >= 2 or (total_runs >= 3 and success_rate < 0.5):
            level = "watch"
        elif total_runs >= 4 and success_rate >= 0.85 and consecutive_failures == 0 and score >= 0.80:
            level = "established"
        elif total_runs >= 2 and success_rate >= 0.70 and score >= 0.65:
            level = "steady"

        effective_risk = decision.risk
        if level == "watch":
            effective_risk = _elevate_risk(decision.risk)

        if level == "watch":
            note = "Repeated failures suggest this allowed command shape is unstable."
        elif level == "established":
            note = "This command shape has a strong recent success record."
        elif level == "steady":
            note = "This command shape has a mostly healthy recent success record."
        else:
            note = "This command shape is allowed, but the history is limited or mixed."

        return CommandTrustAssessment(
            shape=shape,
            known=True,
            total_runs=total_runs,
            successes=successes,
            failures=failures,
            consecutive_failures=consecutive_failures,
            success_rate=round(success_rate, 4),
            score=score,
            level=level,
            category=decision.category,
            policy_risk=decision.risk,
            effective_risk=effective_risk,
            note=note,
        )

    def record(
        self,
        decision: CommandPolicyDecision,
        *,
        success: bool,
        exit_code: Optional[int],
    ) -> CommandTrustAssessment:
        """Persist one execution outcome and return the updated trust view."""

        if not decision.allowed:
            return self.assess(decision)

        shape = _normalize_command_shape(decision)
        if not shape:
            return self.assess(decision)

        entry = self._entry(shape)
        now = _now_iso()
        if not entry.get("first_seen_at"):
            entry["first_seen_at"] = now
        entry["last_seen_at"] = now
        entry["category"] = decision.category
        entry["policy_risk"] = decision.risk
        entry["last_outcome"] = "success" if success else "failure"
        entry["last_exit_code"] = exit_code
        entry["total_runs"] = max(0, int(entry.get("total_runs") or 0)) + 1
        if success:
            entry["successes"] = max(0, int(entry.get("successes") or 0)) + 1
            entry["consecutive_successes"] = max(0, int(entry.get("consecutive_successes") or 0)) + 1
            entry["consecutive_failures"] = 0
        else:
            entry["failures"] = max(0, int(entry.get("failures") or 0)) + 1
            entry["consecutive_failures"] = max(0, int(entry.get("consecutive_failures") or 0)) + 1
            entry["consecutive_successes"] = 0
        self._payload["updated_at"] = now
        self._compact()
        self._write()
        return self.assess(decision)

    def snapshot(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return recent command-shape stats for replay/reporting."""

        commands = self._payload.get("commands")
        if not isinstance(commands, dict):
            return []
        rows: List[Dict[str, Any]] = []
        for raw in commands.values():
            if not isinstance(raw, dict):
                continue
            shape = _safe_text(raw.get("shape"), max_chars=120)
            if not shape:
                continue
            category = _safe_text(raw.get("category"), max_chars=40)
            policy_risk = _safe_text(raw.get("policy_risk"), max_chars=16)
            assessment = self.assess(
                CommandPolicyDecision(
                    allowed=True,
                    reason="snapshot",
                    category=category or "general",
                    risk=policy_risk or "medium",
                    command=shape,
                    argv=shape.split(),
                    env={},
                )
            )
            rows.append(
                {
                    "shape": shape,
                    "category": category,
                    "policy_risk": policy_risk,
                    "total_runs": max(0, int(raw.get("total_runs") or 0)),
                    "successes": max(0, int(raw.get("successes") or 0)),
                    "failures": max(0, int(raw.get("failures") or 0)),
                    "consecutive_failures": max(0, int(raw.get("consecutive_failures") or 0)),
                    "last_outcome": _safe_text(raw.get("last_outcome"), max_chars=16),
                    "last_exit_code": raw.get("last_exit_code"),
                    "last_seen_at": _safe_text(raw.get("last_seen_at"), max_chars=40),
                    "trust_level": assessment.level,
                    "trust_score": assessment.score,
                    "effective_risk": assessment.effective_risk,
                }
            )

        rows.sort(
            key=lambda item: (
                str(item.get("last_seen_at") or ""),
                int(item.get("total_runs") or 0),
                _RISK_ORDER.get(str(item.get("policy_risk") or ""), 1),
            ),
            reverse=True,
        )
        if limit is None or limit <= 0:
            return rows
        return rows[: int(limit)]
