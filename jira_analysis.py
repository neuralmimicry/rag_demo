"""
Jira project/issue quality analysis utilities.

This mirrors the Confluence analysis flow by producing an interactive HTML report
with baseline metrics, findings, an optional LLM-backed executive summary, and
optional per-issue LLM insights. It also supports idempotent posting of
AI-generated comments back to Jira issues when enabled.

Public entrypoint:
- analyze_jira_and_write_report(base_url, auth, projects|jql, output_html, ...)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import datetime as dt
import json
import os
import re
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import hashlib
import logging

logger = logging.getLogger(__name__)


class _FallbackProvider:
    def __init__(self, primary: LLMProvider, fallback: LLMProvider):
        self._primary = primary
        self._fallback = fallback
        self._quota_exhausted = False

    def __getattr__(self, name: str):
        return getattr(self._primary, name)

    def predict(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        system: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ):
        if self._quota_exhausted and self._fallback:
            return self._fallback.predict(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )
        try:
            return self._primary.predict(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )
        except LLMQuotaError:
            retries = int(os.getenv("LLM_RATE_LIMIT_RETRIES", "2"))
            base = float(os.getenv("LLM_RATE_LIMIT_BACKOFF_BASE", "1.0"))
            for attempt in range(retries):
                delay = max(0.1, base * (2 ** attempt))
                logger.info(f"Rate limit hit; backing off {delay:.2f}s before retry {attempt + 1}/{retries}.")
                time.sleep(delay)
                try:
                    return self._primary.predict(
                        messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        timeout=timeout,
                        reasoning_effort=reasoning_effort,
                    )
                except LLMQuotaError:
                    continue
            if not self._fallback:
                raise
            self._quota_exhausted = True
            logger.warning(
                "LLM quota exceeded for primary provider; switching to fallback provider."
            )
            return self._fallback.predict(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )
        except LLMError as exc:
            msg = str(exc).lower()
            if "timed out" in msg or "timeout" in msg:
                retries = int(os.getenv("LLM_TIMEOUT_RETRIES", "1"))
                base = float(os.getenv("LLM_TIMEOUT_BACKOFF_BASE", "1.0"))
                for attempt in range(retries):
                    delay = max(0.1, base * (2 ** attempt))
                    logger.info(f"Timeout hit; backing off {delay:.2f}s before retry {attempt + 1}/{retries}.")
                    time.sleep(delay)
                    try:
                        return self._primary.predict(
                            messages,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            system=system,
                            timeout=timeout,
                            reasoning_effort=reasoning_effort,
                        )
                    except LLMError as retry_exc:
                        retry_msg = str(retry_exc).lower()
                        if "timed out" in retry_msg or "timeout" in retry_msg:
                            continue
                        raise
                if self._fallback:
                    logger.warning(
                        "LLM timeouts from primary provider; using fallback for this call."
                    )
                    return self._fallback.predict(
                        messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        timeout=timeout,
                        reasoning_effort=reasoning_effort,
                    )
            raise

# Prefer using existing project facilities/config
from main import load_config
from atlassian_utils import IssueInfo, JiraClient, parse_atlassian_datetime as _parse_dt

try:
    # Optional: python-jira client (already a dependency in this repo)
    from jira import JIRA as jira_api  # type: ignore
except Exception:  # pragma: no cover - fallback at runtime if not installed
    jira_api = None  # type: ignore

from llm_providers import get_provider, LLMProvider, LLMError, LLMQuotaError


AI_MARKER_PREFIX = "JIRASTATS_AI:id="


def _jira_get(base_url: str, auth: Tuple[str, str], path: str, params: Optional[dict] = None) -> Dict[str, Any]:
    """Basic GET wrapper kept for non-search endpoints."""
    url = base_url.rstrip("/") + path
    logger.debug(f"Jira GET {url}")
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, params=params or {}, headers=headers, auth=auth, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _jira_post(base_url: str, auth: Tuple[str, str], path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    logger.debug(f"Jira POST {url}")
    headers = {
        "Accept": "application/json", 
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }
    resp = requests.post(url, auth=auth, data=json.dumps(payload), headers=headers, timeout=30)
    if resp.status_code >= 400:
        logger.debug(f"Jira POST failed ({resp.status_code}): {resp.text[:500]}")
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def _jira_put(base_url: str, auth: Tuple[str, str], path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    logger.debug(f"Jira PUT {url}")
    resp = requests.put(url, auth=auth, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def _default_jql_for_projects(projects_csv: Optional[str]) -> str:
    if not projects_csv:
        # Safe default limiting scope
        return "order by updated desc"
    keys = [k.strip() for k in projects_csv.split(",") if k.strip()]
    if not keys:
        return "order by updated desc"
    in_list = ",".join(keys)
    return f"project in ({in_list}) order by updated desc"


def _map_issue(base_url: str, raw_issue: Dict[str, Any]) -> IssueInfo:
    key = raw_issue.get("key")
    fields = raw_issue.get("fields", {})
    summary = fields.get("summary") or ""
    issuetype = (fields.get("issuetype") or {}).get("name") or ""
    status = (fields.get("status") or {}).get("name") or ""
    priority = (fields.get("priority") or {}).get("name")
    labels = fields.get("labels") or []
    assignee = ((fields.get("assignee") or {}).get("displayName") if fields.get("assignee") else None)
    updated = _parse_dt(fields.get("updated"))
    created = _parse_dt(fields.get("created"))
    desc = fields.get("description") or ""
    
    reporter = fields.get("reporter", {})
    reporter_name = reporter.get("displayName") if reporter else None
    
    # Extract parent key if present
    parent_obj = fields.get("parent")
    parent_key = parent_obj.get("key") if isinstance(parent_obj, dict) else None
    
    comments_data = fields.get("comment", {})
    comment_count = comments_data.get("total", 0) if comments_data else 0
    commenters = []
    comments = []
    if comments_data and "comments" in comments_data:
        for c in comments_data["comments"]:
            author = c.get("author", {})
            author_name = author.get("displayName")
            body = c.get("body", "")
            if author_name:
                commenters.append(author_name)
                # For V3 API, body is ADT (dict), for V2 it is string
                if isinstance(body, dict):
                    try:
                        body_text = json.dumps(body)
                    except Exception:
                        body_text = str(body)
                else:
                    body_text = str(body)
                comments.append({"author": author_name, "body": body_text})

    return IssueInfo(
        key=str(key),
        url=f"{base_url.rstrip('/')}/browse/{key}",
        summary=str(summary),
        issuetype=str(issuetype),
        status=str(status),
        priority=(str(priority) if priority else None),
        labels=list(labels or []),
        assignee=(str(assignee) if assignee else None),
        updated=updated,
        created=created,
        parent_key=parent_key,
        description=str(desc),
        reporter=reporter_name,
        comment_count=comment_count,
        commenters=commenters,
        comments=comments
    )


def _map_issue_from_client(base_url: str, issue_obj: Any) -> IssueInfo:
    """Map an issue returned by python-jira client into IssueInfo."""
    key = getattr(issue_obj, "key", None)
    f = getattr(issue_obj, "fields", None)
    summary = getattr(f, "summary", "") if f else ""
    issuetype = getattr(getattr(f, "issuetype", None), "name", "") if f else ""
    status = getattr(getattr(f, "status", None), "name", "") if f else ""
    priority = getattr(getattr(f, "priority", None), "name", None) if f else None
    labels = list(getattr(f, "labels", []) or []) if f else []
    assignee_obj = getattr(f, "assignee", None) if f else None
    assignee = getattr(assignee_obj, "displayName", None) if assignee_obj else None
    updated = _parse_dt(getattr(f, "updated", None) if f else None)
    created = _parse_dt(getattr(f, "created", None) if f else None)
    desc = getattr(f, "description", "") if f else ""
    
    reporter_obj = getattr(f, "reporter", None) if f else None
    reporter = getattr(reporter_obj, "displayName", None) if reporter_obj else None
    
    # Extract parent key from client object
    parent_obj = getattr(f, "parent", None)
    parent_key = getattr(parent_obj, "key", None) if parent_obj else None
    
    comment_obj = getattr(f, "comment", None) if f else None
    comment_count = getattr(comment_obj, "total", 0) if comment_obj else 0
    commenters = []
    comments = []
    if comment_obj and hasattr(comment_obj, "comments"):
        for c in comment_obj.comments:
            author = getattr(c, "author", None)
            author_name = getattr(author, "displayName", None) if author else None
            body = getattr(c, "body", "")
            if author_name:
                commenters.append(author_name)
                # python-jira handles ADT to string conversion often, but check
                if not isinstance(body, str):
                    try:
                        body_text = json.dumps(body)
                    except Exception:
                        body_text = str(body)
                else:
                    body_text = str(body)
                comments.append({"author": author_name, "body": body_text})

    # python-jira may return ADT objects for description; coerce to string if needed
    if not isinstance(desc, str):
        try:
            desc = json.dumps(desc)
        except Exception:
            desc = str(desc)
    return IssueInfo(
        key=str(key),
        url=f"{base_url.rstrip('/')}/browse/{key}",
        summary=str(summary),
        issuetype=str(issuetype),
        status=str(status),
        priority=(str(priority) if priority else None),
        labels=labels,
        assignee=(str(assignee) if assignee else None),
        updated=updated,
        created=created,
        parent_key=parent_key,
        description=str(desc or ""),
        reporter=reporter,
        comment_count=comment_count,
        commenters=commenters,
        comments=comments
    )


def _search_via_rest(base_url: str, auth: Tuple[str, str], q: str, start_at: int, max_results: int, fields: List[str]) -> Dict[str, Any]:
    """Try various Jira search endpoints and methods to find one that works."""
    payload = {
        "jql": q,
        "startAt": start_at,
        "maxResults": max_results,
        "fields": fields,
    }
    
    # Define a sequence of (path, method) to try
    search_strategies = [
        ("/rest/api/3/search", "POST"),
        ("/rest/api/2/search", "POST"),
        ("/rest/api/latest/search", "POST"),
        ("/rest/api/3/search", "GET"),
        ("/rest/api/2/search", "GET"),
        ("/rest/api/latest/search", "GET"),
    ]
    
    # If the standard ones fail, we might try with /jira prefix
    extended_strategies = []
    for p, m in search_strategies:
        extended_strategies.append((p, m))
    
    # Try /jira prefix as well, even on cloud (some migrated instances use it)
    for p, m in search_strategies:
        extended_strategies.append(("/jira" + p, m))
        
    best_exception = None
    strategy_failures = []
    for path, method in extended_strategies:
        # Skip GET if JQL is too long to avoid 414/431/410 issues on some proxies
        if method == "GET" and len(q) > 1500:
            continue
            
        try:
            logger.debug(f"Attempting Jira search: {method} {path}")
            if method == "POST":
                return _jira_post(base_url, auth, path, payload)
            else:
                return _jira_get(base_url, auth, path, params={
                    "jql": q,
                    "startAt": start_at,
                    "maxResults": max_results,
                    "fields": ",".join(fields),
                })
        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            strategy_failures.append(f"{method} {path} -> {status}")
            
            # If it's a 400, it's likely a JQL error. Don't fall back to other endpoints with same JQL
            if status == 400:
                logger.error(f"Jira search failed with 400 Bad Request. Likely invalid JQL: {q}")
                raise
            
            # If it's 401, it's definitely an auth issue, don't bother retrying
            if status == 401:
                raise
                
            # Keep track of the "best" exception to raise if all fail.
            # We prefer 403 over 404/410 as it's more informative for Cloud instances.
            if not best_exception or status in (403, 400):
                best_exception = e
            
            # 403/410/404 might be temporary or instance-specific, try next strategy
            continue
        except Exception as e:
            strategy_failures.append(f"{method} {path} -> error: {str(e)}")
            if not best_exception:
                best_exception = e
            continue
            
    if best_exception:
        logger.error(f"All Jira search strategies failed for JQL: {q}. Failures: {', '.join(strategy_failures)}")
        raise best_exception
    raise RuntimeError(f"All Jira search strategies failed for JQL: {q}")


def _get_search_config() -> Tuple[bool, int]:
    """Read prefer_client and page_size from config with safe defaults."""
    try:
        cfg = load_config()
        search_cfg = (cfg or {}).get("search", {})
        prefer_client = bool(search_cfg.get("prefer_client", True))
        page_size = int(search_cfg.get("page_size", 100))
        return prefer_client, page_size
    except Exception:
        return True, 100


def _search_via_client(base_url: str, auth: Tuple[str, str], q: str, start_at: int, max_results: int, fields: List[str]):
    """Search using python-jira client if available. May raise exceptions to trigger REST fallback."""
    if jira_api is None:
        raise RuntimeError("python-jira client not available")
    options = {"headers": {"Accept": "application/json"}}
    jira = jira_api(server=base_url.rstrip("/"), basic_auth=auth, options=options)
    # python-jira expects comma-separated field names
    fields_csv = ",".join(fields)
    # Returns a ResultList (iterable) with .total when jql is valid
    results = jira.search_issues(jql_str=q, startAt=start_at, maxResults=max_results, fields=fields_csv)
    return results


def _get_max_items() -> int:
    try:
        cfg = load_config() or {}
        search_cfg = (cfg or {}).get("search", {})
        return int(search_cfg.get("max_items", 10000))
    except Exception:
        return 10000


def fetch_issues(base_url: str, auth: Tuple[str, str], *, projects: Optional[str], jql: Optional[str], limit: Optional[int] = None) -> List[IssueInfo]:
    q = jql or _default_jql_for_projects(projects)
    logger.info(f"Fetching Jira issues with JQL: {q}")
    prefer_client, page_size = _get_search_config()
    # Resolve max items cap
    cap = int(limit) if isinstance(limit, int) and limit > 0 else _get_max_items()
    start_at = 0
    max_results = max(1, min(page_size, 100))  # keep page size reasonable
    issues: List[IssueInfo] = []
    fields_req = [
        "summary", "issuetype", "status", "priority", "labels", "assignee", "updated", "created", "description", "reporter", "comment", "parent"
    ]

    used_client = False
    client_failed = False

    if prefer_client:
        try:
            used_client = True
            while True:
                res = _search_via_client(base_url, auth, q, start_at, max_results, fields_req)
                # res is iterable; may have .total and __len__ for page
                page_items = list(res)
                for it in page_items:
                    try:
                        issues.append(_map_issue_from_client(base_url, it))
                    except Exception:
                        continue
                total = int(getattr(res, "total", start_at + len(page_items)))
                start_at += len(page_items)
                if start_at >= total or start_at >= cap or len(page_items) == 0:
                    break
        except Exception:
            # Client path failed; fall back to REST
            client_failed = True

    if (not prefer_client) or client_failed:
        start_at = 0
        while True:
            data = _search_via_rest(base_url, auth, q, start_at, max_results, fields_req)
            arr = data.get("issues", [])
            for it in arr:
                try:
                    issues.append(_map_issue(base_url, it))
                except Exception:
                    # Skip malformed records
                    continue
            total = int(data.get("total", 0))
            start_at += len(arr)
            if start_at >= total or start_at >= cap or len(arr) == 0:
                break
    # De-duplicate by key
    uniq: Dict[str, IssueInfo] = {}
    for ii in issues:
        if ii.key not in uniq:
            uniq[ii.key] = ii
    out = list(uniq.values())
    # Enforce cap strictly (may truncate the last page)
    return out[:cap]


def _extract_confluence_ids(text: str, jira_base_url: str) -> List[str]:
    """Extract Confluence content IDs from URLs in the given text.

    Matches typical Atlassian Cloud URL forms like:
    - https://<host>/wiki/spaces/<SPACE>/pages/<ID>/...
    - https://<host>/wiki/pages/<ID>/...
    - https://<host>/wiki/content/<ID>
    Returns unique IDs as strings in first-seen order.
    """
    ids: List[str] = []
    seen: set = set()
    try:
        host = urlparse(jira_base_url).netloc
    except Exception:
        host = ""
    # Host-specific pattern (most accurate)
    if host:
        pat1 = re.compile(rf"https?://{re.escape(host)}/wiki/(?:spaces/[^/]+/pages/|pages/|content/)(\d+)(?:\b|/)")
        for m in pat1.findall(text or ""):
            if m not in seen:
                seen.add(m)
                ids.append(str(m))
    # Fallback host-agnostic pattern (path-style links only, not fully-qualified URLs)
    pat2 = re.compile(r"/wiki/(?:spaces/[^/]+/pages/|pages/|content/)(\d+)(?:\b|/)")
    for m in pat2.finditer(text or ""):
        # Only accept path-style links when '/wiki' starts at BOS or is preceded by whitespace or opening bracket
        start = m.start()
        if start > 0:
            prev = (text or "")[start-1]
            if not (prev.isspace() or prev in "([{"):
                continue
        gid = m.group(1)
        if gid not in seen:
            seen.add(gid)
            ids.append(str(gid))
    return ids


def _fetch_confluence_context(
    base_url: str,
    auth: Tuple[str, str],
    issue_text: str,
    *,
    include_conf: bool = True,
    max_conf_pages: int = 3,
    max_conf_chars: int = 5000,
    max_workers: int = 4,
) -> str:
    """Fetch and return appended Confluence context text for an issue, preserving order.

    This function detects Confluence content IDs from the issue text, fetches up to
    `max_conf_pages` pages concurrently with a small thread pool, truncates each to
    `max_conf_chars`, and returns a string that begins with the delimiter
    "--- Linked Confluence context ---" followed by each page blob. If nothing is
    fetched or if inclusion is disabled, returns an empty string.
    """
    if not include_conf or not issue_text:
        return ""
    try:
        from confluence_analysis import get_page_text_by_id as _conf_get_by_id  # lazy import
    except Exception:
        _conf_get_by_id = None  # type: ignore
    if not _conf_get_by_id:
        return ""
    conf_ids = _extract_confluence_ids(issue_text, base_url)
    if not conf_ids:
        return ""
    sel_ids = conf_ids[: max(0, int(max_conf_pages or 0))]
    if not sel_ids:
        return ""
    # Fetch in parallel but preserve first-seen order
    results: List[Optional[str]] = [None] * len(sel_ids)
    # Bound workers to number of tasks
    workers = max(1, min(int(max_workers or 1), len(sel_ids)))
    def _task(idx: int, cid: str) -> Tuple[int, str]:
        try:
            blob = _conf_get_by_id(base_url, auth, cid) or ""
            if blob and len(blob) > max_conf_chars:
                blob = blob[: max_conf_chars]
            return idx, (f"[Confluence page {cid}]\n{blob}" if blob else "")
        except Exception:
            return idx, ""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_task, i, cid): i for i, cid in enumerate(sel_ids)}
        for fut in as_completed(futs):
            idx, out = fut.result()
            results[idx] = out
    conf_blobs = [r for r in results if r]
    if not conf_blobs:
        return ""
    return "\n\n--- Linked Confluence context ---\n" + "\n\n".join(conf_blobs)


def _baseline_metrics(issues: List[IssueInfo]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    by_priority: Dict[str, int] = {}
    unlabeled = 0
    missing_assignee = 0
    stale = 0
    long_open = 0
    now = dt.datetime.now(dt.timezone.utc)
    for it in issues:
        by_type[it.issuetype] = by_type.get(it.issuetype, 0) + 1
        by_status[it.status] = by_status.get(it.status, 0) + 1
        if it.priority:
            by_priority[it.priority] = by_priority.get(it.priority, 0) + 1
        if not it.labels:
            unlabeled += 1
        if not it.assignee:
            missing_assignee += 1
        if it.updated and (now - it.updated).days > 90:
            stale += 1
        # Heuristic: consider long open if Not in Done and last update > 180d
        if (it.status or '').lower() not in ("done", "resolved", "closed"):
            if it.updated and (now - it.updated).days > 180:
                long_open += 1
    return {
        "total_issues": len(issues),
        "by_type": by_type,
        "by_status": by_status,
        "by_priority": by_priority,
        "unlabeled": unlabeled,
        "missing_assignee": missing_assignee,
        "stale_90d": stale,
        "long_open_180d": long_open,
    }


def _findings_from_metrics(metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    f: List[Dict[str, Any]] = []
    total = metrics.get("total_issues", 0)
    if total:
        unlabeled = metrics.get("unlabeled", 0)
        if unlabeled / max(1, total) > 0.3:
            f.append({"category": "Metadata Quality", "severity": "medium", "message": f"{unlabeled} issues without labels. Adopt standard taxonomy and apply labels."})
        missing_assignee = metrics.get("missing_assignee", 0)
        if missing_assignee:
            f.append({"category": "Ownership", "severity": "high", "message": f"{missing_assignee} issues lack an assignee. Ensure ownership for flow efficiency."})
        stale = metrics.get("stale_90d", 0)
        if stale:
            f.append({"category": "Flow Freshness", "severity": "medium", "message": f"{stale} issues not updated in >90 days. Review and close or progress."})
        long_open = metrics.get("long_open_180d", 0)
        if long_open:
            f.append({"category": "Throughput", "severity": "medium", "message": f"{long_open} long-open issues (>180d). Consider splitting or re-prioritizing."})
    # Security/telco reminders
    f.append({"category": "Security Hygiene", "severity": "info", "message": "Avoid secrets in issues; link to secure repos; consider data classification labels where applicable."})
    # Agile/SDLC reminders
    f.append({"category": "Agile/SDLC", "severity": "info", "message": "Ensure Definition of Done is met; Stories have acceptance criteria; Epics link to Confluence design docs."})
    # De-duplicate and sort
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in f:
        key = (str(it.get('category','')).strip().casefold(), str(it.get('message','')).strip().casefold(), str(it.get('severity','')).strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda x: (str(x.get('category','')).casefold(), str(x.get('message','')).casefold()))
    return out


def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 4))


def _render_html(
    title: str,
    metrics: Dict[str, Any],
    findings: List[Dict[str, Any]],
    issue_rows_html: str,
    executive_summary: Optional[str],
    action_plan: Optional[str],
    llm_insights_html: str,
) -> str:
    # Simple HTML similar to Confluence reporter, with selection UI
    metrics_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items() if k not in ("by_type", "by_status", "by_priority"))
    # distributions
    def dict_table(d: Dict[str, Any]) -> str:
        rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted((d or {}).items(), key=lambda kv: str(kv[0]).casefold()))
        return f"<table><thead><tr><th>Name</th><th>Count</th></tr></thead><tbody>{rows}</tbody></table>"
    findings_html = "".join(f"<li><strong>{it.get('category')}:</strong> {it.get('message')} <em>({it.get('severity','info')})</em></li>" for it in findings)
    exec_html = f"<section><h2>Executive Summary</h2><pre>{executive_summary}</pre></section>" if executive_summary else ""
    action_html = f"<section><h2>Action Plan</h2><pre>{action_plan}</pre></section>" if action_plan else ""
    return f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem; }}
    table {{ border-collapse: collapse; }}
    td, th {{ border: 1px solid #ddd; padding: 8px; }}
    th {{ background: #f3f3f3; }}
    .small {{ font-size: 0.9em; color: #444; }}
    .muted {{ color: #666; }}
    details {{ margin-bottom: 1rem; }}
  </style>
  <script>
    function toggleAll(cls, checked) {{
      document.querySelectorAll('.' + cls).forEach(cb => cb.checked = checked);
      updateEstimates();
    }}
    function updateEstimates() {{
      let totalTokens = 0;
      document.querySelectorAll('.issue-row').forEach(row => {{
        const cb = row.querySelector('input[type=checkbox]');
        const tokens = parseInt(row.getAttribute('data-tokens') || '0');
        if (cb && cb.checked) totalTokens += tokens;
      }});
      const perReq = parseInt((document.getElementById('chunkSize') || {{}}).value || '2000');
      document.getElementById('selTokens').innerText = totalTokens.toString();
      document.getElementById('selReqs').innerText = perReq > 0 ? Math.ceil(totalTokens / perReq) : '-';
    }}
    function downloadSelection() {{
      const items = [];
      document.querySelectorAll('.issue-row').forEach(row => {{
        const cb = row.querySelector('input[type=checkbox]');
        if (cb && cb.checked) {{
          items.push({{ key: row.getAttribute('data-key'), summary: row.getAttribute('data-summary') }});
        }}
      }});
      const manifest = {{ version: 1, selectedIssues: items, chunkSize: parseInt((document.getElementById('chunkSize')||{{}}).value||'2000') }};
      const blob = new Blob([JSON.stringify(manifest, null, 2)], {{type : 'application/json'}});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'selection.json'; a.click();
      URL.revokeObjectURL(url);
    }}
  </script>
  </head>
  <body>
    <h1>{title}</h1>
    {exec_html}
    {action_html}
    <section>
      <h2>Baseline Metrics</h2>
      <table>
        <thead><tr><th>Metric</th><th>Value</th></tr></thead>
        <tbody>{metrics_rows}</tbody>
      </table>
      <h3>By Type</h3>
      {dict_table(metrics.get('by_type', {}))}
      <h3>By Status</h3>
      {dict_table(metrics.get('by_status', {}))}
      <h3>By Priority</h3>
      {dict_table(metrics.get('by_priority', {}))}
    </section>
    <section>
      <h2>Findings & Recommendations</h2>
      <ul>{findings_html}</ul>
    </section>
    <section>
      <h2>Interactive Analysis (LLM scope selection)</h2>
      <div class=\"small\">Token estimates are heuristic (≈1 token ≈ 4 chars).</div>
      <div style=\"margin: 0.5rem 0;\">
        <button onclick=\"toggleAll('issue-checkbox', true)\">Select all</button>
        <button onclick=\"toggleAll('issue-checkbox', false)\">Deselect all</button>
        &nbsp; | Chunk size (tokens): <input id=\"chunkSize\" type=\"number\" value=\"2000\" min=\"200\" max=\"8000\" step=\"100\" oninput=\"updateEstimates()\" />
        &nbsp; | Estimated selected tokens: <strong id=\"selTokens\">0</strong>
        &nbsp; (~requests: <strong id=\"selReqs\">0</strong>)
        &nbsp; <button onclick=\"downloadSelection()\">Download Selection JSON</button>
      </div>
      <table>
        <thead><tr><th>Include</th><th>Summary</th><th>Key</th><th>Type</th><th>Status</th><th>Assignee</th><th>Labels</th><th>Est. tokens</th></tr></thead>
        <tbody id=\"issueList\">{issue_rows_html}</tbody>
      </table>
    </section>
    {llm_insights_html}
  </body>
</html>
"""


