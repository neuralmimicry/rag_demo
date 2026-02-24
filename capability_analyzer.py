from __future__ import annotations

import ast
import os
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

from logging_utils import UK_TZ, UK_DATETIME_FORMAT

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "job_data",
    "node_modules",
    "container",
    "dist",
    "build",
}

MAX_FILE_BYTES = 200_000

ROUTE_RE = re.compile(r"@app\.route\((.+)\)")
STRING_RE = re.compile(r"['\"]([^'\"]+)['\"]")
METHODS_RE = re.compile(r"methods\s*=\s*\[([^\]]+)\]")


FEATURE_RULES: List[Dict[str, Any]] = [
    {
        "id": "agentic",
        "name": "Agentic workflow engine",
        "summary": "Plan → act → verify → reflect loop with phase tracking and retries.",
        "path_tokens": ["agentic_workflow.py", "project_solver.py"],
        "content_keywords": ["agenticworkflow", "plan", "verify", "reflect"],
    },
    {
        "id": "project_solver",
        "name": "Project solver",
        "summary": "Extract requirements, plan work, and optionally apply code changes.",
        "path_tokens": ["project_solver.py", "repo_context.py", "file_converter.py"],
        "content_keywords": ["project solver", "requirements register", "agentic"],
    },
    {
        "id": "topic_research",
        "name": "Topic research",
        "summary": "Iterative research with optional web search and structured notes.",
        "path_tokens": ["topic_researcher.py", "web_research.py"],
        "content_keywords": ["topic research", "web search"],
    },
    {
        "id": "jira",
        "name": "Jira analytics",
        "summary": "Discovery-driven Jira reporting and issue quality analysis.",
        "path_tokens": ["jira_", "fetch_issues.py", "sort_issues_by_priority.py", "main.py"],
        "content_keywords": ["jira", "jql", "worklog"],
    },
    {
        "id": "confluence",
        "name": "Confluence analysis",
        "summary": "Space/page hierarchy analysis with optional LLM/Rovo insights.",
        "path_tokens": ["confluence_", "discover_hierarchy.py"],
        "content_keywords": ["confluence", "rovo", "space"],
    },
    {
        "id": "delivery",
        "name": "Delivery pipeline",
        "summary": "Multi-stage pipeline from sandbox to deploy with approvals.",
        "path_tokens": ["delivery_pipeline.py", "delivery_pipeline.json"],
        "content_keywords": ["delivery pipeline", "staging", "uat", "deploy"],
    },
    {
        "id": "rag",
        "name": "RAG indexing",
        "summary": "Build and query local indexes for unstructured context.",
        "path_tokens": ["rag_engine.py", "repo_context.py"],
        "content_keywords": ["rag", "embedding", "retrieval"],
    },
    {
        "id": "mcp",
        "name": "MCP integrations",
        "summary": "Connect to external systems for structured data and actions.",
        "path_tokens": ["mcp_client.py"],
        "content_keywords": ["mcp", "json-rpc", "tools"],
    },
    {
        "id": "web_ui",
        "name": "Web control room",
        "summary": "Flask-based UI/API for job orchestration and monitoring.",
        "path_tokens": ["refiner_web.py", "frontend_server.py"],
        "content_keywords": ["flask", "api", "control room"],
    },
    {
        "id": "assistant_tools",
        "name": "Assistant tools",
        "summary": "Requirements, form-fill, and RAG/MCP assistant endpoints.",
        "path_tokens": ["refiner_web.py"],
        "content_keywords": ["/api/assistant", "requirements assistant", "form assistant"],
    },
]


def _iter_python_files(root: str) -> List[str]:
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename.endswith(".py"):
                files.append(os.path.join(dirpath, filename))
    return files


def _safe_read(path: str, max_bytes: int = MAX_FILE_BYTES) -> str:
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes)
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_workflows(root: str) -> List[Dict[str, Any]]:
    path = os.path.join(root, "run_refiner.py")
    if not os.path.exists(path):
        return []
    content = _safe_read(path)
    if not content:
        return []
    try:
        doc = ast.get_docstring(ast.parse(content))
    except Exception:
        doc = None
    if not doc:
        return []
    workflows: List[Dict[str, Any]] = []
    capture = False
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("cli entry point to run refiner workflows"):
            capture = True
            continue
        if capture:
            if stripped.startswith("- "):
                item = stripped[2:].strip()
                name = item
                description = ""
                if "(" in item and item.endswith(")"):
                    name, rest = item.split("(", 1)
                    name = name.strip()
                    description = rest.rstrip(")").strip()
                workflows.append(
                    {
                        "id": _slugify(name),
                        "name": name,
                        "description": description,
                        "source": "run_refiner.py:docstring",
                    }
                )
            elif stripped == "" and workflows:
                break
    return workflows


