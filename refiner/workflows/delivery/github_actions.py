"""GitHub Actions verification for repository-backed Refiner jobs.

The delivery pipeline gates production stages, but ordinary repository-backed
solver jobs also need to prove that the uploaded tree builds in the target
repository.  This module provides that small, auditable gate.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request


BUILD_WORKFLOW_HINTS = (
    "build", "ci", "check", "compile", "package", "test", "verify",
)


def _workflow_push_applies_to_branch(workflow_path: Path, branch: str) -> bool:
    """Return whether a workflow can run for a push to ``branch``.

    Repository delivery branches commonly contain build workflows that are
    intentionally restricted to ``main`` or release branches.  Waiting for a
    run that GitHub will never create leaves a Refiner job at 100%/running
    until the full Actions timeout expires.  This small, dependency-free
    parser handles the branch filters used by our workflows and deliberately
    returns ``True`` when a workflow cannot be classified safely.
    """

    try:
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return True
    branch = str(branch or "").strip()
    if not branch:
        return True

    on_index = None
    on_indent = 0
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t"):
            continue
        if line.strip().rstrip(":") in {"on", "\"on\"", "'on'"}:
            on_index = index
            on_indent = len(line) - len(line.lstrip())
            break
    if on_index is None:
        return True

    push_index = None
    push_indent = None
    for index in range(on_index + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= on_indent:
            break
        if line.strip().startswith("push:"):
            push_index = index
            push_indent = indent
            break
    # A push-created commit cannot satisfy pull_request or workflow_dispatch
    # on its own, so there is no run to wait for in this case.
    if push_index is None or push_indent is None:
        return False

    block: List[str] = []
    for line in lines[push_index + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            block.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= push_indent:
            break
        block.append(line)
    text = "\n".join(block)
    branches_match = re.search(r"(?m)^\s+branches:\s*\[([^]]*)\]", text)
    if branches_match:
        patterns = [item.strip().strip("'\"") for item in branches_match.group(1).split(",")]
        return any(fnmatch.fnmatchcase(branch, pattern) for pattern in patterns if pattern)
    patterns = [
        value.strip().strip("'\"")
        for value in re.findall(r"(?m)^\s+-\s+([^#\n]+)", text)
    ]
    if re.search(r"(?m)^\s+branches-ignore:\s*\[([^]]*)\]", text):
        ignored = re.search(r"(?m)^\s+branches-ignore:\s*\[([^]]*)\]", text)
        ignored_patterns = [item.strip().strip("'\"") for item in ignored.group(1).split(",")]
        return not any(fnmatch.fnmatchcase(branch, pattern) for pattern in ignored_patterns if pattern)
    if "branches:" in text:
        return any(fnmatch.fnmatchcase(branch, pattern) for pattern in patterns if pattern)
    if "branches-ignore:" in text:
        return not any(fnmatch.fnmatchcase(branch, pattern) for pattern in patterns if pattern)
    return True


def discover_build_workflows(workspace: str) -> List[str]:
    """Find workflows that should validate a requested repository build."""

    root = Path(workspace) / ".github" / "workflows"
    if not root.is_dir():
        return []
    files = sorted(
        path.name
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
    )
    hinted = [
        name for name in files
        if any(hint in Path(name).stem.lower() for hint in BUILD_WORKFLOW_HINTS)
    ]
    # Unconventional names must not silently bypass verification.
    return hinted or files


def _run_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": run.get("id"),
        "name": run.get("name"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "html_url": run.get("html_url"),
        "run_number": run.get("run_number"),
        "head_sha": run.get("head_sha"),
        "event": run.get("event"),
        "updated_at": run.get("updated_at"),
    }


def wait_for_workflow_commit(
    *,
    owner: str,
    repo: str,
    branch: str,
    workflow_file: str,
    commit_sha: str,
    token: Optional[str],
    timeout_sec: float = 900.0,
    poll_interval_sec: float = 10.0,
    api_base_url: str = "https://api.github.com",
) -> Dict[str, Any]:
    """Wait for a completed successful run for exactly ``commit_sha``."""

    owner, repo, branch = str(owner or "").strip(), str(repo or "").strip(), str(branch or "").strip()
    workflow_file, commit_sha = str(workflow_file or "").strip(), str(commit_sha or "").strip()
    report: Dict[str, Any] = {
        "workflow_file": workflow_file,
        "owner": owner or None,
        "repo": repo or None,
        "branch": branch or None,
        "expected_sha": commit_sha or None,
        "checked": False,
        "succeeded": False,
        "run": None,
        "reason": "GitHub Actions verification was not started",
    }
    if not all((owner, repo, branch, workflow_file, commit_sha)):
        report["reason"] = "missing repository, branch, workflow, or commit context"
        return report
    query = urllib.parse.urlencode({"branch": branch, "per_page": 50})
    url = (
        f"{str(api_base_url or 'https://api.github.com').rstrip('/')}/repos/"
        f"{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/actions/workflows/"
        f"{urllib.parse.quote(workflow_file)}/runs?{query}"
    )
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "refiner-repository-build-gate"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    deadline = time.monotonic() + max(0.0, float(timeout_sec))
    while True:
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            report["checked"] = True
            report["reason"] = f"GitHub Actions API returned HTTP {exc.code}"
            return report
        except Exception as exc:  # pragma: no cover - network-specific detail
            report["checked"] = True
            report["reason"] = f"unable to query GitHub Actions: {exc}"
            return report
        report["checked"] = True
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else []
        matching = [
            run for run in (runs if isinstance(runs, list) else [])
            if isinstance(run, dict)
            and str(run.get("head_sha") or "").strip().lower() == commit_sha.lower()
        ]
        if matching:
            run = matching[0]
            report["run"] = _run_summary(run)
            if str(run.get("status") or "").strip().lower() == "completed":
                conclusion = str(run.get("conclusion") or "").strip().lower()
                report["succeeded"] = conclusion == "success"
                report["reason"] = f"workflow {workflow_file} concluded with {conclusion or 'unknown'}"
                return report
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            report["reason"] = f"timed out waiting for workflow {workflow_file} for commit {commit_sha}"
            return report
        time.sleep(min(max(0.1, float(poll_interval_sec)), remaining))


def verify_repository_builds(
    *,
    workspace: str,
    owner: str,
    repo: str,
    branch: str,
    commit_sha: str,
    token: Optional[str],
    timeout_sec: float = 900.0,
    poll_interval_sec: float = 10.0,
) -> Dict[str, Any]:
    """Verify all discovered build/CI workflows and return an audit report."""

    workflows = discover_build_workflows(workspace)
    if not workflows:
        return {
            "enabled": False,
            "checked": False,
            "succeeded": None,
            "workflows": [],
            "reason": "repository has no GitHub Actions workflow files",
        }
    reports = []
    for workflow in workflows:
        workflow_path = Path(workspace) / ".github" / "workflows" / workflow
        if not _workflow_push_applies_to_branch(workflow_path, branch):
            reports.append(
                {
                    "workflow_file": workflow,
                    "owner": owner or None,
                    "repo": repo or None,
                    "branch": branch or None,
                    "expected_sha": commit_sha or None,
                    "checked": False,
                    "succeeded": True,
                    "run": None,
                    "reason": f"workflow does not trigger on push to branch {branch}",
                }
            )
            continue
        reports.append(
            wait_for_workflow_commit(
                owner=owner,
                repo=repo,
                branch=branch,
                workflow_file=workflow,
                commit_sha=commit_sha,
                token=token,
                timeout_sec=timeout_sec,
                poll_interval_sec=poll_interval_sec,
            )
        )
    succeeded = all(report.get("succeeded") is True for report in reports)
    return {
        "enabled": True,
        "checked": True,
        "succeeded": succeeded,
        "workflows": reports,
        "reason": "all discovered build workflows succeeded" if succeeded else "one or more build workflows failed",
    }
