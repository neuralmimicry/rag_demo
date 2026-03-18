"""
Confluence space quality analysis utilities with optional Atlassian Rovo usage.

This module stays lightweight and reuses the project's existing configuration
and credentials strategy. It prefers standard Confluence Cloud REST APIs and
optionally attempts to call a Rovo/AI endpoint if configured via environment
variables. No local LLM is required.

Environment variables (optional):
- ROVO_API_URL: Base URL for a Rovo/AI analysis endpoint (experimental).
- ROVO_BEARER_TOKEN: Bearer token used to authenticate to the Rovo endpoint.

Public entrypoint:
- analyze_space_and_write_report(base_url, auth, space_key, output_html, use_rovo=False)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import os
import datetime as dt
import json
import requests
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# Optional LLM providers (OpenAI, Gemini, Ollama)
from llm_providers import get_provider, LLMProvider, LLMError, LLMQuotaError
from atlassian_utils import PageInfo, ConfluenceClient, parse_atlassian_datetime as _parse_dt


# ----------------------------
# Local caching for page bodies
# ----------------------------

CACHE_ROOT = ".confluence_cache"


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _cache_path(space_key: str, page_id: str) -> str:
    return os.path.join(CACHE_ROOT, space_key, f"{page_id}.json")


def _read_page_cache(space_key: str, page_id: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_cache_path(space_key, page_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_page_cache(space_key: str, page_id: str, payload: Dict[str, Any]) -> None:
    path = os.path.join(CACHE_ROOT, space_key)
    _ensure_dir(path)
    try:
        with open(_cache_path(space_key, page_id), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def _conf_get_body(base_url: str, auth: Tuple[str, str], content_id: str) -> Dict[str, Any]:
    # Prefer body.view (rendered HTML) which we will strip to text; body.storage is raw XHTML
    return _conf_get(base_url, auth, f"/rest/api/content/{content_id}", params={
        "expand": "body.view,version,metadata.labels,space"
    })


def _html_to_text(html: str) -> str:
    # Very lightweight HTML tag stripper; good enough for summarization context.
    import re
    txt = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    txt = re.sub(r"<style[\s\S]*?</style>", " ", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def get_page_text(base_url: str, auth: Tuple[str, str], space_key: str, page: PageInfo, ttl_hours: int = 24) -> str:
    # Try cache
    cache = _read_page_cache(space_key, page.id)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    if cache and abs(now - int(cache.get("timestamp", 0))) < ttl_hours * 3600:
        return cache.get("text", "")
    # Fetch fresh
    data = _conf_get_body(base_url, auth, page.id)
    html = data.get("body", {}).get("view", {}).get("value", "")
    text = _html_to_text(html)
    _write_page_cache(space_key, page.id, {
        "timestamp": now,
        "id": page.id,
        "title": page.title,
        "url": page.url,
        "text": text,
    })
    return text


def get_page_text_by_id(base_url: str, auth: Tuple[str, str], content_id: str, ttl_hours: int = 24) -> str:
    """Return plain text for a Confluence page by its content ID.

    This helper fetches `body.view` for the page via REST and strips HTML to text.
    It avoids coupling to space/page discovery and may bypass cache unless the
    space key can be inferred from the payload.
    """
    try:
        data = _conf_get_body(base_url, auth, str(content_id))
    except Exception:
        return ""
    html = (data or {}).get("body", {}).get("view", {}).get("value", "")
    text = _html_to_text(html or "")
    # Best-effort cache using actual space key if present
    try:
        space_key = ((data or {}).get("space", {}) or {}).get("key")
        title = (data or {}).get("title") or "page"
        url = f"{_confluence_base(base_url)}/spaces/{space_key}/pages/{content_id}/{(title or 'page').replace(' ', '-')}" if space_key else f"{_confluence_base(base_url)}/pages/{content_id}"
        if space_key:
            now = int(dt.datetime.now(dt.timezone.utc).timestamp())
            _write_page_cache(space_key, str(content_id), {
                "timestamp": now,
                "id": str(content_id),
                "title": str(title),
                "url": url,
                "text": text,
            })
    except Exception:
        # Caching is best-effort; ignore errors
        pass
    return text


def _confluence_base(base_url: str) -> str:
    # Jira base is like https://foo.atlassian.net; Confluence is /wiki under same host
    return base_url.rstrip("/") + "/wiki"


def _parse_dt(ts: Optional[str]) -> Optional[dt.datetime]:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except Exception:
        return None


def _conf_get(base_url: str, auth: Tuple[str, str], path: str, params: Optional[dict] = None) -> Dict[str, Any]:
    url = _confluence_base(base_url) + path
    logger.debug(f"Confluence GET {url}")
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, params=params or {}, auth=auth, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_space(base_url: str, auth: Tuple[str, str], space_key: str) -> Dict[str, Any]:
    return _conf_get(base_url, auth, f"/rest/api/space/{space_key}")


def fetch_space_pages(base_url: str, auth: Tuple[str, str], space_key: str, limit: int = 10000) -> List[PageInfo]:
    """
    Retrieve all pages for a space using Confluence CQL search with robust pagination.

    We continue fetching while the API returns full pages (50/100 boundaries) to avoid
    truncation at server defaults. A high safety cap (limit) prevents runaway fetches.
    """
    logger.info(f"Fetching Confluence pages for space: {space_key}")
    # Resolve paging preferences from config if available
    page_size = 100
    try:
        from main import load_config  # lazy import to avoid heavy dependencies at import time
        cfg = load_config() or {}
        conf_cfg = (cfg.get("confluence") or {}) if isinstance(cfg, dict) else {}
        page_size = int(conf_cfg.get("page_size", 100) or 100)
        limit = int(conf_cfg.get("max_items", limit) or limit)
    except Exception:
        # keep defaults on any failure
        pass

    per_page = max(1, min(page_size, 100))

    pages: List[PageInfo] = []
    cql = f"space={space_key} and type=page"
    start = 0
    while True:
        data = _conf_get(base_url, auth, "/rest/api/search", params={
            "cql": cql,
            "limit": per_page,
            "start": start,
            "expand": "content.version,content.metadata.labels,content.space"
        })
        for r in data.get("results", []):
            content = r.get("content", {})
            _id = content.get("id")
            title = content.get("title")
            # Construct public URL
            space = content.get("space", {})
            space_key_real = space.get("key", space_key)
            slug_part = title.replace(" ", "-") if isinstance(title, str) else "page"
            public_url = f"{_confluence_base(base_url)}/spaces/{space_key_real}/pages/{_id}/{slug_part}"
            version = content.get("version", {})
            when = version.get("when")
            by = version.get("by", {})
            author = by.get("displayName")
            labels = [l.get("name") for l in content.get("metadata", {}).get("labels", {}).get("results", []) if l.get("name")]
            pages.append(PageInfo(
                id=str(_id),
                title=str(title),
                url=public_url,
                last_updated=_parse_dt(when),
                author=author,
                labels=labels,
            ))
        size = int(data.get("size", 0) or 0)
        if size == 0:
            break
        start += size
        # Respect overall safety cap
        if start >= limit:
            break
    return pages


def _conf_get_content_with_ancestors(base_url: str, auth: Tuple[str, str], content_id: str) -> Dict[str, Any]:
    return _conf_get(base_url, auth, f"/rest/api/content/{content_id}", params={
        "expand": "ancestors,version,metadata.labels,space"
    })


def enrich_pages_with_ancestors(base_url: str, auth: Tuple[str, str], pages: List[PageInfo], per_page_timeout: int = 30) -> None:
    """
    Populate each PageInfo with ancestors, parent_id, and computed depth.
    Also builds children lists by linking parent->child.

    Parallelized using ThreadPoolExecutor for faster enrichment of large spaces.
    """
    client = ConfluenceClient(base_url, auth, timeout=per_page_timeout)
    id_to_page: Dict[str, PageInfo] = {p.id: p for p in pages}

    def _enrich_single(p: PageInfo):
        try:
            data = client.get(f"/rest/api/content/{p.id}", params={"expand": "ancestors"})
            ancestors = data.get("ancestors", []) or []
            p.ancestors = [str(a.get("id")) for a in ancestors if a.get("id") is not None]
            p.parent_id = p.ancestors[-1] if p.ancestors else None
            p.depth = len(p.ancestors)
        except Exception as e:
            logger.warning(f"Failed to fetch ancestors for page {p.id}: {e}")
            p.ancestors = []
            p.parent_id = None
            p.depth = None

    # Parallel fetch of ancestors
    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(_enrich_single, pages)

    # Build children links (must be done after all depths/parents are known)
    for p in pages:
        if p.parent_id and p.parent_id in id_to_page:
            if p.id not in id_to_page[p.parent_id].children:
                id_to_page[p.parent_id].children.append(p.id)


def compute_hierarchy_metrics(pages: List[PageInfo]) -> Dict[str, Any]:
    """
    Compute tree-oriented statistics from enriched pages.
    Expects pages to have depth/parent/children populated.
    """
    total = len(pages)
    roots = [p for p in pages if (p.depth == 0) or (p.parent_id is None)]
    leaves = [p for p in pages if len(p.children) == 0]
    depths = [p.depth for p in pages if p.depth is not None]
    max_depth = max(depths) if depths else 0
    avg_depth = round(sum(depths) / len(depths), 2) if depths else 0.0

    # Hubs: pages with many direct children
    hubs = sorted(
        [(p.title, p.url, len(p.children)) for p in pages if len(p.children) >= 5],
        key=lambda t: t[2],
        reverse=True
    )[:10]

    # Deep paths: pages with depth >= 5
    deep_nodes = [p for p in pages if (p.depth or 0) >= 5]

    # Leaf quality: unlabeled or stale leaves
    now = dt.datetime.now(dt.timezone.utc)
    stale_cutoff = now - dt.timedelta(days=180)
    leaf_unlabeled = sum(1 for p in leaves if not p.labels)
    leaf_stale = sum(1 for p in leaves if p.last_updated and p.last_updated < stale_cutoff)

    return {
        "total_pages": total,
        "root_pages": len(roots),
        "leaf_pages": len(leaves),
        "max_depth": max_depth,
        "avg_depth": avg_depth,
        "hubs_top": hubs,  # list of (title, url, child_count)
        "deep_nodes_count": len(deep_nodes),
        "leaf_unlabeled": leaf_unlabeled,
        "leaf_stale": leaf_stale,
    }


def filter_pages_by_max_depth(pages: List[PageInfo], max_depth: int) -> List[PageInfo]:
    """
    Return a new list limited to pages with depth <= max_depth (inclusive).
    Pages with unknown depth (depth is None) are KEPT to avoid dropping content
    when ancestor enrichment is incomplete. We still trim each kept page's
    children list to only include other kept pages so that downstream metrics
    (e.g., leaf detection) stay consistent.
    """
    if max_depth < 0:
        return []
    # Keep pages whose depth is unknown OR within the inclusive max_depth
    kept = [p for p in pages if (p.depth is None or p.depth <= max_depth)]
    kept_ids = {p.id for p in kept}
    for p in kept:
        # Keep only children that are also within the kept set
        p.children = [cid for cid in p.children if cid in kept_ids]
    return kept


def scope_pages_from_starting_depth(
    pages: List[PageInfo],
    starting_depth: int,
    relative_depth: int,
) -> Tuple[List[PageInfo], Dict[str, List[str]]]:
    """
    Build a scoped set that starts at all pages with depth == starting_depth and
    includes their descendants up to `relative_depth` levels below (inclusive).

    This uses children traversal so it works even when some nodes have unknown
    absolute depth (`depth is None`). Returns the kept pages (preserving original
    order) and a mapping from each branch-root id (depth==starting_depth) to the
    list of member page ids in its scoped subtree (also preserving traversal order).
    Children links of kept pages are trimmed to the kept set for downstream metrics.
    """
    if relative_depth < 0:
        return [], {}
    id_to_page: Dict[str, PageInfo] = {p.id: p for p in pages}
    # Identify branch roots at the specified starting depth
    branch_roots: List[PageInfo] = [p for p in pages if p.depth == starting_depth]
    kept_ids: List[str] = []
    kept_set: set[str] = set()
    groups: Dict[str, List[str]] = {}
    for root in branch_roots:
        grp: List[str] = []
        groups[root.id] = grp
        # BFS from root up to `relative_depth` levels (0 means just the root)
        frontier: List[str] = [root.id]
        visited: set[str] = set()
        level = 0
        while frontier and level <= relative_depth:
            next_frontier: List[str] = []
            for pid in frontier:
                if pid in visited:
                    continue
                visited.add(pid)
                if pid in id_to_page:
                    if pid not in kept_set:
                        kept_set.add(pid)
                        kept_ids.append(pid)
                    grp.append(pid)
                    # Traverse children regardless of their absolute depth value
                    for cid in id_to_page[pid].children:
                        next_frontier.append(cid)
            frontier = next_frontier
            level += 1
    # Preserve original order while keeping only the scoped ids
    kept_pages: List[PageInfo] = [p for p in pages if p.id in kept_set]
    # Trim children of kept pages to improve leaf detection consistency
    kept_id_set = set(kept_set)
    for p in kept_pages:
        p.children = [cid for cid in p.children if cid in kept_id_set]
    return kept_pages, groups


def _try_rovo_analysis(base_url: str, space_key: str, pages: List[PageInfo]) -> Optional[Dict[str, Any]]:
    api = os.getenv("ROVO_API_URL")
    token = os.getenv("ROVO_BEARER_TOKEN")
    if not api or not token:
        return None
    try:
        payload = {
            "spaceKey": space_key,
            "pages": [{"id": p.id, "title": p.title, "url": p.url} for p in pages[:50]],
            "prompt": "Provide a concise quality review of this Confluence space: structure, clarity, freshness, ownership, alignment with agile/telco/security best practices."
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.post(api.rstrip("/") + "/analyze/confluence-space", headers=headers, data=json.dumps(payload), timeout=180)
        if resp.status_code >= 200 and resp.status_code < 300:
            return resp.json()
    except Exception:
        return None
    return None


def _baseline_metrics(pages: List[PageInfo]) -> Dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    stale_cutoff = now - dt.timedelta(days=180)
    total = len(pages)
    with_labels = sum(1 for p in pages if p.labels)
    stale = sum(1 for p in pages if p.last_updated and p.last_updated < stale_cutoff)
    unknown_dates = sum(1 for p in pages if not p.last_updated)
    authors = {}
    for p in pages:
        if p.author:
            authors[p.author] = authors.get(p.author, 0) + 1
    top_authors = sorted(authors.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "total_pages": total,
        "pages_with_labels": with_labels,
        "stale_pages_180d": stale,
        "unknown_update_dates": unknown_dates,
        "top_authors": top_authors,
    }


def _render_html(
    space: Dict[str, Any],
    metrics: Dict[str, Any],
    findings: List[Dict[str, Any]],
    rovo_summary: Optional[Dict[str, Any]],
    hierarchy_full: Optional[Dict[str, Any]] = None,
    hierarchy_scoped: Optional[Dict[str, Any]] = None,
    scope_depth: Optional[int] = None,
    executive_summary: Optional[str] = None,
    action_plan: Optional[str] = None,
) -> str:
    title = f"Confluence Space Quality Report — {space.get('name', space.get('key'))}"
    rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items() if k != "top_authors"
    )
    # Render authors alphabetically by name for easy scanning
    authors_sorted = sorted(metrics.get("top_authors", []), key=lambda kv: (str(kv[0]).casefold()))
    authors_html = "".join(f"<li>{name}: {count}</li>" for name, count in authors_sorted)
    # De-duplicate and sort findings by category then message (alphabetically)
    seen_findings = set()
    dedup_findings: List[Dict[str, Any]] = []
    for f in findings:
        key = (str(f.get('category','')).strip().casefold(), str(f.get('message','')).strip().casefold(), str(f.get('severity','')).strip().casefold())
        if key in seen_findings:
            continue
        seen_findings.add(key)
        dedup_findings.append(f)
    findings_sorted = sorted(dedup_findings, key=lambda f: (str(f.get('category','')).casefold(), str(f.get('message','')).casefold()))
    findings_html = "".join(
        f"<li><strong>{f.get('category')}:</strong> {f.get('message')} <em>({f.get('severity','info')})</em></li>" for f in findings_sorted
    )
    rovo_html = ""
    if rovo_summary:
        rovo_text = rovo_summary.get("summary") or json.dumps(rovo_summary)
        rovo_html = f"<section><h2>Rovo Summary</h2><pre>{rovo_text}</pre></section>"
    def _render_hierarchy_block(title_hdr: str, h: Dict[str, Any]) -> str:
        # De-duplicate hubs by (title,url) to avoid rare duplicates
        seen_hubs = set()
        hubs_rows = ""
        for title, url, children in sorted(h.get("hubs_top", []), key=lambda t: str(t[0]).casefold()):
            key = (str(title).strip().casefold(), str(url).strip())
            if key in seen_hubs:
                continue
            seen_hubs.add(key)
            hubs_rows += f"<tr><td><a href='{url}' target='_blank'>{title}</a></td><td>{children}</td></tr>"
        hierarchy_rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in [
                ("Root pages", h.get("root_pages")),
                ("Leaf pages", h.get("leaf_pages")),
                ("Max depth", h.get("max_depth")),
                ("Average depth", h.get("avg_depth")),
                ("Deep nodes (>=5 levels)", h.get("deep_nodes_count")),
                ("Leaf pages without labels", h.get("leaf_unlabeled")),
                ("Leaf pages stale >180d", h.get("leaf_stale")),
            ]
        )
        return f"""
    <section>
      <h2>{title_hdr}</h2>
      <table>
        <thead><tr><th>Metric</th><th>Value</th></tr></thead>
        <tbody>
          {hierarchy_rows}
        </tbody>
      </table>
      <h3>Top Hubs (most direct children)</h3>
      <table>
        <thead><tr><th>Page</th><th>Direct Children</th></tr></thead>
        <tbody>
          {hubs_rows}
        </tbody>
      </table>
    </section>
        """

    hierarchy_html = ""
    if hierarchy_full:
        hierarchy_html += _render_hierarchy_block("Hierarchy Summary — Full Space", hierarchy_full)
    if hierarchy_scoped is not None and scope_depth is not None:
        hierarchy_html += _render_hierarchy_block(f"Hierarchy Summary — Analysis scope (≤ {scope_depth})", hierarchy_scoped)
    exec_html = ""
    if executive_summary:
        exec_html = f"""
    <section>
      <h2>Executive Summary</h2>
      <pre>{executive_summary}</pre>
    </section>
        """
    action_html = ""
    if action_plan:
        action_html = f"""
    <section>
      <h2>Action Plan</h2>
      <pre>{action_plan}</pre>
    </section>
        """

    scope_notice = ""
    if scope_depth is not None:
        scope_notice = (
            f"<div class='small muted'>Analysis scope limited to depth ≤ {scope_depth}; deeper pages are still counted in Baseline Metrics.</div>"
        )

    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
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
    // Simple interactive selection manifest creator
    function toggleAll(cls, checked) {{
      document.querySelectorAll('.' + cls).forEach(cb => cb.checked = checked);
      updateEstimates();
    }}
    function updateEstimates() {{
      let totalTokens = 0;
      document.querySelectorAll('.page-row').forEach(row => {{
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
      document.querySelectorAll('.page-row').forEach(row => {{
        const cb = row.querySelector('input[type=checkbox]');
        if (cb && cb.checked) {{
          items.push({{ id: row.getAttribute('data-id'), title: row.getAttribute('data-title') }});
        }}
      }});
      const manifest = {{ version: 1, selectedPages: items, chunkSize: parseInt((document.getElementById('chunkSize')||{{}}).value||'2000') }};
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
    <p><strong>Space:</strong> {space.get('name')} ({space.get('key')})</p>
    {exec_html}
    {action_html}
    <section>
      <h2>Baseline Metrics</h2>
      <table>
        <thead><tr><th>Metric</th><th>Value</th></tr></thead>
        <tbody>
          {rows}
        </tbody>
      </table>
      <h3>Top Authors</h3>
      <ul>{authors_html}</ul>
    </section>
    <section>
      <h2>Findings & Recommendations</h2>
      <ul>{findings_html}</ul>
    </section>
    {hierarchy_html}
    <section>
      <h2>Interactive Analysis (LLM scope selection)</h2>
      <p class="muted">Use the controls below to choose which pages to include in LLM analysis and download a selection manifest. Then re-run the CLI with <code>--selection selection.json</code> and an LLM provider.</p>
      <div class="small">Token estimates are heuristic (≈1 token ≈ 4 chars).</div>
      {scope_notice}
      <div style="margin: 0.5rem 0;">
        <button onclick="toggleAll('page-checkbox', true)">Select all</button>
        <button onclick="toggleAll('page-checkbox', false)">Deselect all</button>
        &nbsp; | Chunk size (tokens): <input id="chunkSize" type="number" value="2000" min="200" max="8000" step="100" oninput="updateEstimates()" />
        &nbsp; | Estimated selected tokens: <strong id="selTokens">0</strong>
        &nbsp; (~requests: <strong id="selReqs">0</strong>)
        &nbsp; <button onclick="downloadSelection()">Download Selection JSON</button>
      </div>
      <table>
        <thead><tr><th>Include</th><th>Title</th><th>Depth</th><th>Labels</th><th>Last Updated</th><th>Est. tokens</th></tr></thead>
        <tbody id="pageList">
        <!-- Rows injected server-side below -->
        </tbody>
      </table>
      <script>
        // Populate rows provided by server-side placeholder
        updateEstimates();
      </script>
    </section>
    {rovo_html}
  </body>
</html>
"""


