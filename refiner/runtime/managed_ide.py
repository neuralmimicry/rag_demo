"""Bounded handoff payload and HTTP adapter for Refiner's managed IDE fallback."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import requests


_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_RE = re.compile(r"^(?:conductor|refiner)/[A-Za-z0-9._/-]{1,180}$")
_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_JOB_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def build_handoff_payload(
    *,
    job_id: str,
    repository: str,
    source_ref: str,
    source_sha: str,
    requirements: str,
    completion_summary: Optional[Dict[str, Any]],
    completion_reason: str,
) -> Dict[str, Any]:
    """Build a deterministic handoff for one exact, pushed Refiner work branch."""
    if not _JOB_RE.fullmatch(str(job_id or "")):
        raise ValueError("Refiner job identifier is invalid.")
    if not _REPOSITORY_RE.fullmatch(str(repository or "")):
        raise ValueError("Refiner job repository is invalid.")
    if not _BRANCH_RE.fullmatch(str(source_ref or "")) or ".." in source_ref or source_ref.endswith("/") or "//" in source_ref:
        raise ValueError("Managed IDE fallback only accepts Refiner or Conductor work branches.")
    if not _SHA_RE.fullmatch(str(source_sha or "")):
        raise ValueError("Refiner handoff requires a full source commit SHA.")

    requirements = str(requirements or "").strip()[:10_000]
    summary_json = json.dumps(completion_summary or {}, sort_keys=True, ensure_ascii=True)[:2_500]
    reason = str(completion_reason or "Refiner could not verify that all acceptance criteria were met.").strip()[:600]
    prompt = (
        "Refiner's project_solver exhausted its configured attempts without a verified complete result. "
        "Continue the same request in this isolated worktree at the exact Refiner snapshot below. "
        "Review the source and the acceptance criteria, make a reviewable repair, and use the configured "
        "remote runner tools for heavyweight checks. Do not push or merge changes to the Refiner branch.\n\n"
        f"Refiner job: {job_id}\nRepository: {repository}\nBranch: {source_ref}\nCommit: {source_sha}\n"
        f"Refiner outcome: {reason}\nCompletion evidence: {summary_json}\n\n"
        f"Original request and acceptance criteria:\n{requirements or '(Refiner did not retain request text in the job record.)'}"
    )[:15_500]
    key_material = f"refiner-managed-ide:v1:{job_id}:{repository}:{source_ref}:{source_sha}"
    return {
        "idempotencyKey": hashlib.sha256(key_material.encode("utf-8")).hexdigest(),
        "promptSha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "jobId": str(job_id),
        "repository": repository,
        "sourceRef": source_ref,
        "sourceSha": source_sha,
        "prompt": prompt,
    }


def submit_handoff(
    payload: Dict[str, Any],
    *,
    enabled: bool,
    endpoint: str,
    service_token: str,
    post: Optional[Callable[..., Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Submit one authenticated handoff; return a bounded, persistable status."""
    if not enabled:
        return None
    parsed = urlparse(str(endpoint or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return {"status": "blocked", "error": "Managed IDE handoff URL is not configured safely."}
    if not service_token or len(service_token) < 32:
        return {"status": "blocked", "error": "Managed IDE service authentication is not configured."}
    request_post = post or requests.post
    try:
        response = request_post(
            endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {service_token}", "Content-Type": "application/json"},
            timeout=(5, 155),
        )
        if not 200 <= int(response.status_code) < 300:
            return {"status": "blocked", "error": f"Managed IDE rejected the handoff (HTTP {int(response.status_code)})."}
        body = response.json()
        if not isinstance(body, dict) or body.get("status") not in {"queued", "preparing"}:
            return {"status": "blocked", "error": "Managed IDE returned an unrecognised handoff state."}
        result = {
            "status": body["status"],
            "conversationId": str(body.get("conversationId") or "")[:80],
            "browserUrl": str(body.get("browserUrl") or "")[:2_000],
            "sourceSha": payload["sourceSha"],
        }
        if body.get("handoffId"):
            result["handoffId"] = str(body["handoffId"])[:80]
        return result
    except requests.Timeout:
        return {"status": "blocked", "error": "Managed IDE did not respond before the handoff timeout; retry uses the same request key."}
    except requests.RequestException:
        return {"status": "blocked", "error": "Managed IDE could not be reached; retry uses the same request key."}
    except (TypeError, ValueError, KeyError):
        return {"status": "blocked", "error": "Managed IDE returned an invalid handoff response."}
