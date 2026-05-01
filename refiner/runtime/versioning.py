"""Shared application version helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
BUILD_INFO_PATH = PROJECT_ROOT / ".refiner-build.json"
VERSION_PATTERN = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<build>\d{4,})$")


def _env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return ""


def _env_int(*names: str) -> Optional[int]:
    for name in names:
        value = _env_first(name)
        if not value:
            continue
        try:
            return int(value, 10)
        except Exception:
            continue
    return None


def _read_pyproject_version() -> str:
    current_section = ""
    try:
        with PYPROJECT_PATH.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current_section = line
                    continue
                if current_section != "[project]":
                    continue
                match = re.match(r'version\s*=\s*"(?P<value>[^"]+)"', line)
                if match:
                    return match.group("value")
    except Exception:
        pass
    return "0.1.0"


def _parse_major_minor(version_text: str) -> Tuple[int, int]:
    parts = re.findall(r"\d+", version_text)
    major = int(parts[0]) if len(parts) >= 1 else 0
    minor = int(parts[1]) if len(parts) >= 2 else 1
    return major, minor


def _git_output(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        return completed.stdout.strip()
    except Exception:
        return ""


def _read_build_info_file() -> Dict[str, Any]:
    try:
        with BUILD_INFO_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _cached_version_info() -> Dict[str, Any]:
    base_version = _env_first("REFINER_BASE_VERSION") or _read_pyproject_version()
    release_version = _env_first("REFINER_RELEASE_VERSION") or base_version
    major = _env_int("REFINER_VERSION_MAJOR")
    minor = _env_int("REFINER_VERSION_MINOR")
    if major is None or minor is None:
        py_major, py_minor = _parse_major_minor(base_version)
        if major is None:
            major = py_major
        if minor is None:
            minor = py_minor

    build_info_file = _read_build_info_file()
    explicit_version = _env_first("REFINER_VERSION")
    build_number = _env_int("REFINER_BUILD_NUMBER", "BUILD_NUMBER")
    source = "env" if build_number is not None or explicit_version else ""

    if build_number is None:
        git_count = _git_output("rev-list", "--count", "HEAD")
        if git_count.isdigit():
            build_number = int(git_count, 10)
            source = "git"

    if build_number is None:
        try:
            build_number = int(build_info_file.get("build_number"))
            source = "build_file"
        except Exception:
            build_number = None

    if build_number is None:
        build_number = 0
        source = source or "default"

    commit_full = _env_first("GIT_COMMIT", "REFINER_GIT_COMMIT")
    if not commit_full:
        commit_full = _git_output("rev-parse", "HEAD")
        if commit_full and not source:
            source = "git"
    if not commit_full:
        commit_full = str(build_info_file.get("commit") or "").strip()
        if commit_full and not source:
            source = "build_file"
    if not commit_full:
        commit_full = "unknown"
        source = source or "default"

    build = f"{int(build_number):04d}"
    version = f"{int(major)}.{int(minor)}.{build}"

    if explicit_version:
        match = VERSION_PATTERN.match(explicit_version)
        if match:
            version = explicit_version
            major = int(match.group("major"))
            minor = int(match.group("minor"))
            build = match.group("build")
            build_number = int(build, 10)
            source = "env"

    return {
        "version": version,
        "build_version": version,
        "release_version": release_version,
        "major": int(major),
        "minor": int(minor),
        "build": build,
        "build_number": int(build_number),
        "commit": commit_full,
        "commit_short": commit_full[:8] if commit_full and commit_full != "unknown" else "unknown",
        "source": source or "default",
    }


def get_version_info(refresh: bool = False) -> Dict[str, Any]:
    """Return the full version payload."""
    if refresh:
        _cached_version_info.cache_clear()
    return dict(_cached_version_info())


def get_public_version_info(refresh: bool = False) -> Dict[str, Any]:
    """Return the public API/template version payload."""
    info = get_version_info(refresh=refresh)
    return {
        "version": info["version"],
        "build_version": info["build_version"],
        "release_version": info["release_version"],
        "major": info["major"],
        "minor": info["minor"],
        "build": info["build"],
        "build_number": info["build_number"],
        "commit": info["commit_short"],
        "commit_full": info["commit"],
        "source": info["source"],
    }