def analyze_jira_and_write_report(
    *,
    base_url: str,
    auth: Tuple[str, str],
    projects: Optional[str],
    jql: Optional[str],
    output_html: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    selection_path: Optional[str] = None,
    llm_max_tokens: Optional[int] = None,
    llm_chunk_size: Optional[int] = None,
    llm_temperature: float = 0.2,
    llm_timeout: Optional[int] = None,
    llm_reasoning_effort: Optional[str] = None,
    action_plan: bool = False,
    dry_run: bool = False,
    # Optional posting of AI-generated comments
    post_comments: bool = False,
    post_target: str = "both",
    dry_run_post: bool = False,
    llm_inter_request_gap: float = 3.0,
    fallback_llm_provider: Optional[str] = None,
    fallback_llm_model: Optional[str] = None,
    fallback_llm_api_key: Optional[str] = None,
) -> None:
    # Fetch issues using configured max_items cap
    try:
        cfg = load_config() or {}
        search_cfg = (cfg or {}).get("search", {})
        cap = int(search_cfg.get("max_items", 10000))
    except Exception:
        cap = 10000
    issues = fetch_issues(base_url, auth, projects=projects, jql=jql, limit=cap)
    # Sort alphabetically (summary, then key)
    issues = sorted(issues, key=lambda x: (str(x.summary).casefold(), str(x.key)))

    metrics = _baseline_metrics(issues)
    findings = _findings_from_metrics(metrics)

    # Build issue rows with token estimates
    issue_rows: List[str] = []
    seen_keys = set()
    for it in issues:
        if it.key in seen_keys:
            continue
        seen_keys.add(it.key)
        text = (it.description or "")
        est_tokens = _estimate_tokens(text)
        labels = ", ".join(it.labels or [])
        row = (
            f"<tr class='issue-row' data-key='{it.key}' data-summary='{it.summary}' data-tokens='{est_tokens}'>"
            f"<td><input type='checkbox' class='issue-checkbox' onclick='updateEstimates()' /></td>"
            f"<td><a href='{it.url}' target='_blank'>{it.summary}</a></td>"
            f"<td>{it.key}</td>"
            f"<td>{it.issuetype}</td>"
            f"<td>{it.status}</td>"
            f"<td>{it.assignee or '-'}</td>"
            f"<td>{labels}</td>"
            f"<td>{est_tokens}</td>"
            f"</tr>"
        )
        issue_rows.append(row)

    # LLM handling (optional)
    llm_sections: List[str] = []
    provider: Optional[LLMProvider] = None
    if llm_provider:
        try:
            provider = get_provider(llm_provider, model=llm_model, base_url=ollama_base_url, inter_request_gap=llm_inter_request_gap)
        except LLMError:
            provider = None
        if provider and fallback_llm_provider:
            f_kwargs: Dict[str, Any] = {}
            if fallback_llm_api_key:
                if fallback_llm_provider in ("gemini", "google") and fallback_llm_api_key.startswith("ya29."):
                    f_kwargs["access_token"] = fallback_llm_api_key
                else:
                    f_kwargs["api_key"] = fallback_llm_api_key
            try:
                fallback_provider = get_provider(
                    fallback_llm_provider,
                    model=fallback_llm_model,
                    base_url=ollama_base_url,
                    inter_request_gap=llm_inter_request_gap,
                    **f_kwargs,
                )
                provider = _FallbackProvider(provider, fallback_provider)
            except LLMError:
                pass

    # Executive summary (LLM-backed if available)
    def _heuristic_exec() -> str:
        lines = [
            f"Issues: {metrics.get('total_issues',0)}; Unlabeled: {metrics.get('unlabeled',0)}; Missing assignee: {metrics.get('missing_assignee',0)}; Stale>90d: {metrics.get('stale_90d',0)}; Long-open>180d: {metrics.get('long_open_180d',0)}.\n",
            "Areas to Focus:\n- Reduce unassigned items; triage long-open issues.\n",
            "Areas to Improve:\n- Increase label usage; refresh stale items.\n",
            "Areas to Praise:\n- Where throughput and freshness are good, keep current cadences.\n",
            "Areas to Review:\n- Status distribution and WIP limits.\n",
        ]
        return "\n".join(lines).strip()

    def _heuristic_action_plan() -> str:
        def _owner_for(category: str) -> str:
            cat = (category or "").lower()
            if "security" in cat:
                return "Security lead"
            if "agile" in cat or "sdlc" in cat:
                return "Delivery lead"
            if "metadata" in cat or "label" in cat:
                return "Project lead"
            if "throughput" in cat or "flow" in cat:
                return "Delivery lead"
            return "Service owner"

        def _action_line(action: str, owner: str, impact: str, risk: str, evidence: str) -> str:
            return f"- Action: {action} | Owner: {owner} | Impact: {impact} | Risk: {risk} | Evidence: {evidence}"

        buckets = {
            "Immediate (0-2 weeks)": [],
            "Near-term (2-6 weeks)": [],
            "Longer-term (6+ weeks)": [],
        }
        for f in findings:
            sev = (f.get("severity") or "info").lower()
            category = f.get("category") or "General"
            msg = f.get("message") or ""
            owner = _owner_for(category)
            impact = "Improves delivery quality and predictability."
            risk = "Issues persist or worsen."
            evidence = f"{category}: {msg}"
            action = f"Address {category.lower()} gap: {msg}"
            if sev in ("high",):
                buckets["Immediate (0-2 weeks)"].append(_action_line(action, owner, impact, risk, evidence))
            elif sev in ("medium",):
                buckets["Near-term (2-6 weeks)"].append(_action_line(action, owner, impact, risk, evidence))
            else:
                buckets["Longer-term (6+ weeks)"].append(_action_line(action, owner, impact, risk, evidence))

        missing_assignee = metrics.get("missing_assignee", 0) or 0
        if missing_assignee:
            buckets["Immediate (0-2 weeks)"].append(
                _action_line(
                    f"Assign owners to {missing_assignee} unassigned issues.",
                    "Project lead",
                    "Clear accountability and faster triage.",
                    "Work stalls due to missing ownership.",
                    f"Missing assignee: {missing_assignee}",
                )
            )
        unlabeled = metrics.get("unlabeled", 0) or 0
        if unlabeled:
            buckets["Near-term (2-6 weeks)"].append(
                _action_line(
                    f"Apply labels to {unlabeled} unlabeled issues and enforce label hygiene.",
                    "Project lead",
                    "Improved reporting and discovery.",
                    "Poor portfolio visibility.",
                    f"Unlabeled issues: {unlabeled}",
                )
            )
        stale = metrics.get("stale_90d", 0) or 0
        if stale:
            buckets["Near-term (2-6 weeks)"].append(
                _action_line(
                    f"Review and update {stale} stale issues (>90 days).",
                    "Delivery lead",
                    "Backlog freshness and prioritisation.",
                    "Stale work obscures priorities.",
                    f"Stale >90d: {stale}",
                )
            )
        long_open = metrics.get("long_open_180d", 0) or 0
        if long_open:
            buckets["Longer-term (6+ weeks)"].append(
                _action_line(
                    f"Decompose or close {long_open} long-open issues (>180 days).",
                    "Delivery lead",
                    "Improved throughput and ageing control.",
                    "Aging backlog reduces delivery focus.",
                    f"Long-open >180d: {long_open}",
                )
            )

        lines = ["Action Plan"]
        for name, items in buckets.items():
            lines.append(f"\n{name}:")
            if items:
                lines.extend(items[:8])
            else:
                lines.append("- No actions identified at this time.")
        return "\n".join(lines).strip()

    executive_summary: Optional[str] = None
    if provider and not dry_run:
        try:
            provider.health_check()
            findings_text = "\n".join(
                f"- [{f.get('severity','info')}] {f.get('category')}: {f.get('message')}" for f in findings
            )
            metrics_text = json.dumps({k: v for k, v in metrics.items()}, indent=2)
            prompt = (
                "You are a conservative, reserved British professional technical writer. Using the provided Jira metrics "
                "and findings, produce a formal executive summary with four sections: Focus, Improve, Praise, Review. "
                "Use short, actionable, and strictly factually-based bullets. Avoid any sycophantic language."
            )
            msgs = [{"role": "user", "content": f"Metrics:\n{metrics_text}\n\nFindings:\n{findings_text}"}]
            resp = provider.predict(messages=msgs, max_tokens=(llm_max_tokens or 800), temperature=llm_temperature, system=prompt, timeout=llm_timeout or 90, reasoning_effort=llm_reasoning_effort)
            executive_summary = (resp.text or "").strip() or None
        except Exception:
            executive_summary = None
    if not executive_summary:
        executive_summary = _heuristic_exec()

    action_plan_text: Optional[str] = None
    if action_plan:
        if provider and not dry_run:
            try:
                findings_text = "\n".join(
                    f"- [{f.get('severity','info')}] {f.get('category')}: {f.get('message')}" for f in findings
                )
                metrics_text = json.dumps({k: v for k, v in metrics.items()}, indent=2)
                prompt = (
                    "You are a conservative, reserved British professional delivery advisor. Using the provided Jira metrics "
                    "and findings, create a concise action plan. Structure it with three sections: Immediate (0-2 weeks), "
                    "Near-term (2-6 weeks), Longer-term (6+ weeks). Each bullet should include Action, Owner role, Impact, "
                    "Risk if not addressed, and Evidence (metric/finding). Be factual, avoid speculation."
                )
                msgs = [{"role": "user", "content": f"Metrics:\n{metrics_text}\n\nFindings:\n{findings_text}"}]
                resp = provider.predict(
                    messages=msgs,
                    max_tokens=(llm_max_tokens or 900),
                    temperature=llm_temperature,
                    system=prompt,
                    timeout=llm_timeout or 90,
                    reasoning_effort=llm_reasoning_effort,
                )
                action_plan_text = (resp.text or "").strip() or None
            except Exception:
                action_plan_text = None
        if not action_plan_text:
            action_plan_text = _heuristic_action_plan()

    # Optional per-issue LLM insights (map-reduce on description)
    insights: List[Dict[str, Any]] = []
    # Parse selection manifest if provided
    selected_keys: Optional[List[str]] = None
    if selection_path and os.path.exists(selection_path or ""):
        try:
            with open(selection_path, "r", encoding="utf-8") as fsel:
                sel = json.load(fsel)
            selected_keys = [item.get("key") for item in sel.get("selectedIssues", []) if item.get("key")]
            if not llm_chunk_size:
                try:
                    # Default to 25% of context window if provider is available
                    default_chunk = (provider.get_context_window() // 4) if provider else 2000
                    llm_chunk_size = int(sel.get("chunkSize", default_chunk))
                except Exception:
                    llm_chunk_size = llm_chunk_size or 2000
        except Exception:
            selected_keys = None

    if provider and not dry_run:
        # Health check provider (skip if unhealthy)
        healthy = True
        try:
            hc = provider.health_check()
            healthy = bool(hc.get("ok"))
        except Exception:
            healthy = False
        if healthy:
            # Choose issues to analyse
            target_issues = [it for it in issues if (selected_keys is None or it.key in selected_keys)]
            # Cap to prevent runaway costs
            target_issues = target_issues[:100]
            chunk_sz = llm_chunk_size or (provider.get_context_window() // 4 if provider else 2000)
            approx_chars = max(200, int(chunk_sz) * 4)
            max_out = llm_max_tokens or 800
            # Read Confluence inclusion preferences for Jira insights
            include_conf = True
            max_conf_pages = 3
            max_conf_chars = 5000
            max_parallel_fetches = 4
            try:
                cfg2 = load_config() or {}
                jins = (cfg2.get("jira_insights") or {}) if isinstance(cfg2, dict) else {}
                include_conf = bool(jins.get("include_confluence", True))
                max_conf_pages = int(jins.get("max_confluence_pages_per_issue", 3) or 3)
                max_conf_chars = int(jins.get("max_confluence_chars_per_page", 5000) or 5000)
                max_parallel_fetches = int(jins.get("max_parallel_confluence_fetches", 4) or 4)
            except Exception:
                pass
            system = (
                "You are a conservative, reserved British professional technical writer and Jira issue quality reviewer. Assess clarity, "
                "completeness, ownership, and alignment with agile, telco, and security best practices. Be formal, "
                "reserved, concise, and actionable. Avoid any sycophantic language. Meticulously ensure that all findings and assessments "
                "are correctly attributed to the appropriate individual, avoiding any name substitution. If linked Confluence "
                "content is embedded below, treat it as authoritative context for the issue. Finish with a section "
                "titled 'Draft ticket' that contains a compact proposed ticket with fields: Summary, Description (short), "
                "Acceptance Criteria (3-5 bullets), References (links)."
            )
            def _analyze_issue(it):
                text = (it.description or "").strip()
                # Detect and attach linked Confluence page content (concurrently)
                if text:
                    appendix = _fetch_confluence_context(
                        base_url,
                        auth,
                        text,
                        include_conf=include_conf,
                        max_conf_pages=max_conf_pages,
                        max_conf_chars=max_conf_chars,
                        max_workers=max_parallel_fetches,
                    )
                    if appendix:
                        text = (text + appendix).strip()
                
                if not text:
                    return None
                
                chunks = [text[i:i+approx_chars] for i in range(0, len(text), approx_chars)] or [text]
                summaries = []
                req_count = 0
                total_latency = 0
                
                # Map phase
                for ch in chunks:
                    prompt = (
                        f"Issue: {it.summary} ({it.key})\nURL: {it.url}\n\n"
                        f"Summarize key content and identify issues/opportunities."
                    )
                    msgs = [{"role": "user", "content": prompt + "\n\nContent:\n" + ch[:approx_chars]}]
                    try:
                        resp = provider.predict(messages=msgs, max_tokens=max_out, temperature=llm_temperature, system=system, timeout=llm_timeout or 90, reasoning_effort=llm_reasoning_effort)
                        req_count += 1
                        total_latency += int(getattr(resp, "latency_ms", 0) or 0)
                        summaries.append((resp.text or "").strip() or "[Empty]")
                    except Exception as e:
                        summaries.append(f"[Failed: {e}]")
                        break
                
                combined = "\n\n".join(summaries)
                synth_prompt = f"Synthesize multiple chunk summaries for Jira issue '{it.summary}' ({it.key})."
                try:
                    resp2 = provider.predict(messages=[{"role": "user", "content": synth_prompt + "\n\nSummaries:\n" + combined}], max_tokens=max_out, temperature=llm_temperature, system=system, timeout=llm_timeout or 90, reasoning_effort=llm_reasoning_effort)
                    req_count += 1
                    total_latency += int(getattr(resp2, "latency_ms", 0) or 0)
                    return {
                        "issue": {"key": it.key, "summary": it.summary, "url": it.url},
                        "assessment": (resp2.text or "").strip() or "[Empty]",
                        "timing_ms": total_latency,
                        "requests": req_count,
                    }
                except Exception as e:
                    return {
                        "issue": {"key": it.key, "summary": it.summary, "url": it.url},
                        "assessment": f"[Synthesis failed: {e}]",
                        "timing_ms": total_latency,
                        "requests": req_count,
                    }

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(_analyze_issue, it) for it in target_issues]
                for f in as_completed(futures):
                    res = f.result()
                    if res: insights.append(res)

    # Render LLM Insights HTML section, sorted and de-duplicated by issue key
    llm_insights_html = ""
    if insights:
        items: List[str] = []
        seen = set()
        for it in sorted(insights, key=lambda x: (str(x.get("issue",{}).get("summary","" )).casefold(), str(x.get("issue",{}).get("key","")))):
            issue = it.get("issue", {})
            k = str(issue.get("key") or "")
            if not k or k in seen:
                continue
            seen.add(k)
            meta = ""
            if it.get("timing_ms") is not None or it.get("requests") is not None:
                meta = f"<div class='small muted'>LLM requests: {it.get('requests') or 0}, total latency: {it.get('timing_ms') or 0} ms</div>"
            items.append(
                f"<details><summary><a href='{issue.get('url')}' target='_blank'>{issue.get('summary')} ({issue.get('key')})</a></summary>{meta}<pre>{it.get('assessment')}</pre></details>"
            )
        llm_insights_html = "<section><h2>LLM Insights</h2>" + "".join(items) + "</section>"

    html = _render_html(
        title="Jira Quality Report",
        metrics=metrics,
        findings=findings,
        issue_rows_html="".join(issue_rows),
        executive_summary=executive_summary,
        action_plan=action_plan_text,
        llm_insights_html=llm_insights_html,
    )

    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html)

    # Optional: post AI-generated comments to Jira
    if post_comments and post_target in ("both", "jira"):
        try:
            _post_jira_comments(base_url, auth, issues, executive_summary, insights, dry_run=dry_run_post)
        except Exception:
            # Do not fail the report on posting issues
            pass


