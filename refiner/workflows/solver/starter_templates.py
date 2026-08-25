"""Compact starter patterns shown to planners for common playground tasks."""

from __future__ import annotations

import os
from typing import Dict, List


STARTER_TEMPLATES: Dict[str, str] = {
    "node-web": (
        "Use a single project-root package.json and keep server.js/index.html beside it. "
        "Export the Express app from server.js (or app.js), guard listen() with the main-module check, "
        "and test with an ephemeral listen(0) port that is closed in finally."
    ),
    "python-cli": (
        "Keep the executable module in the project root or src/, expose a main() entrypoint, "
        "use pathlib for paths, and add a pytest test for the happy path and one invalid-input path."
    ),
    "rust-cli": (
        "Keep Cargo.toml and src/ in one project root, return Result from fallible operations, "
        "and add unit tests for the main behaviour plus an error case."
    ),
}


def _has(root: str, *names: str) -> bool:
    return any(os.path.exists(os.path.join(root, name)) for name in names)


def build_starter_template_context(project_root: str, languages: Dict[str, List[str]]) -> str:
    """Return only relevant guidance when a project is missing its usual shape."""

    detected = set(languages.get("languages") or [])
    suggestions: List[str] = []
    if "node" in detected and not _has(project_root, "package.json"):
        suggestions.append(f"node-web: {STARTER_TEMPLATES['node-web']}")
    if "python" in detected and not _has(project_root, "pyproject.toml", "setup.py", "main.py"):
        suggestions.append(f"python-cli: {STARTER_TEMPLATES['python-cli']}")
    if "rust" in detected and not _has(project_root, "Cargo.toml"):
        suggestions.append(f"rust-cli: {STARTER_TEMPLATES['rust-cli']}")
    if not suggestions:
        return ""
    return "Starter template guidance for the detected project shape:\n- " + "\n- ".join(suggestions)
