"""Capability inventory and skill-selection helpers for Refiner.

The module combines:
- static workflow metadata,
- optional repository capability analysis, and
- local/external skills catalog loading with lightweight filtering.

It powers `/api/capabilities` responses and skill recommendation UX.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from capability_analyzer import analyse_repo

logger = logging.getLogger(__name__)


_WORKFLOWS: List[Dict[str, Any]] = [
    {
        "id": "jira_stats",
        "name": "Jira statistics",
        "description": "Discover scope and generate CSV/HTML throughput and timeline reports.",
        "triggers": ["default", "--analyze-jira off"],
        "outputs": ["monthly CSVs", "leaderboard", "timelines.csv"],
    },
    {
        "id": "jira_quality",
        "name": "Jira quality analysis",
        "description": "LLM-assisted issue quality analysis with concurrent multi-provider review and optional comment posting.",
        "triggers": ["--analyze-jira"],
        "outputs": ["jira_report.html"],
    },
    {
        "id": "confluence_analysis",
        "name": "Confluence space analysis",
        "description": "Space/page hierarchy analysis with optional LLM/Rovo insights and concurrent provider routing.",
        "triggers": ["--analyze-confluence"],
        "outputs": ["confluence_report.html"],
    },
    {
        "id": "topic_research",
        "name": "Topic research",
        "description": "Iterative research with Jira/Confluence context, optional web search, and role-based provider orchestration.",
        "triggers": ["--topic-research"],
        "outputs": ["researched_document.md"],
    },
    {
        "id": "project_solver",
        "name": "Project solver",
        "description": "Extract requirements, plan with concurrent multi-engine candidates, and optionally apply code changes with an agentic loop.",
        "triggers": ["--project"],
        "outputs": ["project_solution.json", "workspace edits"],
    },
    {
        "id": "delivery_pipeline",
        "name": "Delivery pipeline",
        "description": "Multi-stage pipeline from sandbox to deploy with approvals, artifacts, and project-solver orchestration telemetry.",
        "triggers": ["--delivery"],
        "outputs": ["delivery_pipeline_output/*.json"],
    },
]


_SKILLS: List[Dict[str, Any]] = [
    {
        "id": "requirements_drafting",
        "name": "Requirements drafting",
        "summary": "Create or refine structured requirements with REQ IDs and acceptance criteria.",
        "cues": ["requirements", "acceptance", "scope", "non-functional", "REQ"],
        "best_for": ["Drafting a requirements register", "Clarifying scope"],
    },
    {
        "id": "project_planning",
        "name": "Project planning",
        "summary": "Turn requirements into a concrete plan and execution steps.",
        "cues": ["plan", "milestone", "implementation", "steps", "roadmap"],
        "best_for": ["Break down work", "Sequence tasks"],
    },
    {
        "id": "repo_context_rag",
        "name": "Repository context RAG",
        "summary": "Retrieve relevant unstructured context from indexed documents or code.",
        "cues": ["docs", "context", "spec", "search", "retrieve", "RAG"],
        "best_for": ["Answering questions about docs or code"],
    },
    {
        "id": "mcp_tooling",
        "name": "MCP tool calls",
        "summary": "Query structured data or perform actions via MCP servers.",
        "cues": ["MCP", "tool", "action", "ticket", "update", "server"],
        "best_for": ["Live data", "External system actions"],
    },
    {
        "id": "quality_analysis",
        "name": "Quality analysis",
        "summary": "Assess Jira/Confluence quality and generate reports.",
        "cues": ["Jira", "Confluence", "analysis", "quality", "report"],
        "best_for": ["Content or issue quality review"],
    },
    {
        "id": "delivery_pipeline",
        "name": "Delivery pipeline",
        "summary": "Manage multi-stage delivery with approvals and artifact capture.",
        "cues": ["deploy", "pipeline", "staging", "uat", "release"],
        "best_for": ["Delivery orchestration"],
    },
]

_SKILL_INDEX_ENV_VARS = (
    "REFINER_SKILLS_INDEX_PATH",
    "ANTIGRAVITY_SKILLS_INDEX_PATH",
    "ANTIGRAVITY_SKILLS_INDEX",
)
_SKILL_INDEX_DEFAULT_PATHS = (
    "skills_index.json",
    os.path.join("data", "skills_index.json"),
    os.path.join("data", "antigravity_skills_index.json"),
)
_ANALYSIS_REPORT_ENV_VARS = (
    "REFINER_ANALYSIS_REPORT_PATH",
    "REFINER_CAPABILITIES_REPORT_PATH",
)
_ANALYSIS_REPORT_DEFAULT_PATHS = (
    os.path.join("data", "capabilities_report.json"),
)
_DEFAULT_DENYLIST = [
    "red-team",
    "red team",
    "phish",
    "malware",
    "ransomware",
    "botnet",
    "keylogger",
    "credential harvest",
    "credential-harvest",
    "credential",
    "exploit",
    "reverse-engineer",
    "reverse engineer",
    "payload",
    "backdoor",
    "c2",
    "ddos",
]
_DEFAULT_SYNC_URL = "https://github.com/sickn33/antigravity-awesome-skills.git"
_DEFAULT_SYNC_REF = "main"
_DEFAULT_SYNC_TTL_HOURS = 24


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-/]{2,}")

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_CAPABILITY_CACHE: Dict[str, Any] = {"ts": 0.0, "report": None}
_EXTERNAL_SKILLS_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "skills": []}
_SKILLS_SYNC_CACHE: Dict[str, Any] = {"last_attempt": 0.0, "last_success": 0.0}
_SKILLS_SYNC_LOCK = threading.Lock()


def _split_csv(value: str) -> List[str]:
    """Split comma-separated config/env values into normalized tokens."""
    return [item.strip() for item in value.split(",") if item and item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable with robust truthy parsing."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with fallback default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _load_skills_config() -> Dict[str, Any]:
    """Load ``skills_catalog`` settings from Refiner config when present."""
    cfg_path = os.getenv("REFINER_CONFIG_PATH") or os.getenv("SOLVER_CONFIG_PATH") or "config.json"
    if not os.path.isabs(cfg_path):
        cfg_path = os.path.join(_REPO_ROOT, cfg_path)
    try:
        with open(cfg_path, "r", encoding="utf-8") as handle:
            cfg = json.load(handle)
    except Exception:
        return {}
    if isinstance(cfg, dict):
        catalog = cfg.get("skills_catalog")
        if isinstance(catalog, dict):
            return catalog
    return {}


def _resolve_candidate_roots() -> List[str]:
    """Return likely local skill directories to probe for index files."""
    home = os.path.expanduser("~")
    return [
        os.path.join(_REPO_ROOT, ".agent", "skills"),
        os.path.join(_REPO_ROOT, ".agents", "skills"),
        os.path.join(_REPO_ROOT, ".kiro", "skills"),
        os.path.join(_REPO_ROOT, ".claude", "skills"),
        os.path.join(_REPO_ROOT, ".gemini", "skills"),
        os.path.join(_REPO_ROOT, ".codex", "skills"),
        os.path.join(_REPO_ROOT, ".cursor", "skills"),
        os.path.join(home, ".gemini", "antigravity", "skills"),
        os.path.join(home, ".kiro", "skills"),
        os.path.join(home, ".claude", "skills"),
        os.path.join(home, ".gemini", "skills"),
        os.path.join(home, ".codex", "skills"),
        os.path.join(home, ".cursor", "skills"),
        os.path.join(home, ".agents", "skills"),
    ]


def _normalize_index_path(candidate: str) -> Optional[str]:
    """Normalize an index path candidate and return existing path if valid."""
    if not candidate:
        return None
    candidate = candidate.strip()
    if not candidate:
        return None
    if not os.path.isabs(candidate):
        candidate = os.path.join(_REPO_ROOT, candidate)
    if os.path.isdir(candidate):
        candidate = os.path.join(candidate, "skills_index.json")
    return candidate if os.path.exists(candidate) else None


def _normalize_analysis_report_path(candidate: str) -> Optional[str]:
    """Normalize a bundled analysis report path candidate."""
    if not candidate:
        return None
    candidate = candidate.strip()
    if not candidate:
        return None
    if not os.path.isabs(candidate):
        candidate = os.path.join(_REPO_ROOT, candidate)
    return candidate if os.path.exists(candidate) else None


def _resolve_analysis_report_path() -> Optional[str]:
    """Resolve the optional precomputed capability analysis report path."""
    for env_name in _ANALYSIS_REPORT_ENV_VARS:
        env_value = os.getenv(env_name)
        if env_value:
            resolved = _normalize_analysis_report_path(env_value)
            if resolved:
                return resolved
    for candidate in _ANALYSIS_REPORT_DEFAULT_PATHS:
        resolved = _normalize_analysis_report_path(candidate)
        if resolved:
            return resolved
    return None


def _load_bundled_analysis_report() -> Optional[Dict[str, Any]]:
    """Load a bundled analysis snapshot when runtime source files are absent."""
    path = _resolve_analysis_report_path()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except Exception:
        return None
    return report if isinstance(report, dict) else None


def _analysis_needs_fallback(report: Dict[str, Any]) -> bool:
    """Return True when live source analysis is unavailable or incomplete."""
    if not isinstance(report, dict):
        return True
    if report.get("error"):
        return True
    api = report.get("api") if isinstance(report.get("api"), dict) else {}
    routes = api.get("routes") if isinstance(api.get("routes"), list) else []
    try:
        files_scanned = int(report.get("files_scanned") or 0)
    except Exception:
        files_scanned = 0
    try:
        total_routes = int(api.get("total_routes") or len(routes))
    except Exception:
        total_routes = len(routes)
    return files_scanned == 0 or total_routes == 0


def _resolve_skill_index_path(cfg: Dict[str, Any]) -> Optional[str]:
    """Resolve skills index path from env/config/default candidate paths."""
    for env_name in _SKILL_INDEX_ENV_VARS:
        env_value = os.getenv(env_name)
        if env_value:
            resolved = _normalize_index_path(env_value)
            if resolved:
                return resolved
    cfg_path = cfg.get("index_path") if isinstance(cfg, dict) else None
    if isinstance(cfg_path, str) and cfg_path.strip():
        resolved = _normalize_index_path(cfg_path)
        if resolved:
            return resolved
    for candidate in _SKILL_INDEX_DEFAULT_PATHS:
        resolved = _normalize_index_path(candidate)
        if resolved:
            return resolved
    for root in _resolve_candidate_roots():
        resolved = _normalize_index_path(root)
        if resolved:
            return resolved
    return None


def _resolve_sync_path(cfg: Dict[str, Any]) -> str:
    """Resolve where external skill repositories should be synced locally."""
    env_path = os.getenv("REFINER_SKILLS_SYNC_PATH")
    if env_path:
        return env_path
    for env_name in _SKILL_INDEX_ENV_VARS:
        env_value = os.getenv(env_name)
        if env_value:
            if env_value.endswith("skills_index.json"):
                return os.path.dirname(env_value)
            return env_value
    cfg_path = cfg.get("sync_path") if isinstance(cfg, dict) else None
    if isinstance(cfg_path, str) and cfg_path.strip():
        return cfg_path.strip()
    index_path = cfg.get("index_path") if isinstance(cfg, dict) else None
    if isinstance(index_path, str) and index_path.strip():
        if index_path.endswith("skills_index.json"):
            return os.path.dirname(index_path)
        return index_path.strip()
    return os.path.join(_REPO_ROOT, ".agent", "skills")


def _sync_state_path(repo_path: str) -> str:
    """Return path to the local sync state file."""
    return os.path.join(repo_path, ".skills_sync.json")


def _load_sync_state(repo_path: str) -> Dict[str, Any]:
    """Load last sync metadata for the external skills repo."""
    path = _sync_state_path(repo_path)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_sync_state(repo_path: str, payload: Dict[str, Any]) -> None:
    """Persist sync metadata for external skills repo operations."""
    path = _sync_state_path(repo_path)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        return


def _git_available() -> bool:
    """Return whether ``git`` is available on PATH."""
    return shutil.which("git") is not None


def _git_cmd(args: List[str], cwd: Optional[str] = None, timeout: int = 30) -> Tuple[bool, str]:
    """Run a git command and return ``(ok, combined_output)``."""
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return True, output.strip()
    except Exception as exc:
        return False, str(exc)


def _sync_repo(cfg: Dict[str, Any]) -> None:
    """Best-effort clone/fetch of external skills catalog repository."""
    auto_sync = _env_bool("REFINER_SKILLS_AUTO_SYNC", bool(cfg.get("auto_sync", True)))
    if not auto_sync:
        return
    if not _git_available():
        logger.debug("git not available; skipping skills sync")
        return
    ttl_hours = _env_int(
        "REFINER_SKILLS_SYNC_TTL_HOURS",
        int(cfg.get("sync_ttl_hours") or _DEFAULT_SYNC_TTL_HOURS),
    )
    if ttl_hours <= 0:
        ttl_hours = _DEFAULT_SYNC_TTL_HOURS
    now = time.time()
    if now - _SKILLS_SYNC_CACHE.get("last_attempt", 0.0) < 30:
        return

    repo_url = os.getenv("REFINER_SKILLS_SYNC_URL") or cfg.get("sync_repo_url") or _DEFAULT_SYNC_URL
    repo_ref = os.getenv("REFINER_SKILLS_SYNC_REF") or cfg.get("sync_ref") or _DEFAULT_SYNC_REF

    existing_index = _resolve_skill_index_path(cfg)
    repo_path = None
    if existing_index:
        repo_path = os.path.dirname(existing_index)
    if not repo_path:
        repo_path = _resolve_sync_path(cfg)
    if not os.path.isabs(repo_path):
        repo_path = os.path.join(_REPO_ROOT, repo_path)
    with _SKILLS_SYNC_LOCK:
        _SKILLS_SYNC_CACHE["last_attempt"] = now
        if os.path.isdir(repo_path) and os.path.isdir(os.path.join(repo_path, ".git")):
            state = _load_sync_state(repo_path)
            last_sync = float(state.get("last_sync", 0.0))
            if (now - last_sync) < ttl_hours * 3600:
                return
            ok, output = _git_cmd(["git", "-C", repo_path, "fetch", "--prune", "--tags"], timeout=60)
            if not ok:
                logger.debug("skills repo fetch failed: %s", output)
                return
            if repo_ref:
                _git_cmd(["git", "-C", repo_path, "checkout", repo_ref])
            ok, output = _git_cmd(["git", "-C", repo_path, "pull", "--ff-only"], timeout=60)
            if ok:
                _write_sync_state(repo_path, {"last_sync": now})
                _SKILLS_SYNC_CACHE["last_success"] = now
            else:
                logger.debug("skills repo pull failed: %s", output)
            return
        if os.path.exists(repo_path) and not os.path.isdir(repo_path):
            logger.debug("skills sync path exists and is not a directory: %s", repo_path)
            return
        if os.path.isdir(repo_path) and not os.path.isdir(os.path.join(repo_path, ".git")):
            logger.debug("skills sync path exists but is not a git repo: %s", repo_path)
            return
        os.makedirs(os.path.dirname(repo_path), exist_ok=True)
        args = ["git", "clone", "--depth", "1"]
        if repo_ref:
            args.extend(["--branch", repo_ref])
        args.extend([repo_url, repo_path])
        ok, output = _git_cmd(args, timeout=120)
        if ok:
            _write_sync_state(repo_path, {"last_sync": now})
            _SKILLS_SYNC_CACHE["last_success"] = now
        else:
            logger.debug("skills repo clone failed: %s", output)


def _normalize_external_skill(entry: Dict[str, Any], summary_max_chars: int) -> Optional[Dict[str, Any]]:
    """Normalize one external skill record into Refiner's runtime schema."""
    if not isinstance(entry, dict):
        return None
    skill_id = str(entry.get("id") or "").strip()
    name = str(entry.get("name") or skill_id).strip()
    if not skill_id and not name:
        return None
    summary = str(entry.get("description") or "").strip()
    if summary_max_chars and len(summary) > summary_max_chars:
        summary = summary[:summary_max_chars].rstrip() + "..."
    cues: List[str] = []
    for value in (entry.get("category"), entry.get("id"), entry.get("name"), entry.get("path")):
        if value:
            cues.append(str(value))
    return {
        "id": skill_id or name,
        "name": name or skill_id,
        "summary": summary,
        "cues": cues,
        "source": entry.get("source"),
        "category": entry.get("category"),
        "risk": entry.get("risk"),
        "path": entry.get("path"),
        "external": True,
    }


