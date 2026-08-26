"""GitHub Actions verification for repository-backed Refiner jobs.

The delivery pipeline gates production stages, but ordinary repository-backed
solver jobs also need to prove that the uploaded tree builds in the target
repository.  This module provides that small, auditable gate.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request


BUILD_WORKFLOW_HINTS = (
    "build", "ci", "check", "compile", "package", "test", "verify",
)


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
    reports = [
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
        for workflow in workflows
    ]
    succeeded = all(report.get("succeeded") is True for report in reports)
    return {
        "enabled": True,
        "checked": True,
        "succeeded": succeeded,
        "workflows": reports,
        "reason": "all discovered build workflows succeeded" if succeeded else "one or more build workflows failed",
    }
