"""Command policy for the project solver.

The solver accepts shell commands from model-generated plans. Refiner already
limits *where* those commands run, but historically it still executed them via
``shell=True`` with only light sanitisation. This module adds a stricter,
deterministic gate:

- reject obviously dangerous or destructive commands,
- reject shell control operators that invite prompt-injection style chaining,
- keep dependency installs and verification commands usable, and
- prepare a ``subprocess.run(..., shell=False)`` invocation payload.

The design intentionally borrows the *intent* of a permission system without
copying any Claude Code implementation details. Refiner is non-interactive in
this path, so the safest behaviour is to block risky commands outright and log
why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import shlex
from typing import Dict, List, Optional, Sequence, Union


_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SHELL_META_RE = re.compile(r"(\|\||&&|[|;`<>]|\$\(|\n)")
_SUDO_RE = re.compile(r"\b(?:sudo|su|doas)\b", re.IGNORECASE)
_SHELL_BOOTSTRAP_RE = re.compile(
    r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:bash|sh|zsh|fish)\b",
    re.IGNORECASE,
)
_DESTRUCTIVE_RE = re.compile(
    r"(\brm\b[^\n]*(?:-rf|-fr)\s+/($|\s))"
    r"|(\bgit\s+reset\s+--hard\b)"
    r"|(\bgit\s+clean\b[^\n]*\s-f)"
    r"|(\bgit\s+checkout\s+--\b)"
    r"|(\bgit\s+restore\b[^\n]*\s--source\b)"
    r"|(\b(?:mkfs|fdisk|parted|shutdown|reboot|halt|poweroff|mount|umount|dd)\b)"
    r"|(:\(\)\s*\{)",
    re.IGNORECASE,
)
_BLOCKED_EXECUTABLES = {
    "bash",
    "sh",
    "zsh",
    "fish",
    "sudo",
    "su",
    "doas",
    "powershell",
    "pwsh",
    "cmd.exe",
}
# ``ls-remote`` only reads refs from a remote.  It is required for the
# delivery gate to verify that a solver-pushed branch is visible on GitHub;
# treating it as a mutation leaves otherwise successful no-change jobs stuck
# in the unresolved-failure path.
_READ_ONLY_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "rev-parse",
    "ls-remote",
}
_PACKAGE_MANAGERS = {"pip", "pip3", "npm", "pnpm", "yarn", "cargo", "go", "uv"}
# Read-only workspace inspection is part of verification.  Solver commands
# are executed without a shell and from the bounded solver workspace, so these
# tools cannot introduce shell chaining or privilege escalation.  Keeping the
# list explicit preserves strict mode's prohibition on arbitrary commands.
_READ_ONLY_INSPECTION_COMMANDS = {
    "cat",
    "cut",
    "echo",
    "file",
    "grep",
    "head",
    "ls",
    "od",
    "pwd",
    "rg",
    "sed",
    "stat",
    "tail",
    "test",
    "tr",
    "wc",
}
_STRICT_ALLOWED_CATEGORIES = {"verification", "vcs_readonly"}


@dataclass(frozen=True)
class CommandPolicyDecision:
    """Policy verdict for one solver command."""

    allowed: bool
    reason: str
    category: str
    risk: str
    command: str
    argv: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)


def _categorize_command(executable: str, argv: List[str]) -> str:
    if executable == "git":
        if len(argv) > 1 and argv[1] in _READ_ONLY_GIT_SUBCOMMANDS:
            return "vcs_readonly"
        return "vcs_mutation"

    if executable in {"pytest", "ruff", "mypy"}:
        return "verification"

    if executable.startswith("python"):
        joined = " ".join(argv[1:]).lower()
        if "-m pytest" in joined or "-m py_compile" in joined:
            return "verification"
        if "-m pip" in joined:
            return "dependency_install"
        if "-m venv" in joined:
            return "environment_setup"
        return "python"

    if executable in {"npm", "pnpm", "yarn"}:
        if len(argv) > 1 and argv[1] in {"test", "run"}:
            if len(argv) > 2 and argv[2] in {"test", "lint", "typecheck", "check", "build", "dev", "start", "preview"}:
                return "verification"
        if len(argv) > 1 and argv[1] in {"install", "ci", "add"}:
            return "dependency_install"
        return "node"

    if executable in {"node", "nodejs"} and len(argv) > 1 and argv[1] in {"--check", "-c"}:
        return "verification"

    if executable in _READ_ONLY_INSPECTION_COMMANDS:
        return "verification"

    if executable == "cargo":
        if len(argv) > 1 and argv[1] == "test":
            return "verification"
        if len(argv) > 1 and argv[1] in {"build", "check", "run"}:
            return "verification"
        return "rust"

    if executable == "go":
        if len(argv) > 1 and argv[1] == "test":
            return "verification"
        return "go"

    if executable in {
        "cc", "gcc", "clang", "c++", "g++", "clang++", "cmake", "make",
        "ninja", "kotlinc", "kotlin", "gradle", "gradlew", "mvn", "mvnw",
    }:
        return "verification"

    if executable in _PACKAGE_MANAGERS:
        return "dependency_install"

    return "general"


def _risk_for_category(category: str) -> str:
    if category in {"verification", "environment_setup", "vcs_readonly"}:
        return "low"
    if category in {"dependency_install", "python", "node", "rust", "go"}:
        return "medium"
    if category == "vcs_mutation":
        return "high"
    return "medium"


def _policy_mode() -> str:
    cleaned = str(os.getenv("REFINER_SOLVER_COMMAND_POLICY_MODE") or "standard").strip().lower()
    if cleaned in {"standard", "strict"}:
        return cleaned
    return "standard"


def evaluate_command_policy(command: Union[str, Sequence[str]]) -> CommandPolicyDecision:
    """Validate a command and prepare a shell-free subprocess payload.

    Structured ``argv`` input bypasses shell parsing entirely. Legacy string
    input remains supported, but is parsed into the same shell-free payload.
    """

    structured = isinstance(command, (list, tuple))
    if structured:
        parts = [str(item) for item in command]
        cleaned = " ".join(shlex.quote(part) for part in parts)
    else:
        cleaned = (command or "").strip()
        parts = []
    if not cleaned:
        return CommandPolicyDecision(
            allowed=False,
            reason="empty command",
            category="invalid",
            risk="blocked",
            command=cleaned,
        )

    if not structured and _SUDO_RE.search(cleaned):
        return CommandPolicyDecision(
            allowed=False,
            reason="privilege-escalation commands are not allowed",
            category="privileged",
            risk="blocked",
            command=cleaned,
        )

    if not structured and _SHELL_BOOTSTRAP_RE.search(cleaned):
        return CommandPolicyDecision(
            allowed=False,
            reason="pipe-to-shell bootstrap commands are blocked",
            category="bootstrap",
            risk="blocked",
            command=cleaned,
        )

    if not structured and _DESTRUCTIVE_RE.search(cleaned):
        return CommandPolicyDecision(
            allowed=False,
            reason="destructive command pattern blocked by solver policy",
            category="destructive",
            risk="blocked",
            command=cleaned,
        )

    if not structured and _SHELL_META_RE.search(cleaned):
        return CommandPolicyDecision(
            allowed=False,
            reason="shell control operators are not allowed in solver commands",
            category="shell_syntax",
            risk="blocked",
            command=cleaned,
        )

    if not structured:
        try:
            parts = shlex.split(cleaned, posix=os.name != "nt")
        except ValueError as exc:
            return CommandPolicyDecision(
                allowed=False,
                reason=f"unable to parse command safely: {exc}",
                category="invalid",
                risk="blocked",
                command=cleaned,
            )

    if not parts:
        return CommandPolicyDecision(
            allowed=False,
            reason="empty command after parsing",
            category="invalid",
            risk="blocked",
            command=cleaned,
        )

    if not parts[0].strip():
        return CommandPolicyDecision(
            allowed=False,
            reason="command has no executable",
            category="invalid",
            risk="blocked",
            command=cleaned,
        )

    env_overrides: Dict[str, str] = {}
    argv = list(parts)
    while argv and _ENV_ASSIGNMENT_RE.match(argv[0]):
        key, value = argv.pop(0).split("=", 1)
        env_overrides[key] = value

    if not argv:
        return CommandPolicyDecision(
            allowed=False,
            reason="command only set environment variables and had no executable",
            category="invalid",
            risk="blocked",
            command=cleaned,
        )

    executable = os.path.basename(argv[0]).lower()
    if executable in _BLOCKED_EXECUTABLES:
        return CommandPolicyDecision(
            allowed=False,
            reason=f"direct shell executable '{executable}' is blocked",
            category="shell_entrypoint",
            risk="blocked",
            command=cleaned,
        )

    # Structured argv is intentionally exempt from shell-metacharacter
    # parsing, but destructive executables must still be blocked. Refiner
    # plans are not allowed to delete, re-partition, or power-cycle hosts.
    if structured and executable in {
        "rm", "mkfs", "fdisk", "parted", "shutdown", "reboot", "halt",
        "poweroff", "mount", "umount", "dd",
    }:
        return CommandPolicyDecision(
            allowed=False,
            reason=f"destructive executable '{executable}' is blocked by solver policy",
            category="destructive",
            risk="blocked",
            command=cleaned,
        )

    category = _categorize_command(executable, argv)
    if category == "vcs_mutation":
        return CommandPolicyDecision(
            allowed=False,
            reason="mutating git commands are blocked in solver execution",
            category=category,
            risk="blocked",
            command=cleaned,
        )

    policy_mode = _policy_mode()
    if policy_mode == "strict" and category not in _STRICT_ALLOWED_CATEGORIES:
        return CommandPolicyDecision(
            allowed=False,
            reason=f"{category} commands are disabled when REFINER_SOLVER_COMMAND_POLICY_MODE=strict",
            category=category,
            risk="blocked",
            command=cleaned,
        )

    risk = _risk_for_category(category)
    if category == "dependency_install":
        reason = "dependency installation allowed with medium risk"
    elif category == "verification":
        reason = "verification command allowed"
    elif category == "environment_setup":
        reason = "environment setup command allowed"
    elif category == "vcs_readonly":
        reason = "read-only git command allowed"
    else:
        reason = "simple command allowed"

    return CommandPolicyDecision(
        allowed=True,
        reason=reason,
        category=category,
        risk=risk,
        command=cleaned,
        argv=argv,
        env=env_overrides,
    )