# ----------------------------
# Jira comment upsert helpers
# ----------------------------

def _jira_list_comments(base_url: str, auth: Tuple[str, str], issue_key: str, limit: int = 100) -> List[Dict[str, Any]]:
    data = _jira_get(base_url, auth, f"/rest/api/3/issue/{issue_key}/comment", params={"maxResults": limit})
    return data.get("comments", [])


def _jira_create_comment(base_url: str, auth: Tuple[str, str], issue_key: str, body_html: str) -> Optional[str]:
    payload = {"body": {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": body_html}]}
    ]}}
    try:
        data = _jira_post(base_url, auth, f"/rest/api/3/issue/{issue_key}/comment", payload)
        return str(data.get("id")) if data else None
    except Exception:
        return None


def _jira_update_comment(base_url: str, auth: Tuple[str, str], issue_key: str, comment_id: str, body_html: str) -> bool:
    payload = {"body": {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": body_html}]}
    ]}}
    try:
        _jira_put(base_url, auth, f"/rest/api/3/issue/{issue_key}/comment/{comment_id}", payload)
        return True
    except Exception:
        return False


def _jira_upsert_comment(base_url: str, auth: Tuple[str, str], issue_key: str, marker_id: str, body_visible: str) -> Tuple[str, str]:
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    visible_banner = f"AI Generated • marker {hashlib.sha256(marker_id.encode('utf-8')).hexdigest()[:8]}\n"
    body = f"<!-- {AI_MARKER_PREFIX}{marker_id};ts={ts} -->\n{visible_banner}{body_visible}"
    existing = None
    for c in _jira_list_comments(base_url, auth, issue_key, limit=500):
        # Jira 3 API returns comment body in Atlassian Document Format; marker will not be present unless using storage. We rely on raw text fallback if present.
        raw = c.get("body")
        raw_str = json.dumps(raw) if isinstance(raw, (dict, list)) else str(raw)
        if AI_MARKER_PREFIX + marker_id in (raw_str or ""):
            existing = c
            break
    if existing:
        ok = _jira_update_comment(base_url, auth, issue_key, str(existing.get("id")), body)
        return ("updated" if ok else "skipped"), str(existing.get("id"))
    else:
        cid = _jira_create_comment(base_url, auth, issue_key, body)
        return ("created" if cid else "skipped"), str(cid or "")


