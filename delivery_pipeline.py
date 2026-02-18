"""
Delivery pipeline workflow for project solver outputs.

Implements a staged pipeline that can run isolated sandbox tests, dev and
integration checks, staging, UAT, and deployment steps. Stages are driven by a
JSON configuration file and can be gated by approval files. Optional VCS
actions (pull/branch/commit/merge/push/tag/release) run ahead of stages and are
configured via the pipeline config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import json
import logging
import os
import re
import shutil
import subprocess
import time

from agentic_workflow import AgenticWorkflow, PhaseResult
from vcs_workflow import run_vcs_workflow
from platform_selector import select_platform
from project_solver import _find_workspace_venv, run_project_solver
from language_detector import detect_languages
from repo_context import RepoIndex
from repo_context import DEFAULT_IGNORED_DIRS

logger = logging.getLogger(__name__)


DEFAULT_OUTPUT_DIR = "delivery_pipeline_output"


@dataclass
class PipelineStage:
    name: str
    description: str = ""
    kind: str = ""
    workspace_mode: str = "copy"  # copy or project
    commands: List[Any] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    requires_approval: bool = False
    approval_file: Optional[str] = None
    requires_solver_completion: bool = False
    artifacts: List[str] = field(default_factory=list)
    timeout_sec: int = 900
    allow_failure: bool = False
    enabled: bool = True
    retry_attempts: int = 0
    retry_delay_sec: int = 0
    auto_recover: Optional[bool] = None


@dataclass
class PipelineConfig:
    output_dir: str = DEFAULT_OUTPUT_DIR
    workspace_root: Optional[str] = None
    artifacts_root: Optional[str] = None
    approvals: Dict[str, str] = field(default_factory=dict)
    versioning: Dict[str, Any] = field(default_factory=dict)
    vcs: Dict[str, Any] = field(default_factory=dict)
    platform: Dict[str, Any] = field(default_factory=dict)
    solver_fallback: Dict[str, Any] = field(default_factory=dict)
    solver_gate: str = "block_deploy"
    allow_unfinished_deploy: bool = False
    auto_recover: bool = True
    retry_attempts: int = 1
    retry_delay_sec: int = 2
    overlay_solver_workspace: bool = True
    require_solver_completion: bool = False
    clean_workspaces: bool = False
    stages: List[PipelineStage] = field(default_factory=list)
    extra_ignored: List[str] = field(default_factory=list)


def _safe_mkdir(path: str) -> None:
    if path:
        os.makedirs(path, exist_ok=True)


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _normalise_stage(entry: Dict[str, Any]) -> PipelineStage:
    name = str(entry.get("name") or "").strip()
    if not name:
        raise ValueError("Pipeline stage missing name")
    return PipelineStage(
        name=name,
        description=str(entry.get("description") or "").strip(),
        kind=str(entry.get("kind") or entry.get("category") or "").strip().lower(),
        workspace_mode=str(entry.get("workspace_mode") or entry.get("workspace") or "copy").strip().lower(),
        commands=list(entry.get("commands") or []),
        env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
        requires_approval=bool(entry.get("requires_approval") or False),
        approval_file=entry.get("approval_file"),
        requires_solver_completion=bool(entry.get("requires_solver_completion") or False),
        artifacts=list(entry.get("artifacts") or []),
        timeout_sec=int(entry.get("timeout_sec") or 900),
        allow_failure=bool(entry.get("allow_failure") or False),
        enabled=bool(entry.get("enabled", True)),
        retry_attempts=int(entry.get("retry_attempts") or 0),
        retry_delay_sec=int(entry.get("retry_delay_sec") or 0),
        auto_recover=entry.get("auto_recover"),
    )


def load_pipeline_config(path: str) -> PipelineConfig:
    raw = _read_json(path)
    stages = []
    for entry in raw.get("stages") or []:
        if not isinstance(entry, dict):
            continue
        stages.append(_normalise_stage(entry))
    if not stages:
        raise ValueError("Pipeline config requires at least one stage")

    return PipelineConfig(
        output_dir=str(raw.get("output_dir") or DEFAULT_OUTPUT_DIR),
        workspace_root=raw.get("workspace_root"),
        artifacts_root=raw.get("artifacts_root"),
        approvals=raw.get("approvals") or {},
        versioning=raw.get("versioning") or {},
        vcs=raw.get("vcs") or {},
        platform=raw.get("platform") or {},
        solver_fallback=raw.get("solver_fallback") or {},
        solver_gate=str(raw.get("solver_gate") or "block_deploy").strip().lower(),
        allow_unfinished_deploy=bool(raw.get("allow_unfinished_deploy", False)),
        auto_recover=bool(raw.get("auto_recover", True)),
        retry_attempts=int(raw.get("retry_attempts") or 1),
        retry_delay_sec=int(raw.get("retry_delay_sec") or 2),
        overlay_solver_workspace=bool(raw.get("overlay_solver_workspace", True)),
        require_solver_completion=bool(raw.get("require_solver_completion", False)),
        clean_workspaces=bool(raw.get("clean_workspaces", False)),
        stages=stages,
        extra_ignored=list(raw.get("extra_ignored") or []),
    )


def _resolve_path(root: str, value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(root, value))


def _safe_version(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned or "unknown"


def _git_version(project_root: str) -> Optional[str]:
    commands = [
        ["git", "-C", project_root, "describe", "--tags", "--always", "--dirty"],
        ["git", "-C", project_root, "rev-parse", "--short", "HEAD"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except Exception:
            continue
        if result.returncode == 0:
            text = (result.stdout or "").strip()
            if text:
                return text
    return None


def _compute_version(project_root: str, versioning: Dict[str, Any]) -> str:
    strategy = str(versioning.get("strategy") or "git_or_timestamp").strip().lower()
    version = None
    if strategy in {"git", "git_or_timestamp", "git-or-timestamp"}:
        version = _git_version(project_root)
    if not version:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        version = stamp
    prefix = str(versioning.get("prefix") or "").strip()
    if prefix:
        version = f"{prefix}{version}"
    return _safe_version(version)


def _write_version_file(
    version: str,
    *,
    versioning: Dict[str, Any],
    project_root: str,
    output_dir: str,
) -> Optional[str]:
    if not versioning.get("write_file"):
        return None
    file_path = versioning.get("file")
    if not file_path:
        return None
    location = str(versioning.get("location") or "output").strip().lower()
    base = project_root if location == "project" else output_dir
    target = _resolve_path(base, str(file_path))
    if not target:
        return None
    _safe_mkdir(os.path.dirname(target))
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(version + "\n")
    return target


def _build_ignore(project_root: str, output_dir: str, extra_ignored: List[str]):
    ignored = set(DEFAULT_IGNORED_DIRS)
    ignored.update({DEFAULT_OUTPUT_DIR})
    for item in extra_ignored:
        if item:
            ignored.add(item)

    # Ignore the top-level output directory if it lives under the project root
    if output_dir:
        try:
            rel = os.path.relpath(output_dir, project_root)
        except Exception:
            rel = None
        if rel and not rel.startswith(".."):
            top = rel.split(os.sep)[0]
            ignored.add(top)

    def _ignore(dirpath: str, entries: List[str]) -> List[str]:
        return [entry for entry in entries if entry in ignored]

    return _ignore


def _copy_tree(src: str, dest: str, ignore) -> None:
    shutil.copytree(src, dest, ignore=ignore, dirs_exist_ok=True)


def _trim_output(text: str, limit: int = 4000) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _tail_output(text: str, max_lines: int = 20, max_chars: int = 800) -> str:
    if not text:
        return ""
    lines = text.strip().splitlines()
    tail = "\n".join(lines[-max_lines:])
    if len(tail) <= max_chars:
        return tail
    return tail[-max_chars:]


def _run_command(
    command: str,
    *,
    workdir: str,
    env: Dict[str, str],
    timeout_sec: int,
    stage_name: Optional[str] = None,
    attempt: Optional[int] = None,
) -> Dict[str, Any]:
    started = time.time()
    result = {
        "command": command,
        "exit_code": None,
        "status": "error",
        "stdout": "",
        "stderr": "",
        "stdout_tail": "",
        "stderr_tail": "",
        "duration_sec": 0.0,
    }
    attempt_note = f" attempt={attempt}" if isinstance(attempt, int) else ""
    stage_note = f" stage={stage_name}" if stage_name else ""
    logger.info("Delivery command%s%s: %s", stage_note, attempt_note, command)
    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            shell=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_sec,
        )
        result["exit_code"] = completed.returncode
        result["stdout"] = _trim_output(completed.stdout)
        result["stderr"] = _trim_output(completed.stderr)
        result["stdout_tail"] = _tail_output(completed.stdout)
        result["stderr_tail"] = _tail_output(completed.stderr)
        result["status"] = "ok" if completed.returncode == 0 else "failed"
    except Exception as exc:
        result["stderr"] = _trim_output(str(exc))
        result["stderr_tail"] = _tail_output(str(exc))
        result["status"] = "error"
    result["duration_sec"] = round(time.time() - started, 2)
    if result["status"] != "ok":
        tail = result.get("stderr_tail") or result.get("stdout_tail") or ""
        logger.warning(
            "Delivery command failed%s%s: exit=%s stderr=%s",
            stage_note,
            attempt_note,
            result.get("exit_code"),
            tail,
        )
    return result


def _resolve_stage_workspace(
    stage: PipelineStage,
    *,
    project_root: str,
    workspace_root: str,
    version: str,
    clean_workspaces: bool,
    ignore,
    overlay_root: Optional[str],
    overlay_enabled: bool,
) -> Tuple[str, List[str]]:
    notes: List[str] = []
    if stage.workspace_mode == "project":
        if overlay_enabled and overlay_root:
            notes.append("overlay solver workspace onto project root is disabled by default")
        return project_root, notes

    stage_dir = os.path.join(workspace_root, version, stage.name)
    if clean_workspaces and os.path.exists(stage_dir):
        shutil.rmtree(stage_dir)
        notes.append("cleaned existing workspace")

    _safe_mkdir(stage_dir)
    _copy_tree(project_root, stage_dir, ignore)
    notes.append("copied project into workspace")

    if overlay_enabled and overlay_root and os.path.isdir(overlay_root):
        _copy_tree(overlay_root, stage_dir, ignore)
        notes.append("overlayed solver workspace")

    return stage_dir, notes


def _collect_artifacts(
    stage: PipelineStage,
    *,
    workspace: str,
    artifacts_root: str,
    version: str,
) -> List[Dict[str, str]]:
    collected: List[Dict[str, str]] = []
    if not stage.artifacts:
        return collected
    stage_root = os.path.join(artifacts_root, version, stage.name)
    for item in stage.artifacts:
        rel_path = str(item).strip()
        if not rel_path:
            continue
        source = os.path.join(workspace, rel_path)
        if not os.path.exists(source):
            collected.append({"path": rel_path, "status": "missing"})
            continue
        dest = os.path.join(stage_root, rel_path)
        _safe_mkdir(os.path.dirname(dest))
        if os.path.isdir(source):
            shutil.copytree(source, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(source, dest)
        collected.append({"path": rel_path, "status": "copied", "dest": dest})
    return collected


def _candidate_requirements_files(workspace: str) -> Optional[str]:
    if not workspace or not os.path.isdir(workspace):
        return None
    priority = [
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-dev.in",
        "requirements.in",
        "requirements-test.txt",
        "req.txt",
    ]
    for name in priority:
        candidate = os.path.join(workspace, name)
        if os.path.isfile(candidate):
            return name
    try:
        for name in sorted(os.listdir(workspace)):
            if name.startswith("requirements") and name.endswith(".txt"):
                return name
            if name.startswith("req") and name.endswith(".txt"):
                return name
    except Exception:
        return None
    return None


def _project_packaging_signals(workspace: str) -> Dict[str, bool]:
    def _exists(name: str) -> bool:
        return os.path.isfile(os.path.join(workspace, name))

    has_pyproject = _exists("pyproject.toml")
    has_poetry = False
    if has_pyproject:
        try:
            with open(os.path.join(workspace, "pyproject.toml"), "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read(4096)
            has_poetry = "[tool.poetry]" in text
        except Exception:
            has_poetry = False

    return {
        "pyproject": has_pyproject,
        "poetry": has_poetry or _exists("poetry.lock"),
        "pipfile": _exists("Pipfile"),
        "setup_py": _exists("setup.py"),
        "setup_cfg": _exists("setup.cfg"),
        "package_json": _exists("package.json"),
    }


def _plan_recovery(
    *,
    command: str,
    result: Dict[str, Any],
    workspace: str,
) -> Optional[Dict[str, Any]]:
    text = ((result.get("stdout") or "") + "\n" + (result.get("stderr") or "")).lower()
    recovery: Dict[str, Any] = {"reason": "", "commands": []}

    venv_path = _find_workspace_venv(workspace)
    python_exec = os.path.join(venv_path, "bin", "python") if venv_path else "python"
    pip_cmd = f"{python_exec} -m pip"
    packaging = _project_packaging_signals(workspace)
    lang_info = detect_languages(workspace)
    languages = set(lang_info.get("languages") or [])
    python_signals = bool(
        packaging.get("pyproject")
        or packaging.get("setup_py")
        or packaging.get("setup_cfg")
        or packaging.get("pipfile")
        or "python" in languages
    )

    if python_signals and (".venv/bin/python" in command or ".venv\\scripts\\python" in command.lower()):
        venv_candidate = os.path.join(workspace, ".venv", "bin", "python")
        if not os.path.exists(venv_candidate):
            recovery["reason"] = "missing venv"
            recovery["commands"] = [
                "python -m venv .venv",
                "python -m ensurepip --upgrade",
                f"{pip_cmd} install -U pip setuptools wheel",
            ]
            return recovery

    if python_signals and ("no module named pip" in text or "pip: command not found" in text):
        recovery["reason"] = "pip missing"
        recovery["commands"] = [
            "python -m ensurepip --upgrade",
            f"{pip_cmd} install -U pip setuptools wheel",
        ]
        return recovery

    if python_signals and (
        "could not open requirements file" in text
        or ("no such file or directory" in text and "requirements" in text)
    ):
        req_file = _candidate_requirements_files(workspace)
        if req_file:
            recovery["reason"] = "requirements file missing; fallback to available requirements"
            recovery["commands"] = [f"{pip_cmd} install -r {req_file}"]
            return recovery
        if packaging["poetry"]:
            recovery["reason"] = "requirements missing; poetry project detected"
            commands = []
            if shutil.which("poetry") is None:
                commands.append(f"{pip_cmd} install -U poetry")
            commands.append("poetry install")
            recovery["commands"] = commands
            return recovery
        if packaging["pipfile"]:
            recovery["reason"] = "requirements missing; pipenv project detected"
            commands = []
            if shutil.which("pipenv") is None:
                commands.append(f"{pip_cmd} install -U pipenv")
            commands.append("pipenv install --dev")
            recovery["commands"] = commands
            return recovery
        if packaging["pyproject"] or packaging["setup_py"] or packaging["setup_cfg"]:
            recovery["reason"] = "requirements missing; install project package"
            recovery["commands"] = [f"{pip_cmd} install -e ."]
            return recovery

    if python_signals and ("no module named pytest" in text or "pytest: command not found" in text):
        recovery["reason"] = "pytest missing"
        req_file = _candidate_requirements_files(workspace)
        if req_file:
            recovery["commands"] = [f"{pip_cmd} install -r {req_file}"]
        else:
            recovery["commands"] = [f"{pip_cmd} install -U pytest"]
        return recovery

    if python_signals and (
        "no module named" in text
        and ("pip install" in command or "-m pytest" in command or "pytest" in command)
    ):
        if packaging["pyproject"] or packaging["setup_py"] or packaging["setup_cfg"]:
            recovery["reason"] = "dependency missing; install project package"
            recovery["commands"] = [f"{pip_cmd} install -e ."]
            return recovery
        req_file = _candidate_requirements_files(workspace)
        if req_file:
            recovery["reason"] = "dependency missing; reinstall requirements"
            recovery["commands"] = [f"{pip_cmd} install -r {req_file}"]
            return recovery

    if python_signals and ("error: invalid command 'bdist_wheel'" in text or "bdist_wheel" in text):
        recovery["reason"] = "wheel missing"
        recovery["commands"] = [f"{pip_cmd} install -U wheel"]
        return recovery

    if python_signals and "poetry: command not found" in text and packaging["poetry"]:
        recovery["reason"] = "poetry missing"
        recovery["commands"] = [f"{pip_cmd} install -U poetry", "poetry install"]
        return recovery

    if python_signals and "pipenv: command not found" in text and packaging["pipfile"]:
        recovery["reason"] = "pipenv missing"
        recovery["commands"] = [f"{pip_cmd} install -U pipenv", "pipenv install --dev"]
        return recovery

    if "pytest" in command and ("failed" in text or "error" in text) and "--lf" not in command:
        recovery["reason"] = "pytest failure; retry last failed tests"
        recovery["commands"] = [command + " --lf"]
        return recovery

    if ("go: command not found" in text or "go: not found" in text) and "go" in languages:
        recovery["reason"] = "go toolchain missing"
        recovery["commands"] = []
        return recovery

    if ("cargo: command not found" in text or "rustc: command not found" in text) and "rust" in languages:
        recovery["reason"] = "rust toolchain missing"
        recovery["commands"] = []
        return recovery

    if ("gcc: command not found" in text or "g++: command not found" in text) and ("c" in languages or "cpp" in languages):
        recovery["reason"] = "c/c++ toolchain missing"
        recovery["commands"] = []
        return recovery

    if ("gfortran: command not found" in text or "fortran: command not found" in text) and "fortran" in languages:
        recovery["reason"] = "fortran toolchain missing"
        recovery["commands"] = []
        return recovery

    if ("fpc: command not found" in text or "pascal: command not found" in text) and "pascal" in languages:
        recovery["reason"] = "pascal toolchain missing"
        recovery["commands"] = []
        return recovery

    if packaging["package_json"] and ("npm: command not found" in text or "node: command not found" in text):
        recovery["reason"] = "node tooling missing"
        recovery["commands"] = []
        return recovery

    return None


def _classify_failure(command: str, result: Dict[str, Any]) -> Dict[str, str]:
    stdout = result.get("stdout") or ""
    stderr = result.get("stderr") or ""
    combined = (stdout + "\n" + stderr).lower()
    summary = _tail_output(stdout or stderr)

    if "timed out" in combined or "timeout" in combined:
        return {"failure_type": "timeout", "failure_summary": summary}
    if "permission denied" in combined:
        return {"failure_type": "permission_denied", "failure_summary": summary}
    if "pytest" in command:
        if "no module named" in combined or "importerror" in combined:
            return {"failure_type": "pytest_import_error", "failure_summary": summary}
        if "failed" in combined or "errors" in combined or "error" in combined:
            return {"failure_type": "test_failure", "failure_summary": summary}
    if "no such file or directory" in combined:
        return {"failure_type": "missing_file", "failure_summary": summary}
    if "command not found" in combined:
        return {"failure_type": "missing_command", "failure_summary": summary}
    return {"failure_type": "unknown", "failure_summary": summary}


def _write_solver_context(
    *,
    output_dir: str,
    project_root: str,
    solver_workspace: Optional[str],
    stage_name: str,
    command: str,
    failure_type: str,
    failure_summary: str,
) -> str:
    context_dir = os.path.join(output_dir, "solver_context")
    _safe_mkdir(context_dir)
    filename = f"{stage_name}_failure_context.md"
    path = os.path.join(context_dir, filename)
    focus = _focus_files_from_failure(project_root, failure_summary or "")
    focus_files = focus.get("focus_files") or []
    req_ids = focus.get("req_ids") or []
    hints = focus.get("hints") or []
    recent_git = _recent_git_changes(project_root)
    recent_workspace = _recent_solver_workspace_changes(solver_workspace)
    focus_block = ""
    if focus_files or req_ids or hints:
        lines = ["Focus hints:"]
        if req_ids:
            lines.append(f"- Requirement IDs: {', '.join(req_ids)}")
        if hints:
            lines.append(f"- Symbols: {', '.join(hints)}")
        if focus_files:
            lines.append("- Files/excerpts:")
            for item in focus_files[:4]:
                entry = f"  - {item.get('path')}"
                if item.get("line"):
                    entry += f":{item.get('line')}"
                lines.append(entry)
                excerpt = (item.get("excerpt") or "").strip()
                if excerpt:
                    excerpt_lines = "\n".join(f"    {line}" for line in excerpt.splitlines()[:6])
                    lines.append(excerpt_lines)
        focus_block = "\n".join(lines) + "\n\n"
    recent_block = ""
    if recent_git or recent_workspace:
        lines = ["Recent change hints (for prioritization):"]
        if recent_git:
            lines.append("- git status/diff:")
            for path in recent_git:
                lines.append(f"  - {path}")
        if recent_workspace:
            lines.append("- recent workspace files:")
            for recent_path in recent_workspace[:6]:
                lines.append(f"  - {recent_path}")
        recent_block = "\n".join(lines) + "\n\n"
    content = (
        "# Delivery pipeline solver context\n\n"
        f"Stage: {stage_name}\n"
        f"Command: `{command}`\n"
        f"Failure type: {failure_type}\n\n"
        "Failure summary:\n\n"
        f"{failure_summary}\n\n"
        f"{focus_block}"
        f"{recent_block}"
        "Objective:\n"
        "- Fix the failing build/tests in the project source code.\n"
        "- Prefer minimal, targeted changes.\n"
        "- Do not alter tests unless absolutely required by requirements.\n"
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def _load_solver_summary(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    try:
        data = _read_json(path)
    except Exception:
        return {}
    summary = data.get("completion_summary") if isinstance(data, dict) else None
    return summary if isinstance(summary, dict) else {}


def _extract_req_ids(text: str) -> List[str]:
    if not text:
        return []
    return sorted({match.group(0) for match in re.finditer(r"REQ-\\d+", text, re.IGNORECASE)})


def _extract_paths(text: str) -> List[Dict[str, str]]:
    if not text:
        return []
    pattern = re.compile(
        r"(?P<path>[\\w./\\\\-]+\\.(?:py|rs|go|c|cc|cpp|cxx|h|hpp|hxx|js|ts|tsx|jsx|java|cs|rb|php|sh|ps1)):(?P<line>\\d+)",
        re.IGNORECASE,
    )
    results = []
    for match in pattern.finditer(text):
        results.append({"path": match.group("path"), "line": match.group("line")})
    # de-dupe by path
    seen = set()
    deduped = []
    for item in results:
        path = item["path"]
        if path in seen:
            continue
        seen.add(path)
        deduped.append(item)
    return deduped


def _extract_symbol_hints(text: str) -> List[str]:
    if not text:
        return []
    hints = set()
    attr = re.search(r"AttributeError: '([^']+)' object has no attribute '([^']+)'", text)
    if attr:
        hints.add(attr.group(1))
        hints.add(attr.group(2))
    name_err = re.search(r"NameError: name '([^']+)' is not defined", text)
    if name_err:
        hints.add(name_err.group(1))
    for match in re.finditer(r"\\b([A-Za-z_][A-Za-z0-9_]*)\\b", text):
        token = match.group(1)
        if len(token) >= 4 and token[0].isupper():
            hints.add(token)
    return sorted(hints)


def _looks_like_requirements_doc(path: str) -> bool:
    if not path:
        return False
    ext = os.path.splitext(path)[1].lower()
    doc_exts = {
        ".md",
        ".txt",
        ".rst",
        ".adoc",
        ".pdf",
        ".docx",
        ".odt",
        ".html",
        ".htm",
        ".json",
        ".yaml",
        ".yml",
    }
    code_exts = {
        ".py",
        ".rs",
        ".go",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
        ".hxx",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".cs",
        ".rb",
        ".php",
        ".sh",
        ".ps1",
    }
    if ext in doc_exts:
        return True
    if ext in code_exts:
        return False
    if not ext:
        return True
    basename = os.path.basename(path).lower()
    return "requirement" in basename or "spec" in basename


def _excerpt_for_path(path: str, line: Optional[int]) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
    except Exception:
        return ""
    if not lines:
        return ""
    idx = (line - 1) if line and line > 0 else 0
    start = max(idx - 3, 0)
    end = min(idx + 3, len(lines))
    excerpt = "".join(lines[start:end]).strip()
    return excerpt


def _focus_files_from_failure(project_root: str, summary: str) -> Dict[str, Any]:
    paths = _extract_paths(summary)
    req_ids = _extract_req_ids(summary)
    hints = _extract_symbol_hints(summary)
    focus_files: List[Dict[str, Any]] = []

    for entry in paths[:4]:
        raw_path = entry.get("path")
        line = int(entry.get("line") or 0)
        abs_path = raw_path
        if raw_path and not os.path.isabs(raw_path):
            abs_path = os.path.join(project_root, raw_path)
        excerpt = _excerpt_for_path(abs_path, line)
        focus_files.append(
            {
                "path": raw_path,
                "line": line,
                "excerpt": excerpt,
            }
        )

    if len(focus_files) < 4 and hints:
        try:
            repo_index = RepoIndex.build(project_root, max_files=200)
            for hint in hints:
                matches = repo_index.search(hint, limit=3)
                for match in matches:
                    if len(focus_files) >= 4:
                        break
                    focus_files.append({"path": match.path, "line": None, "excerpt": match.excerpt})
        except Exception:
            pass

    return {"focus_files": focus_files, "req_ids": req_ids, "hints": hints}


def _recent_git_changes(project_root: str, max_files: int = 8) -> List[str]:
    if not os.path.isdir(project_root):
        return []
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    paths = []
    for line in (result.stdout or "").splitlines():
        if not line:
            continue
        path = line[3:].strip()
        if path and path not in paths:
            paths.append(path)
        if len(paths) >= max_files:
            break
    if paths:
        return paths
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "diff", "--name-only", "HEAD~1"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    for line in (result.stdout or "").splitlines():
        path = line.strip()
        if path and path not in paths:
            paths.append(path)
        if len(paths) >= max_files:
            break
    return paths


def _recent_solver_workspace_changes(workspace_root: Optional[str], max_files: int = 8) -> List[str]:
    if not workspace_root or not os.path.isdir(workspace_root):
        return []
    candidates: List[Tuple[float, str]] = []
    for dirpath, dirs, files in os.walk(workspace_root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
        for filename in files:
            if filename.endswith((".py", ".rs", ".go", ".c", ".cpp", ".h", ".js", ".ts", ".tsx", ".jsx")):
                path = os.path.join(dirpath, filename)
                try:
                    mtime = os.path.getmtime(path)
                except Exception:
                    continue
                candidates.append((mtime, path))
        if len(candidates) > 2000:
            break
    candidates.sort(reverse=True)
    recent = [path for _, path in candidates[:max_files]]
    return recent


def _approval_status(
    stage: PipelineStage,
    approvals_cfg: Dict[str, str],
    project_root: str,
    default_base_dir: str,
) -> Tuple[bool, str]:
    if not stage.requires_approval:
        return True, ""

    approval_file = stage.approval_file
    if approval_file:
        approval_path = _resolve_path(project_root, approval_file)
    else:
        base_dir = approvals_cfg.get("base_dir") or default_base_dir
        suffix = approvals_cfg.get("default_suffix") or ".ok"
        approval_path = _resolve_path(project_root, os.path.join(base_dir, f"{stage.name}{suffix}"))

    if approval_path and os.path.exists(approval_path):
        return True, approval_path
    return False, approval_path or ""


def _load_project_solution(path: str) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    if not os.path.exists(path):
        return None
    try:
        data = _read_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _solver_summary(data: Optional[Dict[str, Any]], project_root: str) -> Dict[str, Any]:
    if not data:
        return {}
    completion = data.get("completion_summary") if isinstance(data.get("completion_summary"), dict) else {}
    solver_workspace = data.get("solver_workspace")
    solver_workspace_path = None
    if isinstance(solver_workspace, str) and solver_workspace:
        solver_workspace_path = _resolve_path(project_root, solver_workspace)
    return {
        "completion_summary": completion,
        "solver_workspace": solver_workspace,
        "solver_workspace_path": solver_workspace_path,
        "needs_more_iterations": bool(completion.get("needs_more_iterations")),
        "unresolved_verification_failures": completion.get("unresolved_verification_failures") or [],
    }


def _is_deploy_stage(stage: PipelineStage) -> bool:
    if stage.requires_solver_completion:
        return True
    kind = (stage.kind or stage.name or "").strip().lower()
    deploy_kinds = {"deploy", "delivery", "release", "production", "prod", "staging", "uat"}
    return kind in deploy_kinds


def run_delivery_pipeline(
    project_root: str,
    *,
    config_path: str,
    output_path: Optional[str] = None,
    allow_run: bool = False,
    project_solution_path: Optional[str] = None,
    allow_unfinished: bool = False,
    enable_interim: bool = False,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    fallback_llm_provider: Optional[str] = None,
    fallback_llm_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    llm_max_tokens: Optional[int] = None,
    llm_temperature: float = 0.2,
    llm_timeout: Optional[int] = None,
    llm_reasoning_effort: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    fallback_llm_api_key: Optional[str] = None,
    codingagent: Optional[str] = None,
    codingagent_fallback: Optional[str] = None,
    codingagent_model: Optional[str] = None,
    codingagent_reasoning_effort: Optional[str] = None,
    project_output_dir: Optional[str] = None,
    solver_fallback_override: Optional[bool] = None,
) -> int:
    if not os.path.isdir(project_root):
        raise ValueError(f"Project root is not a directory: {project_root}")

    config = load_pipeline_config(config_path)
    if enable_interim:
        for stage in config.stages:
            if stage.name in {"interim_deploy", "interim_teardown"}:
                stage.enabled = True
    solver_cfg = config.solver_fallback or {}
    solver_enabled = bool(solver_cfg.get("enabled", False))
    if solver_fallback_override is not None:
        solver_enabled = bool(solver_fallback_override)
    solver_max_attempts = int(solver_cfg.get("max_attempts") or 1)
    solver_failure_types = set(
        [str(item) for item in (solver_cfg.get("on_failure_types") or ["test_failure", "pytest_import_error"])]
    )
    solver_allow_run = bool(solver_cfg.get("allow_run", False))
    solver_max_steps = int(solver_cfg.get("max_steps") or 25)
    solver_max_iterations = int(solver_cfg.get("max_iterations") or 2)
    solver_requirements_path = solver_cfg.get("requirements_path")
    solver_requirements_only = bool(solver_cfg.get("requirements_only", False))
    solver_use_workspace = bool(solver_cfg.get("use_workspace", True))
    solver_project_output_dir = solver_cfg.get("project_output_dir") or project_output_dir
    solver_llm_provider = solver_cfg.get("llm_provider") or llm_provider
    solver_llm_model = solver_cfg.get("llm_model") or llm_model
    solver_fallback_provider = solver_cfg.get("fallback_llm_provider") or fallback_llm_provider
    solver_fallback_model = solver_cfg.get("fallback_llm_model") or fallback_llm_model
    solver_ollama_base_url = solver_cfg.get("ollama_base_url") or ollama_base_url
    solver_codingagent = solver_cfg.get("codingagent") or codingagent
    solver_codingagent_fallback = solver_cfg.get("codingagent_fallback") or codingagent_fallback
    solver_codingagent_model = solver_cfg.get("codingagent_model") or codingagent_model
    solver_codingagent_reasoning = solver_cfg.get("codingagent_reasoning_effort") or codingagent_reasoning_effort
    solver_llm_api_key = solver_cfg.get("llm_api_key") or llm_api_key
    solver_fallback_api_key = solver_cfg.get("fallback_llm_api_key") or fallback_llm_api_key
    solver_llm_temperature = (
        float(solver_cfg.get("llm_temperature"))
        if solver_cfg.get("llm_temperature") is not None
        else llm_temperature
    )
    solver_llm_max_tokens = solver_cfg.get("llm_max_tokens") or llm_max_tokens
    solver_llm_timeout = solver_cfg.get("llm_timeout") or llm_timeout
    solver_llm_reasoning = solver_cfg.get("llm_reasoning_effort") or llm_reasoning_effort

    if solver_enabled and not solver_llm_provider:
        logger.warning("Solver fallback enabled but no llm_provider configured; disabling solver fallback.")
        solver_enabled = False
    version = _compute_version(project_root, config.versioning)

    output_dir = _resolve_path(project_root, config.output_dir) or os.path.join(project_root, DEFAULT_OUTPUT_DIR)
    workspace_root = _resolve_path(project_root, config.workspace_root) or os.path.join(output_dir, "workspaces")
    artifacts_root = _resolve_path(project_root, config.artifacts_root) or os.path.join(output_dir, "artifacts")
    approvals_cfg = config.approvals or {}
    approvals_dir = _resolve_path(project_root, approvals_cfg.get("base_dir")) or os.path.join(output_dir, "approvals")

    _safe_mkdir(output_dir)
    _safe_mkdir(workspace_root)
    _safe_mkdir(artifacts_root)
    _safe_mkdir(approvals_dir)

    if not output_path:
        output_path = os.path.join(output_dir, f"pipeline_report_{version}.json")

    version_file = _write_version_file(
        version,
        versioning=config.versioning,
        project_root=project_root,
        output_dir=output_dir,
    )

    platform_selection = select_platform(project_root, config.platform)
    platform_details = {
        "tier": platform_selection.tier,
        "engine": platform_selection.engine,
        "provider": platform_selection.provider,
        "available": platform_selection.available,
        "reason": platform_selection.reason,
        "detected": platform_selection.detected,
        "env": platform_selection.env,
    }
    language_details = detect_languages(project_root)

    solver_data = _load_project_solution(project_solution_path) if project_solution_path else None
    solver_summary = _solver_summary(solver_data, project_root)
    solver_incomplete = bool(solver_summary.get("needs_more_iterations"))
    solver_gate = config.solver_gate
    if config.require_solver_completion:
        solver_gate = "block_all"
    if config.allow_unfinished_deploy or allow_unfinished:
        solver_gate = "warn"

    if solver_gate == "block_all" and solver_incomplete:
        report = {
            "status": "blocked",
            "blocked_reason": "project_solver_incomplete",
            "version": version,
            "project_root": project_root,
            "config_path": config_path,
            "version_file": version_file,
            "solver_summary": solver_summary,
            "platform": platform_details,
            "languages": language_details,
            "solver_gate": solver_gate,
            "solver_incomplete": solver_incomplete,
            "allow_unfinished_deploy": bool(config.allow_unfinished_deploy or allow_unfinished),
            "solver_fallback": {
                "enabled": solver_enabled,
                "requirements_only": solver_requirements_only,
                "attempts": [],
                "attempt_count": 0,
            },
        }
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        return 2

    vcs_result = run_vcs_workflow(
        project_root,
        config=config.vcs,
        version=version,
        allow_run=allow_run,
        approvals_dir=approvals_dir,
    )
    vcs_details = vcs_result.details

    if vcs_result.status in {"failed", "blocked"}:
        report = {
            "status": vcs_result.status,
            "blocked_reason": vcs_details.get("blocked_reason") if vcs_result.status == "blocked" else None,
            "version": version,
            "project_root": project_root,
            "config_path": config_path,
            "output_dir": output_dir,
            "workspace_root": workspace_root,
            "artifacts_root": artifacts_root,
            "approvals_dir": approvals_dir,
            "version_file": version_file,
            "allow_run": allow_run,
            "solver_summary": solver_summary,
            "vcs": vcs_details,
            "platform": platform_details,
            "languages": language_details,
            "solver_gate": solver_gate,
            "solver_incomplete": solver_incomplete,
            "allow_unfinished_deploy": bool(config.allow_unfinished_deploy or allow_unfinished),
            "solver_fallback": {
                "enabled": solver_enabled,
                "requirements_only": solver_requirements_only,
                "attempts": [],
                "attempt_count": 0,
            },
            "stages": [],
            "workflow": None,
        }
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        return 2 if vcs_result.status == "blocked" else 1

    ignore = _build_ignore(project_root, output_dir, config.extra_ignored)

    workflow = AgenticWorkflow(
        phases=[stage.name for stage in config.stages],
        max_cycles=1,
        logger=logger,
        label="delivery_pipeline",
    )
    cycle = workflow.start_cycle(1, context={"project_root": project_root, "version": version})

    stage_results: List[Dict[str, Any]] = []
    solver_fallback_log: List[Dict[str, Any]] = []
    pipeline_status = "success"
    blocked = False
    failed = False

    for stage in config.stages:
        stage_start = time.time()
        stage_retry_attempts: Optional[int] = None
        stage_auto_recover: Optional[bool] = None
        approval_present, approval_path = _approval_status(
            stage,
            approvals_cfg,
            project_root,
            default_base_dir=approvals_dir,
        )
        stage_notes: List[str] = []
        if stage.requires_approval and not approval_present:
            stage_notes.append("approval missing")
        stage_kind = stage.kind or stage.name
        if not stage.enabled:
            stage_result = {
                "name": stage.name,
                "description": stage.description,
                "kind": stage.kind,
                "status": "skipped_disabled",
                "workspace": None,
                "workspace_mode": stage.workspace_mode,
                "approval_required": stage.requires_approval,
                "approval_present": approval_present,
                "approval_file": approval_path,
                "commands": stage.commands,
                "command_results": [],
                "recovery_actions": [],
                "solver_attempts": [],
                "artifacts": [],
                "notes": stage_notes + ["disabled"],
                "duration_sec": round(time.time() - stage_start, 2),
            }
            stage_results.append(stage_result)
            continue
        if allow_run and solver_incomplete and solver_gate == "block_deploy" and _is_deploy_stage(stage):
            stage_status = "blocked"
            blocked = True
            phase_result = PhaseResult.halt("solver incomplete")
            stage_notes.append("solver incomplete; deploy stage gated")
            command_results: List[Dict[str, Any]] = []
            stage_recoveries: List[Dict[str, Any]] = []
            stage_solver_attempts: List[Dict[str, Any]] = []
            workspace = None
            artifacts = []
        elif allow_run and stage.requires_approval and not approval_present:
            stage_status = "blocked"
            blocked = True
            phase_result = PhaseResult.halt("approval missing")
            command_results = []
            stage_recoveries = []
            stage_solver_attempts = []
            workspace = None
            artifacts = []
        else:
            workspace, workspace_notes = _resolve_stage_workspace(
                stage,
                project_root=project_root,
                workspace_root=workspace_root,
                version=version,
                clean_workspaces=config.clean_workspaces,
                ignore=ignore,
                overlay_root=solver_summary.get("solver_workspace_path"),
                overlay_enabled=config.overlay_solver_workspace,
            )
            stage_notes.extend(workspace_notes)

            command_results = []
            stage_status = "skipped"
            stage_recoveries: List[Dict[str, Any]] = []
            stage_solver_attempts: List[Dict[str, Any]] = []
            stage_retry_attempts = stage.retry_attempts if stage.retry_attempts else config.retry_attempts
            stage_retry_delay = stage.retry_delay_sec if stage.retry_delay_sec else config.retry_delay_sec
            stage_auto_recover = config.auto_recover if stage.auto_recover is None else bool(stage.auto_recover)
            solver_attempts_used = 0

            if allow_run:
                env = os.environ.copy()
                env.update(platform_selection.env)
                env.update(stage.env)
                env.update(
                    {
                        "PIPELINE_STAGE": stage.name,
                        "PIPELINE_STAGE_KIND": stage_kind,
                        "PIPELINE_VERSION": version,
                        "PIPELINE_WORKSPACE": workspace,
                        "PIPELINE_PROJECT_ROOT": project_root,
                        "PIPELINE_SOLVER_INCOMPLETE": str(solver_incomplete).lower(),
                        "PIPELINE_SOLVER_GATE": solver_gate,
                        "PIPELINE_LANGUAGES": ",".join(language_details.get("languages") or []),
                        "PIPELINE_BUILD_SYSTEMS": ",".join(language_details.get("build_systems") or []),
                    }
                )
                if stage.commands:
                    stage_status = "ok"
                    for item in stage.commands:
                        if isinstance(item, dict):
                            command = item.get("command") or item.get("cmd") or ""
                            timeout = int(item.get("timeout_sec") or stage.timeout_sec)
                        else:
                            command = str(item)
                            timeout = stage.timeout_sec
                        command = command.strip()
                        if not command:
                            continue
                        attempt = 0
                        while True:
                            result = _run_command(
                                command,
                                workdir=workspace,
                                env=env,
                                timeout_sec=timeout,
                                stage_name=stage.name,
                                attempt=attempt + 1,
                            )
                            if result["status"] != "ok":
                                result.update(_classify_failure(command, result))
                                logger.warning(
                                    "Failure classified stage=%s type=%s summary=%s",
                                    stage.name,
                                    result.get("failure_type"),
                                    result.get("failure_summary"),
                                )
                            command_results.append(result)
                            if result["status"] == "ok":
                                break
                            if attempt >= stage_retry_attempts:
                                failure_type = result.get("failure_type") or "unknown"
                                failure_summary = result.get("failure_summary") or ""
                                solver_should_try = (
                                    solver_enabled
                                    and allow_run
                                    and solver_attempts_used < solver_max_attempts
                                    and (failure_type in solver_failure_types or "any" in solver_failure_types)
                                )
                                if solver_should_try:
                                    solver_attempts_used += 1
                                    solver_root = workspace if (solver_use_workspace and workspace) else project_root
                                    solver_out_dir = os.path.join(output_dir, "solver_attempts")
                                    _safe_mkdir(solver_out_dir)
                                    solver_out_path = os.path.join(
                                        solver_out_dir, f"{stage.name}_attempt{solver_attempts_used}.json"
                                    )
                                    req_path = None
                                    context_path = None
                                    if solver_requirements_path:
                                        req_candidate = (
                                            solver_requirements_path
                                            if os.path.isabs(str(solver_requirements_path))
                                            else _resolve_path(project_root, str(solver_requirements_path))
                                        )
                                        if not req_candidate or not os.path.exists(req_candidate):
                                            logger.warning(
                                                "Configured solver requirements_path not found: %s", req_candidate
                                            )
                                        elif not _looks_like_requirements_doc(req_candidate):
                                            logger.warning(
                                                "Configured solver requirements_path does not look like a requirements doc: %s",
                                                req_candidate,
                                            )
                                        else:
                                            req_path = req_candidate
                                    if not req_path:
                                        context_path = _write_solver_context(
                                            output_dir=output_dir,
                                            project_root=project_root,
                                            solver_workspace=workspace,
                                            stage_name=stage.name,
                                            command=command,
                                            failure_type=failure_type,
                                            failure_summary=failure_summary,
                                        )
                                        req_path = context_path
                                        logger.info("Solver context written: %s", context_path)
                                    logger.warning(
                                        "Solver requirements: path=%s requirements_only=%s",
                                        req_path,
                                        solver_requirements_only,
                                    )
                                    logger.warning(
                                        "Invoking project_solver for stage %s (attempt %s)",
                                        stage.name,
                                        solver_attempts_used,
                                    )
                                    solver_exit = run_project_solver(
                                        solver_root,
                                        requirements_path=req_path,
                                        requirements_only=solver_requirements_only,
                                        output_path=solver_out_path,
                                        llm_provider=solver_llm_provider,
                                        llm_model=solver_llm_model,
                                        ollama_base_url=solver_ollama_base_url,
                                        llm_max_tokens=solver_llm_max_tokens,
                                        llm_temperature=solver_llm_temperature,
                                        llm_timeout=solver_llm_timeout,
                                        llm_reasoning_effort=solver_llm_reasoning,
                                        llm_api_key=solver_llm_api_key,
                                        fallback_llm_provider=solver_fallback_provider,
                                        fallback_llm_model=solver_fallback_model,
                                        fallback_llm_api_key=solver_fallback_api_key,
                                        llm_inter_request_gap=0.0,
                                        allow_run=solver_allow_run,
                                        max_steps=solver_max_steps,
                                        max_iterations=solver_max_iterations,
                                        project_output_dir=solver_project_output_dir,
                                        codingagent=solver_codingagent,
                                        codingagent_fallback=solver_codingagent_fallback,
                                        codingagent_model=solver_codingagent_model,
                                        codingagent_reasoning_effort=solver_codingagent_reasoning,
                                    )
                                    solver_summary = _load_solver_summary(solver_out_path)
                                    solver_attempt = {
                                        "attempt": solver_attempts_used,
                                        "stage": stage.name,
                                        "failure_type": failure_type,
                                        "requirements_path": req_path,
                                        "requirements_only": solver_requirements_only,
                                        "context_path": context_path,
                                        "solver_root": solver_root,
                                        "output_path": solver_out_path,
                                        "exit_code": solver_exit,
                                        "summary": solver_summary,
                                    }
                                    stage_solver_attempts.append(solver_attempt)
                                    solver_fallback_log.append(solver_attempt)
                                    if solver_exit == 0:
                                        attempt = 0
                                        continue
                                stage_status = "failed"
                                if not stage.allow_failure:
                                    failed = True
                                break
                            recovery = None
                            if stage_auto_recover:
                                recovery = _plan_recovery(
                                    command=command,
                                    result=result,
                                    workspace=workspace,
                                )
                            if recovery:
                                logger.warning(
                                    "Attempting recovery for stage %s: %s",
                                    stage.name,
                                    recovery.get("reason"),
                                )
                                recovery_results = []
                                for rec_cmd in recovery.get("commands") or []:
                                    rec_result = _run_command(
                                        rec_cmd,
                                        workdir=workspace,
                                        env=env,
                                        timeout_sec=timeout,
                                        stage_name=stage.name,
                                        attempt=attempt + 1,
                                    )
                                    recovery_results.append(rec_result)
                                stage_recoveries.append(
                                    {
                                        "reason": recovery.get("reason"),
                                        "commands": recovery.get("commands"),
                                        "results": recovery_results,
                                    }
                                )
                            else:
                                stage_recoveries.append(
                                    {
                                        "reason": "retry without recovery",
                                        "commands": [],
                                        "results": [],
                                    }
                                )
                            if stage_retry_delay:
                                time.sleep(stage_retry_delay)
                            attempt += 1
                            continue
                        if stage_status == "failed" and failed:
                            break
                else:
                    stage_status = "no_op"

                if stage_status in {"ok", "no_op"}:
                    phase_result = PhaseResult.ok("stage completed")
                elif stage.allow_failure:
                    phase_result = PhaseResult.ok("stage failed (allowed)")
                else:
                    phase_result = PhaseResult.error("stage failed")
            else:
                stage_status = "planned"
                phase_result = PhaseResult.ok("dry-run")

            artifacts = _collect_artifacts(
                stage,
                workspace=workspace,
                artifacts_root=artifacts_root,
                version=version,
            )

        stage_result = {
            "name": stage.name,
            "description": stage.description,
            "kind": stage.kind,
            "status": stage_status,
            "workspace": workspace,
            "workspace_mode": stage.workspace_mode,
            "approval_required": stage.requires_approval,
            "approval_present": approval_present,
            "approval_file": approval_path,
            "commands": stage.commands,
            "command_results": command_results,
            "recovery_actions": stage_recoveries,
            "solver_attempts": stage_solver_attempts,
            "artifacts": artifacts,
            "notes": stage_notes,
            "duration_sec": round(time.time() - stage_start, 2),
            "retry_attempts": stage_retry_attempts,
            "auto_recover": stage_auto_recover,
        }
        stage_results.append(stage_result)
        cycle.record(stage.name, phase_result)

        if blocked or failed:
            break

    if blocked:
        pipeline_status = "blocked"
    elif failed:
        pipeline_status = "failed"
    elif not allow_run:
        pipeline_status = "planned"
    elif solver_incomplete and solver_gate == "warn":
        pipeline_status = "warn"

    stage_counts: Dict[str, int] = {}
    failed_stage_names: List[str] = []
    for stage in stage_results:
        status = stage.get("status") if isinstance(stage, dict) else None
        if status:
            stage_counts[status] = stage_counts.get(status, 0) + 1
        if status in {"failed", "blocked", "error"}:
            failed_stage_names.append(str(stage.get("name") or ""))
    run_summary = {
        "stage_counts": stage_counts,
        "failed_stages": [name for name in failed_stage_names if name],
        "solver_fallback_attempts": len(solver_fallback_log),
    }
    logger.info("Delivery pipeline summary: status=%s stages=%s solver_attempts=%s", pipeline_status, stage_counts, len(solver_fallback_log))

    report = {
        "status": pipeline_status,
        "version": version,
        "project_root": project_root,
        "config_path": config_path,
        "output_dir": output_dir,
        "workspace_root": workspace_root,
        "artifacts_root": artifacts_root,
        "approvals_dir": approvals_dir,
        "version_file": version_file,
        "allow_run": allow_run,
        "solver_summary": solver_summary,
        "vcs": vcs_details,
        "platform": platform_details,
        "languages": language_details,
        "solver_gate": solver_gate,
        "solver_incomplete": solver_incomplete,
        "allow_unfinished_deploy": bool(config.allow_unfinished_deploy or allow_unfinished),
        "solver_fallback": {
            "enabled": solver_enabled,
            "requirements_only": solver_requirements_only,
            "attempts": solver_fallback_log,
            "attempt_count": len(solver_fallback_log),
        },
        "summary": run_summary,
        "stages": stage_results,
        "workflow": workflow.export(),
    }

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    if blocked:
        return 2
    if failed:
        return 1
    return 0
