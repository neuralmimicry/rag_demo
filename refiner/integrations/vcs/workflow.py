"""
Git workflow helper for delivery pipeline.

Supports pull, branch, commit, merge, push, tagging, and optional GitHub releases.
Defaults assume github.com/neuralmimicry as the primary owner but can be
configured via delivery_pipeline.json.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import os
import re
import shutil
import subprocess
import time


DEFAULT_PROVIDER = "github"
DEFAULT_BASE_URL = "https://github.com"
DEFAULT_OWNER = "neuralmimicry"
DEFAULT_REMOTE = "origin"
DEFAULT_DEFAULT_BRANCH = "main"


@dataclass
class VcsResult:
    status: str
    details: Dict[str, Any]


def _trim(text: str, limit: int = 4000) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _run(command: List[str], *, cwd: str, timeout_sec: int = 20) -> Dict[str, Any]:
    start = time.time()
    result = {
        "command": " ".join(command),
        "exit_code": None,
        "status": "error",
        "stdout": "",
        "stderr": "",
        "duration_sec": 0.0,
    }
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        result["exit_code"] = completed.returncode
        result["stdout"] = _trim(completed.stdout)
        result["stderr"] = _trim(completed.stderr)
        result["status"] = "ok" if completed.returncode == 0 else "failed"
    except Exception as exc:
        result["stderr"] = _trim(str(exc))
    result["duration_sec"] = round(time.time() - start, 2)
    return result


def _looks_like_git_repo(project_root: str) -> bool:
    dot_git = os.path.join(project_root, ".git")
    if os.path.isdir(dot_git) or os.path.isfile(dot_git):
        return True
    return False


def _is_git_repo(project_root: str) -> bool:
    if _looks_like_git_repo(project_root):
        return True
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.returncode == 0 and "true" in (result.stdout or "").lower()
    except Exception:
        return False


def _current_branch(project_root: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    branch = (result.stdout or "").strip()
    return branch or None


def _status_porcelain(project_root: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout or ""


def _parse_git_remote(remote_url: str) -> Optional[Dict[str, str]]:
    if not remote_url:
        return None
    patterns = [
        r"^https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^git@[^:]+:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    ]
    for pat in patterns:
        match = re.match(pat, remote_url.strip())
        if match:
            owner = match.group("owner")
            repo = match.group("repo")
            return {"owner": owner, "repo": repo}
    return None


def _get_remote_url(project_root: str, remote: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "remote", "get-url", remote],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _build_remote_url(base_url: str, owner: str, repo: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/{owner}/{repo}.git"


def _template(value: str, variables: Dict[str, str]) -> str:
    if not value:
        return value
    for key, val in variables.items():
        value = value.replace("{" + key + "}", val)
    return value


def _ensure_remote(
    project_root: str,
    *,
    remote: str,
    desired_url: str,
    allow_run: bool,
) -> Dict[str, Any]:
    existing = _get_remote_url(project_root, remote)
    if existing and existing == desired_url:
        return {"status": "ok", "action": "remote", "remote": remote, "url": existing, "changed": False}

    if not allow_run:
        return {
            "status": "planned",
            "action": "remote",
            "remote": remote,
            "url": desired_url,
            "changed": existing != desired_url,
            "existing": existing,
        }

    if existing:
        cmd = ["git", "-C", project_root, "remote", "set-url", remote, desired_url]
    else:
        cmd = ["git", "-C", project_root, "remote", "add", remote, desired_url]
    result = _run(cmd, cwd=project_root)
    result.update({"action": "remote", "remote": remote, "url": desired_url, "existing": existing})
    return result


def _action_commands(action: Dict[str, Any], variables: Dict[str, str], defaults: Dict[str, str]) -> List[List[str]]:
    action_type = str(action.get("type") or "").strip().lower()
    commands: List[List[str]] = []

    if action_type == "pull":
        remote = action.get("remote") or defaults["remote"]
        branch = action.get("branch") or variables.get("branch") or defaults["default_branch"]
        rebase = bool(action.get("rebase", False))
        cmd = ["git", "pull", str(remote), str(branch)]
        if rebase:
            cmd.append("--rebase")
        commands.append(cmd)
    elif action_type == "branch":
        name = _template(str(action.get("name") or ""), variables)
        if not name:
            return []
        checkout = action.get("checkout", True)
        if checkout:
            commands.append(["git", "checkout", "-B", name])
        else:
            commands.append(["git", "branch", name])
    elif action_type == "commit":
        message = _template(str(action.get("message") or ""), variables)
        paths = action.get("paths") or ["."]
        allow_empty = bool(action.get("allow_empty", False))
        add_cmd = ["git", "add"] + [str(p) for p in paths]
        commands.append(add_cmd)
        commit_cmd = ["git", "commit", "-m", message]
        if allow_empty:
            commit_cmd.append("--allow-empty")
        commands.append(commit_cmd)
    elif action_type == "merge":
        source = _template(str(action.get("source") or variables.get("branch") or ""), variables)
        target = _template(str(action.get("target") or defaults["default_branch"]), variables)
        if action.get("checkout_target", True):
            commands.append(["git", "checkout", target])
        merge_cmd = ["git", "merge", source]
        if action.get("no_ff", False):
            merge_cmd.append("--no-ff")
        if action.get("ff_only", False):
            merge_cmd.append("--ff-only")
        commands.append(merge_cmd)
    elif action_type == "push":
        remote = action.get("remote") or defaults["remote"]
        branch = action.get("branch") or variables.get("branch") or defaults["default_branch"]
        cmd = ["git", "push", str(remote), str(branch)]
        if action.get("set_upstream", False):
            cmd.insert(2, "-u")
        commands.append(cmd)
    elif action_type == "tag":
        tag = _template(str(action.get("name") or ""), variables)
        if not tag:
            return []
        message = _template(str(action.get("message") or ""), variables)
        annotated = action.get("annotated", True)
        if annotated:
            tag_cmd = ["git", "tag", "-a", tag, "-m", message or tag]
        else:
            tag_cmd = ["git", "tag", tag]
        commands.append(tag_cmd)
        if action.get("push", True):
            remote = action.get("remote") or defaults["remote"]
            commands.append(["git", "push", str(remote), tag])
    elif action_type == "release":
        tag = _template(str(action.get("tag") or action.get("name") or ""), variables)
        if not tag:
            return []
        use_gh = bool(action.get("use_gh", False))
        if use_gh:
            title = _template(str(action.get("title") or tag), variables)
            notes_file = action.get("notes_file")
            if notes_file:
                commands.append(["gh", "release", "create", tag, "-t", title, "-F", str(notes_file)])
            else:
                body = _template(str(action.get("notes") or ""), variables)
                commands.append(["gh", "release", "create", tag, "-t", title, "-n", body])
    return commands


def run_vcs_workflow(
    project_root: str,
    *,
    config: Optional[Dict[str, Any]],
    version: str,
    allow_run: bool,
    approvals_dir: Optional[str] = None,
) -> VcsResult:
    cfg = config or {}
    enabled = bool(cfg.get("enabled", False))
    if not enabled:
        return VcsResult(status="skipped", details={"enabled": False})

    if not _is_git_repo(project_root):
        return VcsResult(
            status="skipped",
            details={"enabled": True, "status": "skipped", "reason": "not a git repo"},
        )

    provider = str(cfg.get("provider") or DEFAULT_PROVIDER)
    base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL)
    owner = str(cfg.get("owner") or DEFAULT_OWNER)
    remote = str(cfg.get("remote") or DEFAULT_REMOTE)
    default_branch = str(cfg.get("default_branch") or DEFAULT_DEFAULT_BRANCH)
    ensure_remote = bool(cfg.get("ensure_remote", True))
    require_clean = bool(cfg.get("require_clean", True))
    block_on_failure = bool(cfg.get("block_on_failure", True))
    requires_approval = bool(cfg.get("requires_approval", False))

    approval_file = cfg.get("approval_file")
    approval_path = None
    if requires_approval:
        if approval_file:
            approval_path = approval_file
            if not os.path.isabs(approval_path):
                approval_path = os.path.join(project_root, approval_path)
        elif approvals_dir:
            approval_path = os.path.join(approvals_dir, "vcs.ok")

    approval_present = True
    if requires_approval and approval_path:
        approval_present = os.path.exists(approval_path)

    current_branch = _current_branch(project_root)
    status_porcelain = _status_porcelain(project_root)
    clean = bool(status_porcelain == "") if status_porcelain is not None else None

    repo = cfg.get("repo")
    if not repo:
        remote_url = _get_remote_url(project_root, remote)
        parsed = _parse_git_remote(remote_url or "") if remote_url else None
        if parsed:
            repo = parsed.get("repo")
            owner = parsed.get("owner") or owner
        if not repo:
            repo = os.path.basename(os.path.abspath(project_root))
    repo = str(repo)

    remote_url = _build_remote_url(base_url, owner, repo)

    details: Dict[str, Any] = {
        "enabled": True,
        "provider": provider,
        "base_url": base_url,
        "owner": owner,
        "repo": repo,
        "remote": remote,
        "remote_url": remote_url,
        "current_branch": current_branch,
        "clean": clean,
        "approval_required": requires_approval,
        "approval_present": approval_present,
        "approval_file": approval_path,
        "actions": [],
    }

    if requires_approval and not approval_present and allow_run:
        details["status"] = "blocked"
        details["blocked_reason"] = "approval missing"
        return VcsResult(status="blocked" if block_on_failure else "skipped", details=details)

    if require_clean and clean is False and allow_run:
        details["status"] = "blocked"
        details["blocked_reason"] = "working tree not clean"
        return VcsResult(status="blocked" if block_on_failure else "skipped", details=details)

    if ensure_remote:
        remote_result = _ensure_remote(
            project_root,
            remote=remote,
            desired_url=remote_url,
            allow_run=allow_run,
        )
        details["actions"].append(remote_result)
        if allow_run and remote_result.get("status") not in {"ok", "planned"}:
            details["status"] = "failed"
            return VcsResult(status="failed" if block_on_failure else "skipped", details=details)

    variables = {
        "version": version,
        "owner": owner,
        "repo": repo,
        "branch": current_branch or default_branch,
    }
    defaults = {"remote": remote, "default_branch": default_branch}

    actions = cfg.get("actions") or []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("enabled") is False:
            continue
        action_type = str(action.get("type") or "").strip().lower()
        if not action_type:
            continue
        commands = _action_commands(action, variables, defaults)
        action_entry = {"type": action_type, "commands": [], "status": "planned"}
        allow_failure = bool(action.get("allow_failure", False))
        if allow_run and commands:
            action_entry["status"] = "ok"
            for cmd in commands:
                if not cmd:
                    continue
                if cmd[0] == "gh" and shutil.which("gh") is None:
                    action_entry["commands"].append(
                        {
                            "command": " ".join(cmd),
                            "status": "skipped",
                            "reason": "gh not installed",
                        }
                    )
                    continue
                result = _run(cmd, cwd=project_root)
                if action_type == "commit" and result["status"] != "ok":
                    combined = (result.get("stdout", "") + result.get("stderr", "")).lower()
                    if "nothing to commit" in combined and not action.get("fail_on_no_changes", False):
                        result["status"] = "ok"
                        result["note"] = "nothing to commit"
                action_entry["commands"].append(result)
                if result["status"] != "ok":
                    action_entry["status"] = "failed"
                    if not allow_failure:
                        details["actions"].append(action_entry)
                        details["status"] = "failed"
                        return VcsResult(status="failed" if block_on_failure else "skipped", details=details)
        else:
            for cmd in commands:
                action_entry["commands"].append({"command": " ".join(cmd), "status": "planned"})
        details["actions"].append(action_entry)

    details["status"] = details.get("status") or ("planned" if not allow_run else "ok")
    return VcsResult(status=details["status"], details=details)
