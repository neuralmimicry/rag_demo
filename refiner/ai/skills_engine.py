from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from refiner.capabilities import get_skills

logger = logging.getLogger(__name__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-/]{2,}")

_DEFAULT_PLAYBOOK_PATHS = (
    os.path.join("data", "skills_playbooks.json"),
    "skills_playbooks.json",
)

_MAX_HINTS_PER_SECTION = 6

_FOCUS_KEYWORDS = {
    "project_solver": [
        "code",
        "coding",
        "dev",
        "developer",
        "testing",
        "test",
        "qa",
        "api",
        "cli",
        "database",
        "data",
        "pipeline",
        "etl",
        "backend",
        "frontend",
        "performance",
        "security",
        "refactor",
        "migration",
        "infra",
        "devops",
        "python",
        "javascript",
    ],
    "topic_research": [
        "jira",
        "confluence",
        "research",
        "analysis",
        "documentation",
        "report",
        "writing",
        "summarise",
        "summarize",
    ],
}


_HEURISTIC_HINTS: List[Dict[str, Any]] = [
    {
        "keywords": ["test", "testing", "pytest", "qa"],
        "plan_hints": [
            "Include unit tests for critical paths and edge cases.",
        ],
        "verification_hints": [
            "Add a verification command that runs the test suite covering new code.",
        ],
    },
    {
        "keywords": ["api", "rest", "graphql", "endpoint"],
        "plan_hints": [
            "Validate request inputs and return structured error responses.",
        ],
        "coding_hints": [
            "Document the request/response schema in code comments or docstrings.",
        ],
    },
    {
        "keywords": ["security", "auth", "authentication", "authorisation", "authorization"],
        "safety_hints": [
            "Validate and sanitise all external inputs; avoid logging secrets.",
        ],
        "coding_hints": [
            "Fail fast on missing credentials and provide clear error messages.",
        ],
    },
    {
        "keywords": ["performance", "latency", "throughput", "scalability", "scale"],
        "plan_hints": [
            "Avoid O(N^2) loops and consider caching expensive operations.",
        ],
        "verification_hints": [
            "Include a lightweight benchmark or profiling check where feasible.",
        ],
    },
    {
        "keywords": ["data", "etl", "pipeline", "dataset", "csv", "parquet"],
        "plan_hints": [
            "Validate schema and handle missing/invalid records explicitly.",
        ],
        "coding_hints": [
            "Provide clear error handling or quarantine logic for bad records.",
        ],
    },
    {
        "keywords": ["database", "sql", "postgres", "mysql", "sqlite"],
        "plan_hints": [
            "Use parameterised queries and avoid string interpolation for SQL.",
        ],
        "coding_hints": [
            "Plan for migrations when changing schema or indexes.",
        ],
    },
    {
        "keywords": ["cli", "command-line", "argparse"],
        "plan_hints": [
            "Add clear CLI usage/help output and validate required arguments.",
        ],
        "verification_hints": [
            "Add a verification step that runs the CLI with --help or a sample input.",
        ],
    },
    {
        "keywords": ["documentation", "docs", "readme", "report"],
        "draft_hints": [
            "Summarise changes in a concise, structured format with clear headings.",
        ],
        "plan_hints": [
            "Update README or inline docstrings for non-obvious logic.",
        ],
    },
    {
        "keywords": ["frontend", "ui", "ux", "css", "html"],
        "plan_hints": [
            "Ensure responsive layout and basic accessibility (labels, contrast).",
        ],
    },
    {
        "keywords": ["logging", "observability", "metrics", "monitoring"],
        "coding_hints": [
            "Use structured logging with consistent context fields.",
        ],
    },
]


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    for match in _TOKEN_RE.findall(text or ""):
        token = match.lower().strip()
        if len(token) < 3:
            continue
        tokens.append(token)
        for part in re.split(r"[-_/]", token):
            if len(part) >= 3:
                tokens.append(part)
    return tokens