def _load_external_skills(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Load and sanitize external skills from index JSON (if configured)."""
    cfg = _load_skills_config()
    _sync_repo(cfg)
    index_path = _resolve_skill_index_path(cfg)
    if not index_path:
        return []
    if not os.path.isabs(index_path):
        index_path = os.path.join(_REPO_ROOT, index_path)
    if not os.path.exists(index_path):
        return []
    try:
        mtime = os.path.getmtime(index_path)
    except Exception:
        mtime = None
    if (
        not force_refresh
        and _EXTERNAL_SKILLS_CACHE.get("path") == index_path
        and _EXTERNAL_SKILLS_CACHE.get("mtime") == mtime
    ):
        return list(_EXTERNAL_SKILLS_CACHE.get("skills") or [])
    try:
        with open(index_path, "r", encoding="utf-8") as handle:
            raw_data = json.load(handle)
    except Exception:
        return []
    if not isinstance(raw_data, list):
        return []
    denylist = _DEFAULT_DENYLIST
    env_denylist = os.getenv("REFINER_SKILLS_DENYLIST")
    if env_denylist:
        denylist = _split_csv(env_denylist)
    elif isinstance(cfg.get("denylist"), list):
        denylist = [str(item).strip() for item in cfg.get("denylist") if str(item).strip()]
    allow_categories = None
    env_allow = os.getenv("REFINER_SKILLS_ALLOW_CATEGORIES")
    if env_allow:
        allow_categories = {item.lower() for item in _split_csv(env_allow)}
    elif isinstance(cfg.get("allow_categories"), list):
        allow_categories = {str(item).lower() for item in cfg.get("allow_categories") if str(item).strip()}
    summary_max_chars = cfg.get("summary_max_chars")
    env_summary = os.getenv("REFINER_SKILLS_SUMMARY_MAX_CHARS")
    if env_summary:
        try:
            summary_max_chars = int(env_summary)
        except Exception:
            summary_max_chars = None
    if not isinstance(summary_max_chars, int) or summary_max_chars <= 0:
        summary_max_chars = 180
    max_external = cfg.get("max_external")
    env_max = os.getenv("REFINER_SKILLS_MAX_EXTERNAL")
    if env_max:
        try:
            max_external = int(env_max)
        except Exception:
            max_external = None
    if not isinstance(max_external, int) or max_external <= 0:
        max_external = None

    cleaned: List[Dict[str, Any]] = []
    for entry in raw_data:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("category") or "").lower()
        if allow_categories and category and category not in allow_categories:
            continue
        if allow_categories and not category:
            continue
        text = " ".join(
            [
                str(entry.get("id") or ""),
                str(entry.get("name") or ""),
                str(entry.get("description") or ""),
                str(entry.get("path") or ""),
            ]
        ).lower()
        if any(token in text for token in denylist):
            continue
        normalized = _normalize_external_skill(entry, summary_max_chars)
        if normalized:
            cleaned.append(normalized)
        if max_external and len(cleaned) >= max_external:
            break
    _EXTERNAL_SKILLS_CACHE.update({"path": index_path, "mtime": mtime, "skills": cleaned})
    return list(cleaned)


def _get_skills(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Return merged built-in and external skills without duplicates."""
    skills = list(_SKILLS)
    external = _load_external_skills(force_refresh=force_refresh)
    seen = {skill.get("id") for skill in skills if skill.get("id")}
    for skill in external:
        skill_id = skill.get("id")
        if skill_id and skill_id in seen:
            continue
        skills.append(skill)
        if skill_id:
            seen.add(skill_id)
    return skills



def _load_analysis(force_refresh: bool = False) -> Dict[str, Any]:
    """Run or reuse repository capability analysis output."""
    global _CAPABILITY_CACHE
    if not force_refresh and _CAPABILITY_CACHE.get("report"):
        return _CAPABILITY_CACHE["report"]
    bundled_report = _load_bundled_analysis_report()
    try:
        report = analyse_repo(_REPO_ROOT)
        if bundled_report and _analysis_needs_fallback(report):
            report = bundled_report
    except Exception as exc:
        report = bundled_report or {"error": str(exc)}
    _CAPABILITY_CACHE = {"ts": time.time(), "report": report}
    return report


def _tokenize(text: str) -> List[str]:
    """Tokenize free-form text for lightweight skill matching."""
    tokens = []
    for match in _TOKEN_RE.findall(text or ""):
        token = match.lower().strip()
        if len(token) < 3:
            continue
        tokens.append(token)
        for part in re.split(r"[-_/]", token):
            if len(part) >= 3:
                tokens.append(part)
    return tokens


def get_capabilities(force_refresh: bool = False) -> Dict[str, Any]:
    """Build the complete capability payload exposed by the API."""
    analysis = _load_analysis(force_refresh=force_refresh)
    skills = _get_skills(force_refresh=force_refresh)
    external_count = len([s for s in skills if s.get("external")])
    return {
        "agentic": {
            "loop": "plan → act → verify → reflect",
            "roles": "role-based LLM overrides available",
        },
        "workflows": list(_WORKFLOWS),
        "rag": {
            "purpose": "Retrieve unstructured context to improve responses.",
            "supports": ["local documents", "project files", "notes"],
            "notes": "Read-only context injection; no actions.",
        },
        "mcp": {
            "purpose": "Call external tools for structured data or actions.",
            "notes": "Admin-only server registration; tool calls may be audited.",
        },
        "skills": skills,
        "skills_catalog": {
            "total": len(skills),
            "external": external_count,
            "config_path": os.getenv("REFINER_CONFIG_PATH") or os.getenv("SOLVER_CONFIG_PATH") or "config.json",
            "index_path": _resolve_skill_index_path(_load_skills_config()),
        },
        "analysis": analysis,
    }


def get_skills(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Public accessor for merged skills catalog."""
    return _get_skills(force_refresh=force_refresh)


def capability_summary(max_items: int = 6) -> str:
    """Render a concise human-readable capability summary."""
    capabilities = get_capabilities()
    workflows = capabilities.get("workflows") or []
    workflow_names = ", ".join([item.get("name", "") for item in workflows if item.get("name")])
    analysis = capabilities.get("analysis") or {}
    features = analysis.get("features") or []
    feature_names = ", ".join([item.get("name", "") for item in features[:4] if item.get("name")])
    lines = []
    if workflow_names:
        lines.append(f"Core workflows: {workflow_names}.")
    if feature_names:
        lines.append(f"Key capabilities: {feature_names}.")
    lines.extend(
        [
            "RAG: build indexes from documents and retrieve relevant context.",
            "MCP: connect to external systems for structured queries and actions.",
            "Agentic loop: plan → act → verify → reflect with role overrides.",
            "Web UI/API: job queue, tokens, secrets, refunds, requirements tools.",
        ]
    )
    if max_items and len(lines) > max_items:
        lines = lines[:max_items]
    return "\n".join(lines)


def select_skills(query: str, limit: int = 4) -> List[Dict[str, Any]]:
    """Return top skills relevant to a query using token overlap scoring."""
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return []
    scored = []
    for skill in _get_skills():
        tokens = []
        tokens.extend(_tokenize(skill.get("name") or ""))
        tokens.extend(_tokenize(skill.get("summary") or ""))
        for cue in skill.get("cues") or []:
            tokens.extend(_tokenize(str(cue)))
        score = len(query_tokens & set(tokens))
        if score:
            scored.append((score, skill))
    scored.sort(key=lambda item: (-item[0], item[1].get("id")))
    return [dict(item[1]) for item in scored[:limit]]


def format_skill_brief(skills: List[Dict[str, Any]]) -> str:
    """Format selected skills into a compact bullet list string."""
    if not skills:
        return ""
    lines = []
    for skill in skills:
        name = skill.get("name") or skill.get("id")
        summary = skill.get("summary") or ""
        if summary:
            lines.append(f"- {name}: {summary}")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)
