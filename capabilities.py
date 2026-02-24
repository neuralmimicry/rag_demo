from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List

from capability_analyzer import analyse_repo


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
        "description": "LLM-assisted issue quality analysis with optional comment posting.",
        "triggers": ["--analyze-jira"],
        "outputs": ["jira_report.html"],
    },
    {
        "id": "confluence_analysis",
        "name": "Confluence space analysis",
        "description": "Space/page hierarchy analysis with optional LLM/Rovo insights.",
        "triggers": ["--analyze-confluence"],
        "outputs": ["confluence_report.html"],
    },
    {
        "id": "topic_research",
        "name": "Topic research",
        "description": "Iterative research with Jira/Confluence context and optional web search.",
        "triggers": ["--topic-research"],
        "outputs": ["researched_document.md"],
    },
    {
        "id": "project_solver",
        "name": "Project solver",
        "description": "Extract requirements, plan, and optionally apply code changes with an agentic loop.",
        "triggers": ["--project"],
        "outputs": ["project_solution.json", "workspace edits"],
    },
    {
        "id": "delivery_pipeline",
        "name": "Delivery pipeline",
        "description": "Multi-stage pipeline from sandbox to deploy with approvals and artifacts.",
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


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-/]{2,}")

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_CAPABILITY_CACHE: Dict[str, Any] = {"ts": 0.0, "report": None}


def _load_analysis(force_refresh: bool = False) -> Dict[str, Any]:
    global _CAPABILITY_CACHE
    if not force_refresh and _CAPABILITY_CACHE.get("report"):
        return _CAPABILITY_CACHE["report"]
    try:
        report = analyse_repo(_REPO_ROOT)
    except Exception as exc:
        report = {"error": str(exc)}
    _CAPABILITY_CACHE = {"ts": time.time(), "report": report}
    return report


def _tokenize(text: str) -> List[str]:
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
    analysis = _load_analysis(force_refresh=force_refresh)
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
        "skills": list(_SKILLS),
        "analysis": analysis,
    }


def capability_summary(max_items: int = 6) -> str:
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
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return []
    scored = []
    for skill in _SKILLS:
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