def analyze_space_and_write_report(
    base_url: str,
    auth: Tuple[str, str],
    space_key: str,
    output_html: str,
    use_rovo: bool = False,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    selection_path: Optional[str] = None,
    emit_templates: bool = False,
    templates_dir: Optional[str] = None,
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
    post_exec_summary: bool = True,
    post_page_insights: bool = True,
    dry_run_post: bool = False,
    tree_max_depth: Optional[int] = None,
    starting_depth: Optional[int] = None,
    llm_inter_request_gap: float = 3.0,
    fallback_llm_provider: Optional[str] = None,
    fallback_llm_model: Optional[str] = None,
    fallback_llm_api_key: Optional[str] = None,
) -> None:
    space = fetch_space(base_url, auth, space_key)
    pages = fetch_space_pages(base_url, auth, space_key)
    # De-duplicate pages by ID to avoid rendering duplicates in any section
    unique_pages_map: Dict[str, PageInfo] = {}
    for p in pages:
        if p and p.id and p.id not in unique_pages_map:
            unique_pages_map[p.id] = p
    pages = list(unique_pages_map.values())
    # Enrich with hierarchy (ancestors/children/depth)
    enrich_pages_with_ancestors(base_url, auth, pages)
    # Compute baseline metrics and full hierarchy BEFORE any scoping/filtering
    pages_full = pages
    metrics = _baseline_metrics(pages_full)
    hierarchy_full = compute_hierarchy_metrics(pages_full)
    # Build analysis scope. Two modes:
    #  - Branch mode: when starting_depth is specified, traverse from each branch root
    #    at that depth down `tree_max_depth` levels (relative). Unknown-depth pages are
    #    included via children traversal.
    #  - Global depth mode: when only tree_max_depth is specified, keep pages with
    #    depth <= N (inclusive), preserving unknown depths.
    pages_scope: List[PageInfo] = pages_full
    scope_depth: Optional[int] = None  # kept for compatibility with renderer notice
    branch_groups: Optional[Dict[str, List[str]] ] = None
    if starting_depth is not None:
        rel = int(tree_max_depth) if isinstance(tree_max_depth, int) else 0
        # Compute scoped union and branch group mapping
        pages_scope, branch_groups = scope_pages_from_starting_depth(list(pages_full), int(starting_depth), rel)
    elif tree_max_depth is not None:
        try:
            scope_depth = int(tree_max_depth)
        except Exception:
            scope_depth = None
        if scope_depth is not None:
            pages_scope = filter_pages_by_max_depth(list(pages_full), scope_depth)
    hierarchy_scoped = compute_hierarchy_metrics(pages_scope)

    # Best-practices evaluation
    from best_practices import evaluate_against_baselines
    findings = evaluate_against_baselines(space, pages_scope, metrics)

    # Tree-aware additional findings
    # - Excessive depth
    ref_h = hierarchy_scoped if scope_depth is not None else hierarchy_full
    if ref_h.get("max_depth", 0) >= 6:
        findings.append({
            "category": "Information Architecture",
            "severity": "medium",
            "message": "Page hierarchy is very deep (>= 6 levels). Consider flattening or introducing index pages to improve findability."
        })
    # - Oversized hubs
    hubs_top = ref_h.get("hubs_top", [])
    if hubs_top and hubs_top[0][2] >= 30:
        findings.append({
            "category": "Information Architecture",
            "severity": "medium",
            "message": f"Some pages act as hubs with very large numbers of children (e.g., {hubs_top[0][2]}). Split content into sub-sections or use index pages."
        })
    # - Leaf quality
    if ref_h.get("leaf_unlabeled", 0) > 0:
        findings.append({
            "category": "Metadata Quality",
            "severity": "low",
            "message": f"{ref_h.get('leaf_unlabeled')} leaf pages have no labels. Label leaves to support search and governance."
        })
    if ref_h.get("leaf_stale", 0) > 0:
        findings.append({
            "category": "Content Freshness",
            "severity": "low",
            "message": f"{ref_h.get('leaf_stale')} leaf pages appear stale (>180 days). Encourage owners to review and update or archive."
        })

    rovo_summary = _try_rovo_analysis(base_url, space_key, pages_scope) if use_rovo else None

    # Build interactive table rows with token estimates (based on cached/fetched text), sorted alphabetically by title
    # Parallelize text fetching for better performance
    client = ConfluenceClient(base_url, auth)
    def _fetch_text(p: PageInfo):
        try:
            return get_page_text(base_url, auth, space_key, p)
        except Exception:
            return ""

    sorted_scoped = sorted(pages_scope, key=lambda pg: str(pg.title).casefold())
    unique_scoped = []
    seen_ids = set()
    for p in sorted_scoped:
        if p.id not in seen_ids:
            seen_ids.add(p.id)
            unique_scoped.append(p)

    with ThreadPoolExecutor(max_workers=10) as executor:
        texts = list(executor.map(_fetch_text, unique_scoped))

    table_rows = []
    for p, text in zip(unique_scoped, texts):
        est_tokens = max(1, int(len(text) / 4))
        depth = p.depth if p.depth is not None else "-"
        labels = ", ".join(p.labels)
        last = p.last_updated.isoformat() if p.last_updated else "-"
        # Use custom attributes for JS to compute totals
        row = (
            f"<tr class='page-row' data-id='{p.id}' data-title='{p.title}' data-tokens='{est_tokens}'>"
            f"<td><input type='checkbox' class='page-checkbox' onclick='updateEstimates()' /></td>"
            f"<td><a href='{p.url}' target='_blank'>{p.title}</a></td>"
            f"<td>{depth}</td>"
            f"<td>{labels}</td>"
            f"<td>{last}</td>"
            f"<td>{est_tokens}</td>"
            f"</tr>"
        )
        table_rows.append(row)

    # If LLM analysis requested, perform selection-driven or default analysis
    llm_insights: List[Dict[str, Any]] = []
    provider: Optional[LLMProvider] = None
    if llm_provider:
        try:
            provider = get_provider(llm_provider, model=llm_model, base_url=ollama_base_url, inter_request_gap=llm_inter_request_gap)
        except LLMError as e:
            provider = None
            findings.append({
                "category": "LLM Integration",
                "severity": "low",
                "message": f"LLM provider setup failed: {e}"
            })
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
            except LLMError as e:
                findings.append({
                    "category": "LLM Integration",
                    "severity": "low",
                    "message": f"Fallback LLM provider setup failed: {e}"
                })
        # Preflight health check for availability and latency
        if provider:
            try:
                health = provider.health_check()
                if not health.get("ok"):
                    findings.append({
                        "category": "LLM Integration",
                        "severity": "low",
                        "message": f"LLM health check failed (provider={provider.name}): {health.get('message')}"
                    })
                    provider = None
                else:
                    # Record basic availability and latency info in findings (info level)
                    findings.append({
                        "category": "LLM Integration",
                        "severity": "info",
                        "message": f"LLM provider {provider.name}/{provider.model} reachable (latency ~{health.get('latency_ms')} ms)."
                    })
            except Exception as e:
                findings.append({
                    "category": "LLM Integration",
                    "severity": "low",
                    "message": f"LLM health check raised error: {e}"
                })
                provider = None

    selected_ids: Optional[List[str]] = None
    if selection_path and os.path.exists(selection_path):
        try:
            with open(selection_path, "r", encoding="utf-8") as fsel:
                sel = json.load(fsel)
            selected_ids = [item.get("id") for item in sel.get("selectedPages", []) if item.get("id")]
            if not llm_chunk_size:
                # Default to 25% of context window if provider is available
                default_chunk = (provider.get_context_window() // 4) if provider else 2000
                llm_chunk_size = int(sel.get("chunkSize", default_chunk))
        except Exception:
            selected_ids = None

    if provider and not dry_run:
        chunk_sz = llm_chunk_size or (provider.get_context_window() // 4 if provider else 2000)
        max_out = llm_max_tokens or 800
        system = (
            "You are a conservative, reserved British professional technical writer and reviewer. Assess clarity, structure, "
            "freshness, ownership, and alignment with agile, telco, and security best practices. Maintain a formal, "
            "reserved tone and avoid any sycophantic language. Meticulously ensure that all findings and assessments "
            "are correctly attributed to the appropriate individual, avoiding any name substitution. Provide concrete, "
            "factually-based, and concise recommendations."
        )
        if starting_depth is not None:
            # Build one insight per branch root at starting_depth, aggregating its scoped subtree
            # Recompute groups if needed
            if branch_groups is None:
                _, branch_groups = scope_pages_from_starting_depth(list(pages_full), int(starting_depth), int(tree_max_depth or 0))
            id_to_page: Dict[str, PageInfo] = {p.id: p for p in pages_full}
            # Respect selection if provided: keep only branch roots whose subtree intersects selection (or no selection -> all)
            branch_root_ids = [rid for rid in (branch_groups or {}).keys()]
            
            def _analyze_branch(rid):
                members = branch_groups.get(rid, [])
                # Apply selection filter
                if selected_ids is not None:
                    if not any(mid in selected_ids for mid in members):
                        return None
                
                # Aggregate texts
                combined_text_parts: List[str] = []
                for mid in members:
                    p = id_to_page.get(mid)
                    if not p: continue
                    try: t = get_page_text(base_url, auth, space_key, p)
                    except Exception: t = ""
                    combined_text_parts.append(f"## {p.title}\n{t}")
                
                combined_text = "\n\n".join(combined_text_parts)
                approx_chars = chunk_sz * 4
                chunks = [combined_text[i:i+approx_chars] for i in range(0, len(combined_text), approx_chars)] or [combined_text]
                summaries = []
                req_count = 0
                latency_ms = 0
                
                # Map phase (sequential per branch to keep chunk order, but branches run in parallel)
                for ch in chunks:
                    prompt = f"Branch root: {id_to_page.get(rid).title if id_to_page.get(rid) else rid}\nSummarize branch content."
                    msgs = [{"role": "user", "content": prompt + "\n\nContent:\n" + ch[:approx_chars]}]
                    try:
                        resp = provider.predict(messages=msgs, max_tokens=max_out, temperature=llm_temperature, system=system, timeout=llm_timeout or 90, reasoning_effort=llm_reasoning_effort)
                        req_count += 1
                        latency_ms += int(getattr(resp, "latency_ms", 0) or 0)
                        summaries.append((resp.text or "").strip() or "[Empty]")
                    except Exception as e:
                        summaries.append(f"[Failed: {e}]")
                        break
                
                # Synthesis phase
                combined_summary = "\n\n".join(summaries)
                synth_prompt = "Synthesize multiple chunk summaries for this Confluence branch into one quality assessment."
                try:
                    resp2 = provider.predict(messages=[{"role": "user", "content": synth_prompt + "\n\nSummaries:\n" + combined_summary}], max_tokens=max_out, temperature=llm_temperature, system=system, timeout=llm_timeout or 90, reasoning_effort=llm_reasoning_effort)
                    req_count += 1
                    latency_ms += int(getattr(resp2, "latency_ms", 0) or 0)
                    root_page = id_to_page.get(rid) or PageInfo(id=rid, title=f"Branch {rid}", url="")
                    return {
                        "page": {"id": root_page.id, "title": root_page.title, "url": root_page.url},
                        "assessment": (resp2.text or "").strip() or "[Empty]",
                        "timing_ms": latency_ms,
                        "requests": req_count,
                    }
                except Exception as e:
                    root_page = id_to_page.get(rid) or PageInfo(id=rid, title=f"Branch {rid}", url="")
                    return {"page": {"id": root_page.id, "title": root_page.title, "url": root_page.url}, "assessment": f"[Synthesis failed: {e}]", "timing_ms": latency_ms, "requests": req_count}

            # Parallelize branch analysis
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(_analyze_branch, rid) for rid in branch_root_ids[:100]]
                for f in as_completed(futures):
                    res = f.result()
                    if res: llm_insights.append(res)
        else:
            # Original per-page behaviour parallelised
            target_pages = [p for p in pages_scope if (selected_ids is None or p.id in selected_ids)]
            target_pages = target_pages[:100]
            
            def _analyze_page(p):
                text = get_page_text(base_url, auth, space_key, p)
                approx_chars = chunk_sz * 4
                chunks = [text[i:i+approx_chars] for i in range(0, len(text), approx_chars)] or [text]
                summaries = []
                req_count = 0
                latency_ms = 0
                for ch in chunks:
                    prompt = f"Page: {p.title}\nSummarize key content."
                    msgs = [{"role": "user", "content": prompt + "\n\nContent:\n" + ch[:approx_chars]}]
                    try:
                        resp = provider.predict(messages=msgs, max_tokens=max_out, temperature=llm_temperature, system=system, timeout=llm_timeout or 90, reasoning_effort=llm_reasoning_effort)
                        req_count += 1
                        latency_ms += int(getattr(resp, "latency_ms", 0) or 0)
                        summaries.append((resp.text or "").strip() or "[Empty]")
                    except Exception as e:
                        summaries.append(f"[Failed: {e}]")
                        break
                combined = "\n\n".join(summaries)
                synth_prompt = f"Synthesize multiple chunk summaries for page '{p.title}'."
                try:
                    resp2 = provider.predict(messages=[{"role": "user", "content": synth_prompt + "\n\nSummaries:\n" + combined}], max_tokens=max_out, temperature=llm_temperature, system=system, timeout=llm_timeout or 90, reasoning_effort=llm_reasoning_effort)
                    req_count += 1
                    latency_ms += int(getattr(resp2, "latency_ms", 0) or 0)
                    return {
                        "page": {"id": p.id, "title": p.title, "url": p.url},
                        "assessment": (resp2.text or "").strip() or "[Empty]",
                        "timing_ms": latency_ms,
                        "requests": req_count,
                    }
                except Exception as e:
                    return {"page": {"id": p.id, "title": p.title, "url": p.url}, "assessment": f"[Synthesis failed: {e}]", "timing_ms": latency_ms, "requests": req_count}

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(_analyze_page, p) for p in target_pages]
                for f in as_completed(futures):
                    llm_insights.append(f.result())

        # Render LLM insights into HTML (append section)
        if llm_insights:
            pass

    # Optionally emit templates based on LLM or heuristics
    if emit_templates:
        out_dir = templates_dir or os.path.join("templates", space.get("key", space_key))
        _ensure_dir(out_dir)
        def _write(path: str, content: str) -> None:
            try:
                with open(path, "w", encoding="utf-8") as tf:
                    tf.write(content)
            except Exception:
                pass
        # If we have LLM insights, generate page-specific improvement drafts
        if llm_insights:
            for it in llm_insights[:20]:
                title = it.get("page", {}).get("title", "page")
                safe = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_"))[:60].strip().replace(" ", "-")
                md = f"# Improvement Plan: {title}\n\n{it.get('assessment')}\n"
                _write(os.path.join(out_dir, f"improve-{safe}.md"), md)
        # Emit standard templates
        _write(os.path.join(out_dir, "TEMPLATE-ADR.md"), "# Architecture Decision Record (ADR)\n\n## Context\n...\n\n## Decision\n...\n\n## Consequences\n...\n")
        _write(os.path.join(out_dir, "TEMPLATE-RUNBOOK.md"), "# Runbook\n\n## Overview\n...\n\n## Procedures\n...\n\n## Contacts\n...\n")
        _write(os.path.join(out_dir, "TEMPLATE-RFC.md"), "# Request for Comments (RFC)\n\n## Summary\n...\n\n## Motivation\n...\n\n## Proposal\n...\n\n## Alternatives\n...\n")

    # Build Executive Summary (LLM-backed if available, else heuristic)
    # Choose hierarchy for executive summary (scoped if available)
    exec_h = hierarchy_scoped if scope_depth is not None else hierarchy_full

    def _heuristic_exec_summary() -> str:
        lines: List[str] = []
        total = metrics.get("total_pages", 0)
        with_labels = metrics.get("pages_with_labels", 0)
        stale = metrics.get("stale_pages_180d", 0)
        avg_depth = exec_h.get("avg_depth") if exec_h else None
        max_depth = exec_h.get("max_depth") if exec_h else None
        # Prepare buckets based on findings severity/category
        focus: List[str] = []
        improve: List[str] = []
        praise: List[str] = []
        review: List[str] = []
        for fnd in sorted(findings, key=lambda f: (str(f.get('category','')).casefold(), str(f.get('message','')).casefold())):
            sev = (fnd.get("severity") or "info").lower()
            msg = f"{fnd.get('category')}: {fnd.get('message')}"
            if sev in ("high",):
                focus.append(msg)
            elif sev in ("medium",):
                improve.append(msg)
            elif sev in ("low", "info"):
                review.append(msg)
        if total and with_labels / max(1, total) > 0.7:
            praise.append("Good label coverage observed in the space.")
        if exec_h and (max_depth or 0) <= 3:
            praise.append("Hierarchy depth is reasonable, aiding findability.")
        # Compose
        def sec(name: str, items: List[str]) -> str:
            if not items:
                return f"{name}:\n- None at this time.\n"
            return name + ":\n- " + "\n- ".join(items) + "\n"
        lines.append(f"Space '{space.get('name')}' Executive Summary\n")
        lines.append(f"Overview: {total} pages; labels on {with_labels}; stale >180d: {stale}; avg depth: {avg_depth}, max depth: {max_depth}.\n")
        lines.append(sec("Areas to Focus", focus))
        lines.append(sec("Areas to Improve", improve))
        lines.append(sec("Areas to Praise", praise))
        lines.append(sec("Areas to Review", review))
        return "\n".join(lines).strip()

    def _heuristic_action_plan() -> str:
        def _owner_for(category: str) -> str:
            cat = (category or "").lower()
            if "security" in cat:
                return "Security lead"
            if "metadata" in cat or "label" in cat:
                return "Knowledge manager"
            if "information architecture" in cat or "hierarchy" in cat:
                return "Space owner"
            if "content freshness" in cat:
                return "Content owners"
            return "Documentation lead"

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
            impact = "Improves clarity and governance."
            risk = "Documentation quality degrades."
            evidence = f"{category}: {msg}"
            action = f"Address {category.lower()} gap: {msg}"
            if sev in ("high",):
                buckets["Immediate (0-2 weeks)"].append(_action_line(action, owner, impact, risk, evidence))
            elif sev in ("medium",):
                buckets["Near-term (2-6 weeks)"].append(_action_line(action, owner, impact, risk, evidence))
            else:
                buckets["Longer-term (6+ weeks)"].append(_action_line(action, owner, impact, risk, evidence))

        total = metrics.get("total_pages", 0) or 0
        with_labels = metrics.get("pages_with_labels", 0) or 0
        stale = metrics.get("stale_pages_180d", 0) or 0
        if total and with_labels < total:
            buckets["Near-term (2-6 weeks)"].append(
                _action_line(
                    "Improve label coverage on pages.",
                    "Knowledge manager",
                    "Better search and governance.",
                    "Low discoverability.",
                    f"Labels on {with_labels}/{total} pages",
                )
            )
        if stale:
            buckets["Near-term (2-6 weeks)"].append(
                _action_line(
                    f"Review and update {stale} stale pages (>180 days).",
                    "Content owners",
                    "Improved freshness and trust.",
                    "Outdated guidance remains in circulation.",
                    f"Stale pages >180d: {stale}",
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

    executive_summary_text: Optional[str] = None
    if provider and not dry_run:
        try:
            # Build concise material for the LLM
            findings_text = "\n".join(
                f"- [{f.get('severity','info')}] {f.get('category')}: {f.get('message')}" for f in sorted(findings, key=lambda f: (str(f.get('category','')).casefold(), str(f.get('message','')).casefold()))
            )
            metrics_text = json.dumps({k: v for k, v in metrics.items() if k != "top_authors"}, indent=2)
            hierarchy_text = json.dumps(exec_h or {}, indent=2)
            insights_text = "\n\n".join(
                f"Page: {it.get('page',{}).get('title')}\nAssessment:\n{it.get('assessment')}" for it in sorted(llm_insights, key=lambda x: str(x.get('page',{}).get('title','')).casefold())
            )
            prompt = (
                "You are a conservative, reserved British professional technical writer and expert documentation quality consultant. "
                "Using the provided metrics, hierarchy stats, findings, and page-level assessments, "
                "draft a formal executive summary for the Confluence space. Include four sections with bullet points:\n"
                "1) Areas to Focus (urgent/high-risk items)\n2) Areas to Improve (medium-term opportunities)\n3) Areas to Praise (what works well)\n4) Areas to Review (low-severity checks).\n"
                "Be formal, reserved, and strictly factually-based. Avoid any sycophantic language. Meticulously ensure that all "
                "findings and assessments are correctly attributed to the appropriate individual, avoiding any name substitution. "
                "Prefer short bullets."
            )
            messages = [
                {"role": "user", "content": (
                    f"Space: {space.get('name')} ({space.get('key')})\n\n"
                    f"Baseline metrics:\n{metrics_text}\n\nHierarchy:\n{hierarchy_text}\n\nFindings:\n{findings_text}\n\nPage assessments (optional):\n{insights_text[:12000]}"
                )}
            ]
            resp = provider.predict(messages=messages, max_tokens=(llm_max_tokens or 800), temperature=llm_temperature, system="Provide an executive summary as instructed.", timeout=llm_timeout or 90, reasoning_effort=llm_reasoning_effort)
            executive_summary_text = (resp.text or "").strip() or None
        except Exception:
            executive_summary_text = None
    if not executive_summary_text:
        executive_summary_text = _heuristic_exec_summary()

    action_plan_text: Optional[str] = None
    if action_plan:
        if provider and not dry_run:
            try:
                findings_text = "\n".join(
                    f"- [{f.get('severity','info')}] {f.get('category')}: {f.get('message')}" for f in sorted(findings, key=lambda f: (str(f.get('category','')).casefold(), str(f.get('message','')).casefold()))
                )
                metrics_text = json.dumps({k: v for k, v in metrics.items() if k != "top_authors"}, indent=2)
                hierarchy_text = json.dumps(exec_h or {}, indent=2)
                prompt = (
                    "You are a conservative, reserved British professional documentation quality advisor. Using the provided "
                    "Confluence metrics, hierarchy stats, and findings, create a concise action plan. Structure it with three "
                    "sections: Immediate (0-2 weeks), Near-term (2-6 weeks), Longer-term (6+ weeks). Each bullet should include "
                    "Action, Owner role, Impact, Risk if not addressed, and Evidence. Be factual, avoid speculation."
                )
                messages = [
                    {"role": "user", "content": (
                        f"Space: {space.get('name')} ({space.get('key')})\n\n"
                        f"Baseline metrics:\n{metrics_text}\n\nHierarchy:\n{hierarchy_text}\n\nFindings:\n{findings_text}"
                    )}
                ]
                resp = provider.predict(
                    messages=messages,
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

    # Now render full HTML with executive summary and inject sorted page table rows
    html = _render_html(
        space,
        metrics,
        findings,
        rovo_summary,
        hierarchy_full=hierarchy_full,
        hierarchy_scoped=hierarchy_scoped,
        scope_depth=scope_depth,
        executive_summary=executive_summary_text,
        action_plan=action_plan_text,
    )
    html = html.replace(
        "<tbody id=\"pageList\">\n        <!-- Rows injected server-side below -->\n        </tbody>",
        "<tbody id=\"pageList\">" + "".join(table_rows) + "</tbody>"
    )

    # Append LLM insights section, sorted alphabetically by page title
    if llm_insights:
        items = []
        seen_llm_pages = set()
        for it in sorted(llm_insights, key=lambda x: str(x.get('page',{}).get('title','')).casefold()):
            page = it.get("page", {})
            timing = it.get("timing_ms")
            reqs = it.get("requests")
            pid = str(page.get('id') or '')
            if pid in seen_llm_pages:
                continue
            if pid:
                seen_llm_pages.add(pid)
            meta = ""
            if timing is not None or reqs is not None:
                meta = f"<div class='small muted'>LLM requests: {reqs or 0}, total latency: {timing or 0} ms</div>"
            items.append(
                f"<details><summary><a href='{page.get('url')}' target='_blank'>{page.get('title')}</a></summary>{meta}<pre>{it.get('assessment')}</pre></details>"
            )
        llm_html = "<section><h2>LLM Insights</h2>" + "".join(items) + "</section>"
        html = html.replace("</body>", llm_html + "\n  </body>")

    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html)

    # ----------------------------------------
    # Optional: Post AI-generated comments to Confluence
    # ----------------------------------------
    if post_comments and post_target in ("both", "confluence"):
        try:
            _post_confluence_comments(
                base_url=base_url,
                auth=auth,
                space=space,
                space_key=space_key,
                exec_summary=executive_summary_text or "",
                llm_insights=llm_insights,
                post_exec_summary=post_exec_summary,
                post_page_insights=post_page_insights,
                dry_run=dry_run_post,
            )
        except Exception:
            # Swallow posting errors to avoid failing the report generation path
            pass


# ----------------------------
# Comment upsert helpers (Confluence)
# ----------------------------

AI_MARKER_PREFIX = "JIRASTATS_AI:id="


def _conf_get_space_with_home(base_url: str, auth: Tuple[str, str], space_key: str) -> Dict[str, Any]:
    return _conf_get(base_url, auth, f"/rest/api/space/{space_key}", params={"expand": "homepage"})


def _conf_list_comments(base_url: str, auth: Tuple[str, str], page_id: str, limit: int = 10000) -> List[Dict[str, Any]]:
    """List comments for a page with robust pagination beyond 100 items by default."""
    # Allow page size override via config
    per_page = 100
    try:
        from main import load_config  # optional
        cfg = load_config() or {}
        conf_cfg = (cfg.get("confluence") or {}) if isinstance(cfg, dict) else {}
        per_page = int(conf_cfg.get("comments_page_size", 100) or 100)
        limit = int(conf_cfg.get("max_comments", limit) or limit)
    except Exception:
        pass

    per_page = max(1, min(per_page, 100))

    comments: List[Dict[str, Any]] = []
    start = 0
    while True:
        data = _conf_get(base_url, auth, f"/rest/api/content/{page_id}/child/comment", params={
            "expand": "body.storage,version",
            "limit": per_page,
            "start": start,
        })
        results = data.get("results", [])
        comments.extend(results)
        size = int(data.get("size", 0) or 0)
        if size == 0:
            break
        start += size
        if start >= limit:
            break
    return comments


def _conf_create_comment(base_url: str, auth: Tuple[str, str], page_id: str, body_storage_html: str) -> Optional[str]:
    url = _confluence_base(base_url).rstrip("/") + "/rest/api/content"
    payload = {
        "type": "comment",
        "container": {"type": "page", "id": page_id},
        "body": {
            "storage": {"value": body_storage_html, "representation": "storage"}
        },
    }
    try:
        resp = requests.post(url, auth=auth, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code < 300:
            return resp.json().get("id")
    except Exception:
        return None
    return None


def _conf_update_comment(base_url: str, auth: Tuple[str, str], comment: Dict[str, Any], body_storage_html: str) -> bool:
    cid = str(comment.get("id"))
    version = ((comment.get("version") or {}).get("number") or 1) + 1
    url = _confluence_base(base_url).rstrip("/") + f"/rest/api/content/{cid}"
    payload = {
        "id": cid,
        "type": "comment",
        "version": {"number": version},
        "body": {
            "storage": {"value": body_storage_html, "representation": "storage"}
        },
    }
    try:
        resp = requests.put(url, auth=auth, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=30)
        return resp.status_code < 300
    except Exception:
        return False


def _conf_upsert_comment(base_url: str, auth: Tuple[str, str], page_id: str, marker_id: str, body_html_visible: str) -> Tuple[str, str]:
    """
    Upsert a Confluence comment identified by a stable AI marker.
    Returns (action, comment_id) where action in {"created","updated","skipped"}.
    """
    # Embed non-visible marker and a visible banner
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    visible_banner = f"<p><strong>AI Generated</strong> • <span style='color:#666'>marker {hashlib.sha256(marker_id.encode('utf-8')).hexdigest()[:8]}</span></p>"
    storage_value = f"<!-- {AI_MARKER_PREFIX}{marker_id};ts={ts} -->\n{visible_banner}\n{body_html_visible}"
    # Find existing comment by marker
    existing = None
    for c in _conf_list_comments(base_url, auth, page_id, limit=500):
        try:
            val = ((c.get("body") or {}).get("storage") or {}).get("value", "")
        except Exception:
            val = ""
        if isinstance(val, str) and AI_MARKER_PREFIX + marker_id in val:
            existing = c
            break
    if existing:
        ok = _conf_update_comment(base_url, auth, existing, storage_value)
        return ("updated" if ok else "skipped"), str(existing.get("id"))
    else:
        cid = _conf_create_comment(base_url, auth, page_id, storage_value)
        return ("created" if cid else "skipped"), str(cid or "")


def _post_confluence_comments(
    *,
    base_url: str,
    auth: Tuple[str, str],
    space: Dict[str, Any],
    space_key: str,
    exec_summary: str,
    llm_insights: List[Dict[str, Any]],
    post_exec_summary: bool,
    post_page_insights: bool,
    dry_run: bool,
) -> None:
    # Resolve homepage for space-level executive summary
    home_id: Optional[str] = None
    try:
        sp = _conf_get_space_with_home(base_url, auth, space_key)
        home_id = str(((sp or {}).get("homepage") or {}).get("id")) if sp else None
    except Exception:
        home_id = None

    if post_exec_summary and home_id and exec_summary:
        marker = f"space:{space_key}:exec"
        body = f"<h3>Executive Summary</h3><pre>{exec_summary}</pre>"
        if dry_run:
            print(f"[dry-run] Confluence upsert comment on home page {home_id} marker={marker}")
        else:
            _conf_upsert_comment(base_url, auth, home_id, marker, body)

    if post_page_insights and llm_insights:
        # Post an insight comment per page
        for it in sorted(llm_insights, key=lambda x: str(x.get('page',{}).get('title','')).casefold()):
            page = it.get("page", {})
            pid = str(page.get("id"))
            if not pid:
                continue
            marker = f"page:{pid}"
            assessment = (it.get("assessment") or "").strip()
            timing = it.get("timing_ms")
            reqs = it.get("requests")
            meta = ""
            if timing is not None or reqs is not None:
                meta = f"<div class='small muted'>LLM requests: {reqs or 0}, total latency: {timing or 0} ms</div>"
            body = f"<h3>AI Page Assessment: {page.get('title','')}</h3>{meta}<pre>{assessment}</pre>"
            if dry_run:
                print(f"[dry-run] Confluence upsert comment on page {pid} marker={marker}")
            else:
                _conf_upsert_comment(base_url, auth, pid, marker, body)
