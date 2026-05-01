"""Lightweight language/build-system detection for delivery pipelines."""
from __future__ import annotations

from typing import Dict, List, Set
import os

from refiner.repo_context import DEFAULT_IGNORED_DIRS


LANGUAGE_EXTS = {
    "python": {".py"},
    "javascript": {".js", ".jsx"},
    "typescript": {".ts", ".tsx"},
    "go": {".go"},
    "rust": {".rs"},
    "c": {".c", ".h"},
    "cpp": {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"},
    "fortran": {".f", ".f90", ".f95", ".f03", ".f08"},
    "pascal": {".pas", ".pp"},
    "shell": {".sh"},
    "powershell": {".ps1"},
}

BUILD_SYSTEM_FILES = {
    "python": [
        "pyproject.toml",
        "requirements.txt",
        "requirements.in",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "poetry.lock",
    ],
    "node": ["package.json", "yarn.lock", "pnpm-lock.yaml", "package-lock.json"],
    "go": ["go.mod"],
    "rust": ["Cargo.toml", "Cargo.lock"],
    "make": ["Makefile", "makefile"],
    "cmake": ["CMakeLists.txt"],
    "meson": ["meson.build"],
    "ninja": ["build.ninja"],
}


def _match_file(name: str, patterns: List[str]) -> bool:
    return any(name == pattern for pattern in patterns)


def detect_languages(root: str, *, max_files: int = 500) -> Dict[str, List[str]]:
    languages: Set[str] = set()
    build_systems: Set[str] = set()
    files_seen = 0

    if not os.path.isdir(root):
        return {"languages": [], "build_systems": []}

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
        for filename in files:
            files_seen += 1
            if files_seen > max_files:
                break
            ext = os.path.splitext(filename)[1].lower()
            for lang, exts in LANGUAGE_EXTS.items():
                if ext in exts:
                    languages.add(lang)
            for system, markers in BUILD_SYSTEM_FILES.items():
                if _match_file(filename, markers):
                    build_systems.add(system)
        if files_seen > max_files:
            break

    # Normalize some grouped languages
    if "javascript" in languages or "typescript" in languages:
        languages.add("node")
    if "powershell" in languages:
        languages.add("powerscript")

    return {
        "languages": sorted(languages),
        "build_systems": sorted(build_systems),
    }