def _extract_agentic_phases(root: str) -> List[str]:
    path = os.path.join(root, "agentic_workflow.py")
    if not os.path.exists(path):
        return ["plan", "act", "verify", "reflect"]
    content = _safe_read(path)
    match = re.search(r"phases\s*or\s*\[([^\]]+)\]", content)
    if not match:
        return ["plan", "act", "verify", "reflect"]
    raw = match.group(1)
    items = []
    for chunk in raw.split(","):
        token = chunk.strip().strip("'\"")
        if token:
            items.append(token)
    return items or ["plan", "act", "verify", "reflect"]


def _extract_routes(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    lines = _safe_read(path).splitlines()
    routes: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if "@app.route" in line:
            buffer = line.strip()
            while not buffer.endswith(")") and idx + 1 < len(lines):
                idx += 1
                buffer += lines[idx].strip()
            match = ROUTE_RE.search(buffer)
            if match:
                args = match.group(1)
                path_match = STRING_RE.search(args)
                route_path = path_match.group(1) if path_match else None
                methods_match = METHODS_RE.search(args)
                methods = []
                if methods_match:
                    for token in methods_match.group(1).split(","):
                        method = token.strip().strip("'\"")
                        if method:
                            methods.append(method)
                if route_path:
                    handler = None
                    lookahead = idx + 1
                    while lookahead < len(lines):
                        candidate = lines[lookahead].lstrip()
                        if candidate.startswith("def "):
                            handler = candidate.split("def ")[1].split("(")[0]
                            break
                        if candidate.strip() and not candidate.startswith("@"):
                            break
                        lookahead += 1
                    routes.append(
                        {
                            "path": route_path,
                            "methods": methods or ["GET"],
                            "handler": handler,
                        }
                    )
        idx += 1
    return routes


def _group_routes(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, int] = {}
    for route in routes:
        path = route.get("path") or ""
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            group = f"/{parts[0]}/{parts[1]}"
        elif parts:
            group = f"/{parts[0]}"
        else:
            group = "/"
        grouped[group] = grouped.get(group, 0) + 1
    summary = [{"group": key, "count": count} for key, count in grouped.items()]
    summary.sort(key=lambda item: (-item["count"], item["group"]))
    return summary


def _extract_assistants(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hints = {
        "requirements": "Requirements drafting and Q&A.",
        "form-fill": "Suggests field values for forms.",
        "rag-mcp": "Combines RAG context and MCP tool results.",
        "plan": "Playground planning for quick project briefs.",
    }
    assistants: List[Dict[str, Any]] = []
    for route in routes:
        path = route.get("path") or ""
        if not path.startswith("/api/assistant/"):
            continue
        slug = path.split("/api/assistant/")[-1]
        assistants.append(
            {
                "id": slug or "assistant",
                "path": path,
                "methods": route.get("methods") or ["GET"],
                "summary": hints.get(slug, ""),
            }
        )
    return assistants


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return cleaned or "workflow"


def _detect_features(root: str, files: List[str]) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    for path in files:
        rel_path = os.path.relpath(path, root)
        content = _safe_read(path)
        signals.append(
            {
                "path": rel_path,
                "path_lower": rel_path.lower(),
                "content_lower": content.lower(),
            }
        )

    features: List[Dict[str, Any]] = []
    for rule in FEATURE_RULES:
        evidence: List[str] = []
        tokens = [token.lower() for token in rule.get("path_tokens") or []]
        keywords = [kw.lower() for kw in rule.get("content_keywords") or []]
        for signal in signals:
            matched = False
            for token in tokens:
                if token and token in signal["path_lower"]:
                    matched = True
                    break
            if not matched and keywords:
                for keyword in keywords:
                    if keyword and keyword in signal["content_lower"]:
                        matched = True
                        break
            if matched:
                evidence.append(signal["path"])
        if evidence:
            features.append(
                {
                    "id": rule["id"],
                    "name": rule["name"],
                    "summary": rule["summary"],
                    "evidence": sorted(set(evidence))[:6],
                    "evidence_count": len(set(evidence)),
                }
            )
    features.sort(key=lambda item: item.get("name", ""))
    return features


def analyse_repo(root: str) -> Dict[str, Any]:
    root = os.path.abspath(root)
    files = _iter_python_files(root)
    routes = _extract_routes(os.path.join(root, "refiner_web.py"))
    report = {
        "generated_at": datetime.now(UK_TZ).strftime(UK_DATETIME_FORMAT),
        "root": os.path.basename(root),
        "files_scanned": len(files),
        "workflows_detected": _extract_workflows(root),
        "agentic_phases": _extract_agentic_phases(root),
        "api": {
            "total_routes": len(routes),
            "groups": _group_routes(routes),
            "routes": routes,
            "assistants": _extract_assistants(routes),
        },
        "features": _detect_features(root, files),
    }
    return report
