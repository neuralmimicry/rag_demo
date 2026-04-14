"""Post-run replay analysis for recent solver episodes.

The solver already writes a detailed JSON report for the current run. Replay
analysis adds a second view over *recent history* so repeated loops stand out:

- which sources keep failing or being deferred,
- which verification issues recur,
- which prompt sections are frequently dropped under budget pressure, and
- which command shapes are currently unstable.

The output is intentionally compact and JSON-friendly so it can be attached to
the final solver result without overwhelming downstream tooling.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from solver_command_trust import CommandTrustStore
from solver_memory import SolverEpisodeStore


def _safe_list(value: object) -> List[object]:
    if isinstance(value, list):
        return value
    return []


def _safe_dict(value: object) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _safe_text(value: object, max_chars: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "...(truncated)"


def build_solver_replay_analysis(
    episode_store: SolverEpisodeStore,
    *,
    command_trust_store: Optional[CommandTrustStore] = None,
    limit: int = 60,
) -> Dict[str, Any]:
    """Summarize recent solver history into a compact replay report."""

    episodes = episode_store.snapshot(limit=max(1, int(limit)))
    if not episodes:
        return {
            "window": {"episodes_analyzed": 0, "sources_analyzed": 0, "limit": max(1, int(limit))},
            "outcomes": {},
            "sources_needing_attention": [],
            "top_verification_failures": [],
            "prompt_budget": {
                "episodes_with_omissions": 0,
                "top_omitted_sections": [],
            },
            "command_patterns": [],
            "recommendations": ["No recent solver episodes were available for replay analysis."],
        }

    outcome_counts: Counter[str] = Counter()
    verification_counts: Counter[str] = Counter()
    omitted_counts: Counter[str] = Counter()
    omitted_before_failure: Counter[str] = Counter()
    command_stats: Dict[str, Dict[str, Any]] = {}
    episodes_by_source: Dict[str, List[Any]] = {}
    episodes_with_omissions = 0

    for episode in episodes:
        outcome = _safe_text(episode.outcome, max_chars=32) or "unknown"
        outcome_counts[outcome] += 1
        episodes_by_source.setdefault(episode.source_path, []).append(episode)

        for issue in episode.verification_failures:
            cleaned_issue = _safe_text(issue, max_chars=200)
            if cleaned_issue:
                verification_counts[cleaned_issue] += 1

        metadata = _safe_dict(getattr(episode, "metadata", {}) or {})
        prompt_budget = _safe_dict(metadata.get("prompt_budget") or {})
        omitted_sections = [
            _safe_text(item, max_chars=80)
            for item in _safe_list(prompt_budget.get("omitted_sections") or [])
            if _safe_text(item, max_chars=80)
        ]
        if omitted_sections:
            episodes_with_omissions += 1
        for section in omitted_sections:
            omitted_counts[section] += 1
            if episode.outcome in {"failure", "deferred"} or episode.verification_failures:
                omitted_before_failure[section] += 1

        for raw in _safe_list(metadata.get("command_results") or []):
            item = _safe_dict(raw)
            shape = _safe_text(item.get("shape") or item.get("command"), max_chars=120)
            if not shape:
                continue
            entry = command_stats.setdefault(
                shape,
                {
                    "shape": shape,
                    "runs": 0,
                    "successes": 0,
                    "failures": 0,
                    "category": _safe_text(item.get("category"), max_chars=32),
                    "policy_risk": _safe_text(item.get("policy_risk"), max_chars=16),
                    "effective_risk": _safe_text(item.get("effective_risk"), max_chars=16),
                    "trust_level": _safe_text(item.get("trust_level"), max_chars=24),
                    "trust_score": item.get("trust_score"),
                },
            )
            entry["runs"] += 1
            if bool(item.get("success")):
                entry["successes"] += 1
            else:
                entry["failures"] += 1
            if item.get("effective_risk"):
                entry["effective_risk"] = _safe_text(item.get("effective_risk"), max_chars=16)
            if item.get("trust_level"):
                entry["trust_level"] = _safe_text(item.get("trust_level"), max_chars=24)
            if item.get("trust_score") is not None:
                try:
                    entry["trust_score"] = round(float(item.get("trust_score")), 4)
                except Exception:
                    pass

    trust_snapshot_by_shape: Dict[str, Dict[str, Any]] = {}
    if command_trust_store is not None:
        for item in command_trust_store.snapshot(limit=200):
            shape = _safe_text(item.get("shape"), max_chars=120)
            if shape:
                trust_snapshot_by_shape[shape] = item

    command_patterns: List[Dict[str, Any]] = []
    for shape, entry in command_stats.items():
        trust_row = trust_snapshot_by_shape.get(shape, {})
        runs = int(entry.get("runs") or 0)
        failures = int(entry.get("failures") or 0)
        failure_rate = round((failures / runs), 4) if runs else 0.0
        if trust_row and not entry.get("trust_level"):
            entry["trust_level"] = _safe_text(trust_row.get("trust_level"), max_chars=24)
        if trust_row and entry.get("trust_score") is None and trust_row.get("trust_score") is not None:
            try:
                entry["trust_score"] = round(float(trust_row.get("trust_score")), 4)
            except Exception:
                pass
        command_patterns.append(
            {
                "shape": shape,
                "runs": runs,
                "successes": int(entry.get("successes") or 0),
                "failures": failures,
                "failure_rate": failure_rate,
                "category": entry.get("category"),
                "policy_risk": entry.get("policy_risk"),
                "effective_risk": entry.get("effective_risk"),
                "trust_level": entry.get("trust_level")
                or _safe_text(trust_row.get("last_outcome"), max_chars=24),
                "trust_score": entry.get("trust_score"),
                "consecutive_failures": int(trust_row.get("consecutive_failures") or 0),
            }
        )

    command_patterns.sort(
        key=lambda item: (
            int(item.get("failures") or 0),
            float(item.get("failure_rate") or 0.0),
            int(item.get("runs") or 0),
        ),
        reverse=True,
    )

    attention_rows: List[Dict[str, Any]] = []
    for source_path, source_episodes in episodes_by_source.items():
        tail = source_episodes[-3:]
        recent_non_success = sum(
            1
            for item in tail
            if item.outcome in {"failure", "deferred"} or item.verification_failures
        )
        last_episode = tail[-1]
        if recent_non_success == 0 or last_episode.outcome == "success":
            continue
        attention_rows.append(
            {
                "source_path": source_path,
                "recent_non_successes": recent_non_success,
                "last_outcome": _safe_text(last_episode.outcome, max_chars=24),
                "last_iteration": int(last_episode.iteration or 0),
                "last_summary": _safe_text(last_episode.summary, max_chars=220),
            }
        )

    attention_rows.sort(
        key=lambda item: (
            int(item.get("recent_non_successes") or 0),
            int(item.get("last_iteration") or 0),
        ),
        reverse=True,
    )

    top_omitted_sections = [
        {
            "section": section,
            "count": count,
            "failure_related": int(omitted_before_failure.get(section, 0)),
        }
        for section, count in omitted_counts.most_common(6)
    ]
    top_verification_failures = [
        {"issue": issue, "count": count}
        for issue, count in verification_counts.most_common(6)
    ]

    recommendations: List[str] = []
    if attention_rows:
        worst = attention_rows[0]
        recommendations.append(
            f"Review '{worst['source_path']}' first; it has {worst['recent_non_successes']} recent non-success episodes."
        )
    if top_verification_failures:
        item = top_verification_failures[0]
        recommendations.append(
            f"Stabilize the recurring verification failure '{item['issue']}' before adding new work."
        )
    if top_omitted_sections and top_omitted_sections[0]["failure_related"] > 0:
        item = top_omitted_sections[0]
        recommendations.append(
            f"Prompt budget pressure is dropping '{item['section']}' before failures; compress lower-value context or raise the budget."
        )
    if command_patterns and float(command_patterns[0].get("failure_rate") or 0.0) >= 0.5:
        item = command_patterns[0]
        recommendations.append(
            f"Command shape '{item['shape']}' is unreliable ({item['failures']}/{item['runs']} failures); tighten recovery or verification around it."
        )
    if not recommendations:
        recommendations.append("Recent solver history is stable; no repeated loops stood out in the replay window.")

    return {
        "window": {
            "episodes_analyzed": len(episodes),
            "sources_analyzed": len(episodes_by_source),
            "limit": max(1, int(limit)),
        },
        "outcomes": dict(outcome_counts),
        "sources_needing_attention": attention_rows[:6],
        "top_verification_failures": top_verification_failures,
        "prompt_budget": {
            "episodes_with_omissions": episodes_with_omissions,
            "top_omitted_sections": top_omitted_sections,
        },
        "command_patterns": command_patterns[:6],
        "recommendations": recommendations[:5],
    }


def build_solver_feedback_prompt(
    episode_store: SolverEpisodeStore,
    *,
    command_trust_store: Optional[CommandTrustStore] = None,
    query_text: str = "",
    source_path: Optional[str] = None,
    requirement_ids: Optional[Sequence[str]] = None,
    limit: int = 4,
    max_chars: int = 1800,
    header: str = "Recent solver feedback for similar work (use this to avoid repeating unstable patterns):",
) -> str:
    """Format compact operational guidance for the next solver iteration.

    This complements episodic memory instead of replacing it. Episodic memory
    captures *what* changed and why; this helper highlights *how* similar work
    has recently gone wrong so the next prompt can steer away from known traps.
    """

    episodes = episode_store.search(
        query_text,
        source_path=source_path,
        requirement_ids=requirement_ids,
        limit=max(1, int(limit)),
    )
    if not episodes:
        return ""

    ordered = sorted(
        episodes,
        key=lambda item: (_safe_text(getattr(item, "created_at", ""), max_chars=40), int(item.iteration or 0)),
        reverse=True,
    )
    recent_window = ordered[:3]
    verification_counts: Counter[str] = Counter()
    omitted_counts: Counter[str] = Counter()
    command_failures: Dict[str, Dict[str, Any]] = {}

    for episode in episodes:
        for issue in episode.verification_failures:
            cleaned_issue = _safe_text(issue, max_chars=180)
            if cleaned_issue:
                verification_counts[cleaned_issue] += 1

        metadata = _safe_dict(getattr(episode, "metadata", {}) or {})
        prompt_budget = _safe_dict(metadata.get("prompt_budget") or {})
        if episode.outcome in {"failure", "deferred"} or episode.verification_failures:
            for item in _safe_list(prompt_budget.get("omitted_sections") or []):
                section = _safe_text(item, max_chars=80)
                if section:
                    omitted_counts[section] += 1

        for raw in _safe_list(metadata.get("command_results") or []):
            item = _safe_dict(raw)
            shape = _safe_text(item.get("shape") or item.get("command"), max_chars=120)
            if not shape:
                continue
            stats = command_failures.setdefault(
                shape,
                {
                    "shape": shape,
                    "runs": 0,
                    "failures": 0,
                    "trust_level": _safe_text(item.get("trust_level"), max_chars=24),
                    "effective_risk": _safe_text(item.get("effective_risk") or item.get("policy_risk"), max_chars=16),
                },
            )
            stats["runs"] += 1
            if not bool(item.get("success")):
                stats["failures"] += 1
            if item.get("trust_level"):
                stats["trust_level"] = _safe_text(item.get("trust_level"), max_chars=24)
            if item.get("effective_risk") or item.get("policy_risk"):
                stats["effective_risk"] = _safe_text(
                    item.get("effective_risk") or item.get("policy_risk"),
                    max_chars=16,
                )

    trust_snapshot_by_shape: Dict[str, Dict[str, Any]] = {}
    if command_trust_store is not None:
        for item in command_trust_store.snapshot(limit=80):
            shape = _safe_text(item.get("shape"), max_chars=120)
            if shape:
                trust_snapshot_by_shape[shape] = item

    lines: List[str] = [header]
    latest = ordered[0]
    recent_non_success = sum(
        1
        for item in recent_window
        if item.outcome in {"failure", "deferred"} or item.verification_failures
    )
    if recent_non_success:
        lines.append(
            f"- {recent_non_success} of the last {len(recent_window)} similar runs ended in failure/deferred or hit verification breakage."
        )

    if latest.outcome != "success" or latest.verification_failures:
        summary = _safe_text(latest.summary, max_chars=220)
        suffix = f" Summary: {summary}" if summary else ""
        lines.append(
            f"- Latest similar outcome for '{_safe_text(latest.source_path, max_chars=160)}' was "
            f"{_safe_text(latest.outcome, max_chars=24)} at iteration {int(latest.iteration or 0)}.{suffix}"
        )

    if verification_counts:
        issue, count = verification_counts.most_common(1)[0]
        lines.append(f"- Recurring verification issue: '{issue}' ({count} recent occurrence(s)).")

    if omitted_counts:
        section, count = omitted_counts.most_common(1)[0]
        lines.append(
            f"- Prompt budget dropped '{section}' before non-success runs {count} time(s); keep that context compact instead of losing it entirely."
        )

    command_rows: List[Dict[str, Any]] = []
    for shape, item in command_failures.items():
        if int(item.get("failures") or 0) <= 0:
            continue
        trust_row = trust_snapshot_by_shape.get(shape, {})
        command_rows.append(
            {
                "shape": shape,
                "runs": int(item.get("runs") or 0),
                "failures": int(item.get("failures") or 0),
                "trust_level": _safe_text(
                    trust_row.get("trust_level") or item.get("trust_level"),
                    max_chars=24,
                )
                or "unknown",
                "effective_risk": _safe_text(
                    trust_row.get("effective_risk") or item.get("effective_risk"),
                    max_chars=16,
                )
                or "medium",
            }
        )
    command_rows.sort(
        key=lambda item: (
            int(item.get("failures") or 0),
            int(item.get("runs") or 0),
        ),
        reverse=True,
    )
    if command_rows:
        item = command_rows[0]
        lines.append(
            f"- Treat command shape '{item['shape']}' carefully: {item['failures']}/{item['runs']} recent failure(s), "
            f"trust {item['trust_level']}, effective risk {item['effective_risk']}."
        )

    if len(lines) == 1:
        return ""

    text = "\n".join(lines).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "...(truncated)"
