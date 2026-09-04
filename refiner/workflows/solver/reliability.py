"""Small, deterministic reliability primitives used by the project solver.

The project solver receives plans from models, so the boundary between a plan
and the local filesystem/process table must remain explicit and testable.  The
helpers in this module deliberately have no Refiner imports; they can be used
by the CLI, the API worker, and focused tests without starting a workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import signal
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


CODE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".ejs", ".jsx", ".php", ".py", ".rb", ".rs", ".sh", ".sql", ".ts", ".tsx", ".css",
}
MANIFEST_NAMES = {
    "cargo.toml", "go.mod", "package.json", "package-lock.json", "pnpm-lock.yaml",
    "poetry.lock", "pyproject.toml", "requirements-dev.txt", "requirements.txt",
    "setup.cfg", "setup.py", "uv.lock", "readme.md", "readme.rst",
}


@dataclass(frozen=True)
class CommandSpec:
    """A shell-free command and its execution directory."""

    argv: Tuple[str, ...]
    workdir: str = "."
    env: Tuple[Tuple[str, str], ...] = ()
    timeout: int = 600

    @property
    def display(self) -> str:
        import shlex

        return shlex.join(self.argv)


def command_spec_from_step(step: Dict[str, Any]) -> Optional[CommandSpec]:
    """Normalise legacy ``command`` and preferred structured ``argv`` plans.

    ``argv`` is preferred because it makes shell operators unrepresentable as
    control syntax.  Legacy string commands remain supported for compatibility
    and are parsed once by the command policy before execution.
    """

    if not isinstance(step, dict):
        return None
    raw_argv = step.get("argv")
    if isinstance(raw_argv, (list, tuple)):
        # Preserve empty arguments: argv is an exact process boundary, not a
        # shell-like token stream. The policy layer rejects an empty
        # executable while retaining legitimate empty data arguments.
        argv = tuple(str(item) for item in raw_argv)
    else:
        command = step.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        # Keep parsing in the policy module for legacy strings.  Returning a
        # one-item marker here lets callers preserve the original string.
        argv = (command.strip(),)
    if not argv:
        return None
    workdir = step.get("workdir")
    workdir = str(workdir).strip() if workdir else "."
    raw_env = step.get("env")
    env: List[Tuple[str, str]] = []
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            key_text = str(key).strip()
            if key_text:
                env.append((key_text, str(value)))
    timeout = step.get("timeout", 600)
    try:
        timeout_value = max(1, int(timeout))
    except (TypeError, ValueError):
        timeout_value = 600
    return CommandSpec(argv=argv, workdir=workdir, env=tuple(env), timeout=timeout_value)


def canonical_project_target(
    requested_path: str,
    *,
    project_root: str,
    workspace_root: Optional[str] = None,
    step_type: str = "write_file",
) -> Tuple[str, Optional[str]]:
    """Return a canonical target, keeping project code and manifests together.

    A workspace nested in the project is intentionally *not* used as a second
    source tree.  This prevents a generated ``package.json`` being separated
    from its ``server.js`` or HTML assets.  Explicit absolute workspace paths
    remain supported for environments/artifacts outside the project root.
    """

    requested = str(requested_path or "").strip()
    if not requested:
        return requested, None
    if os.path.isabs(requested):
        return requested, None
    normalized = requested.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized == ".." or normalized.startswith("../"):
        # Preserve traversal for the caller's containment check; never turn
        # an unsafe path into a safe-looking basename here.
        return requested, None
    workspace_name = os.path.basename(os.path.abspath(workspace_root)) if workspace_root else ""
    while workspace_name and normalized.startswith(workspace_name + "/"):
        normalized = normalized[len(workspace_name) + 1 :]
    basename = os.path.basename(normalized).lower()
    extension = os.path.splitext(basename)[1]
    # Source, tests, documentation, manifests, and web resources need one
    # shared cwd. Other generated material may still be redirected by the
    # caller when desired. Documentation is a repository deliverable just
    # like source and README files; isolating ``docs/`` would make a solver
    # appear to succeed while leaving the requested documentation out of the
    # commit.
    canonical = (
        extension in CODE_EXTENSIONS
        or basename in MANIFEST_NAMES
        or normalized.startswith((
            "docs/", "tests/", "test/", "src/", "public/", "static/", "templates/"
        ))
        or basename in {
            "index.html",
            "index.css",
            "index.js",
            "server.js",
            "app.js",
            "main.py",
            # Contract-validation reports are repository deliverables and
            # must remain alongside the project-root test that verifies them.
            "validation_report.md",
        }
    )
    if canonical or step_type in {"append_file", "replace_in_file"}:
        return normalized, "Canonical project-root target: {}".format(requested)
    return normalized, None


def terminate_process_tree(process: subprocess.Popen, *, grace_seconds: float = 3.0) -> None:
    """Terminate a managed process and children without leaving dev servers."""

    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=grace_seconds)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=grace_seconds)
    except (OSError, subprocess.TimeoutExpired):
        pass


def plan_fingerprint(plan_steps: Iterable[Dict[str, Any]]) -> str:
    """Create a stable fingerprint for repeated/no-progress plans."""

    import json

    relevant: List[Dict[str, Any]] = []
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        relevant.append({
            key: step.get(key)
            for key in ("type", "path", "command", "argv", "workdir", "find", "replace", "content")
            if key in step
        })
    payload = json.dumps(relevant, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def select_planner_role(*, fast_planner_available: bool, source_requires_code: bool) -> str:
    """Select the latency-oriented role only for routine, non-code work."""

    if fast_planner_available and not source_requires_code:
        return "fast_planner"
    return "planner"