def _skill_text(skill: Dict[str, Any]) -> str:
    parts = [
        str(skill.get("id") or ""),
        str(skill.get("name") or ""),
        str(skill.get("summary") or ""),
        str(skill.get("category") or ""),
    ]
    cues = skill.get("cues") or []
    if isinstance(cues, list):
        parts.extend([str(c) for c in cues if c])
    return " ".join(parts).lower()


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in values:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _apply_hints(target: List[str], hints: Sequence[str]) -> None:
    for hint in hints or []:
        if not hint:
            continue
        if hint not in target:
            target.append(hint)


def _load_playbooks() -> List[Dict[str, Any]]:
    path = os.getenv("REFINER_SKILLS_PLAYBOOK_PATH")
    candidates = [path] if path else []
    candidates.extend(_DEFAULT_PLAYBOOK_PATHS)
    for candidate in candidates:
        if not candidate:
            continue
        if not os.path.isabs(candidate):
            candidate_path = os.path.join(PROJECT_ROOT, candidate)
        else:
            candidate_path = candidate
        if not os.path.isfile(candidate_path):
            continue
        try:
            with open(candidate_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return data
        except Exception as exc:
            logger.debug("Failed to load skills playbook %s: %s", candidate_path, exc)
            continue
    return []


def _matches_playbook(skill: Dict[str, Any], entry: Dict[str, Any]) -> bool:
    match = entry.get("match") if isinstance(entry, dict) else None
    if not isinstance(match, dict):
        return False
    skill_id = str(skill.get("id") or "")
    name = str(skill.get("name") or "")
    category = str(skill.get("category") or "")
    text = _skill_text(skill)
    ids = match.get("ids") or []
    if isinstance(ids, list) and skill_id and skill_id in ids:
        return True
    prefixes = match.get("id_prefixes") or []
    if isinstance(prefixes, list) and skill_id:
        for prefix in prefixes:
            if skill_id.startswith(str(prefix)):
                return True
    categories = match.get("categories") or []
    if isinstance(categories, list) and category:
        for cat in categories:
            if category.lower() == str(cat).lower():
                return True
    keywords = match.get("keywords") or []
    if isinstance(keywords, list):
        for keyword in keywords:
            if keyword and str(keyword).lower() in text:
                return True
    names = match.get("names") or []
    if isinstance(names, list) and name:
        for n in names:
            if name.lower() == str(n).lower():
                return True
    return False


def _derive_hints_from_playbooks(skills: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    playbooks = _load_playbooks()
    hints: Dict[str, List[str]] = {
        "plan_hints": [],
        "coding_hints": [],
        "query_hints": [],
        "verification_hints": [],
        "draft_hints": [],
        "safety_hints": [],
    }
    if not playbooks:
        return hints
    for skill in skills:
        for entry in playbooks:
            if not isinstance(entry, dict):
                continue
            if not _matches_playbook(skill, entry):
                continue
            directives = entry.get("directives") if isinstance(entry.get("directives"), dict) else {}
            for key in hints.keys():
                _apply_hints(hints[key], directives.get(key) or [])
    return hints


def _derive_hints_from_heuristics(skills: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    hints: Dict[str, List[str]] = {
        "plan_hints": [],
        "coding_hints": [],
        "query_hints": [],
        "verification_hints": [],
        "draft_hints": [],
        "safety_hints": [],
    }
    for skill in skills:
        text = _skill_text(skill)
        for rule in _HEURISTIC_HINTS:
            keywords = rule.get("keywords") or []
            if not any(keyword in text for keyword in keywords):
                continue
            for key in hints.keys():
                _apply_hints(hints[key], rule.get(key) or [])
        risk = str(skill.get("risk") or "").lower()
        if risk in {"high", "critical"}:
            _apply_hints(
                hints["safety_hints"],
                ["Treat high-risk skills cautiously and avoid unsafe actions without explicit approval."],
            )
    return hints


def _merge_hints(primary: Dict[str, List[str]], secondary: Dict[str, List[str]]) -> Dict[str, List[str]]:
    merged: Dict[str, List[str]] = {key: list(primary.get(key) or []) for key in primary.keys()}
    for key, items in secondary.items():
        _apply_hints(merged.setdefault(key, []), items or [])
    return merged


def _cap_hints(hints: Dict[str, List[str]]) -> Dict[str, List[str]]:
    capped: Dict[str, List[str]] = {}
    for key, items in hints.items():
        capped[key] = list(_dedupe(items))[:_MAX_HINTS_PER_SECTION]
    return capped


def _focus_boost(skill: Dict[str, Any], focus: Optional[str]) -> int:
    if not focus:
        return 0
    keywords = _FOCUS_KEYWORDS.get(focus)
    if not keywords:
        return 0
    text = _skill_text(skill)
    return 2 if any(keyword in text for keyword in keywords) else 0


def select_skills(
    text: str,
    *,
    limit: int = 6,
    focus: Optional[str] = None,
) -> List[Dict[str, Any]]:
    tokens = set(_tokenize(text))
    if not tokens:
        return []
    skills = get_skills()
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for skill in skills:
        skill_tokens = _tokenize(_skill_text(skill))
        if not skill_tokens:
            continue
        score = len(tokens & set(skill_tokens))
        if score <= 0:
            continue
        score += _focus_boost(skill, focus)
        scored.append((score, skill))
    scored.sort(key=lambda item: (-item[0], item[1].get("id") or item[1].get("name") or ""))
    return [dict(item[1]) for item in scored[:limit]]


@dataclass
class SkillContext:
    skills: List[Dict[str, Any]] = field(default_factory=list)
    plan_hints: List[str] = field(default_factory=list)
    coding_hints: List[str] = field(default_factory=list)
    query_hints: List[str] = field(default_factory=list)
    verification_hints: List[str] = field(default_factory=list)
    draft_hints: List[str] = field(default_factory=list)
    safety_hints: List[str] = field(default_factory=list)


def build_skill_context(
    *parts: str,
    focus: Optional[str] = None,
    limit: int = 6,
) -> SkillContext:
    text = "\n".join([p for p in parts if p and p.strip()])
    if not text:
        return SkillContext()
    skills = select_skills(text, limit=limit, focus=focus)
    if not skills:
        return SkillContext()
    playbook_hints = _derive_hints_from_playbooks(skills)
    heuristic_hints = _derive_hints_from_heuristics(skills)
    merged = _merge_hints(playbook_hints, heuristic_hints)
    merged = _cap_hints(merged)
    return SkillContext(
        skills=skills,
        plan_hints=merged.get("plan_hints", []),
        coding_hints=merged.get("coding_hints", []),
        query_hints=merged.get("query_hints", []),
        verification_hints=merged.get("verification_hints", []),
        draft_hints=merged.get("draft_hints", []),
        safety_hints=merged.get("safety_hints", []),
    )


def format_skill_directives(
    context: SkillContext,
    *,
    sections: Sequence[str],
    include_skills: bool = True,
) -> str:
    if not context or not context.skills:
        return ""
    lines: List[str] = []
    if include_skills:
        skill_names = [skill.get("name") or skill.get("id") for skill in context.skills if skill]
        skill_names = [s for s in skill_names if s]
        if skill_names:
            lines.append("Selected skills:")
            lines.extend([f"- {name}" for name in skill_names[:_MAX_HINTS_PER_SECTION]])
    section_map = {
        "plan_hints": ("Plan directives:", context.plan_hints),
        "coding_hints": ("Coding directives:", context.coding_hints),
        "query_hints": ("Query directives:", context.query_hints),
        "verification_hints": ("Verification directives:", context.verification_hints),
        "draft_hints": ("Drafting directives:", context.draft_hints),
        "safety_hints": ("Safety directives:", context.safety_hints),
    }
    for section in sections:
        title, items = section_map.get(section, ("", []))
        if not items:
            continue
        if title:
            lines.append(title)
        lines.extend([f"- {item}" for item in items])
    return "\n".join(lines).strip()