def _post_jira_comments(base_url: str, auth: Tuple[str, str], issues: List[IssueInfo], executive_summary: Optional[str], insights: List[Dict[str, Any]], *, dry_run: bool) -> None:
    # Prefer posting per-issue insights when available
    if insights:
        for it in sorted(insights, key=lambda x: (str(x.get("issue",{}).get("summary","" )).casefold(), str(x.get("issue",{}).get("key","")))):
            issue = it.get("issue", {})
            key = str(issue.get("key") or "")
            if not key:
                continue
            marker = f"issue:{key}"
            assessment = (it.get("assessment") or "").strip()
            meta = ""
            if it.get("timing_ms") is not None or it.get("requests") is not None:
                meta = f"\n\n(LLM requests: {it.get('requests') or 0}, latency: {it.get('timing_ms') or 0} ms)"
            body = f"AI Assessment for {key}: {issue.get('summary','')}\n\n{assessment}{meta}"
            if dry_run:
                print(f"[dry-run] Jira upsert comment on {key} marker={marker}")
            else:
                _jira_upsert_comment(base_url, auth, key, marker, body)
        return
    # Fallback: post executive summary to first 1–3 issues
    if not issues or not executive_summary:
        return
    for it in issues[:3]:
        marker = f"issue:{it.key}"
        body = f"Executive Summary for selection\n\n{executive_summary}"
        if dry_run:
            print(f"[dry-run] Jira upsert comment on {it.key} marker={marker}")
        else:
            _jira_upsert_comment(base_url, auth, it.key, marker, body)
