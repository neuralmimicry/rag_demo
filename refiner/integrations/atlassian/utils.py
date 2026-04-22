from __future__ import annotations
import datetime as dt
import json
import logging
import requests
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def normalise_confluence_base_url(base_url: str) -> str:
    """Return a Confluence REST base URL with the `/wiki` prefix applied once."""
    cleaned = str(base_url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith("/wiki"):
        return cleaned
    return cleaned + "/wiki"

def parse_atlassian_datetime(ts: Optional[str]) -> Optional[dt.datetime]:
    if not ts:
        return None
    try:
        # Atlassian timestamps are usually ISO8601; normalize to UTC
        # Handles 2024-01-01T12:00:00.000+0000 or 2024-01-01T12:00:00.000Z
        t = ts.replace("Z", "+00:00")
        if len(t) > 19 and t[19] == '.' and '+' in t[19:]:
            # Handle +0000 format which fromisoformat might struggle with if not separated by colon
            parts = t.split('+')
            if len(parts) == 2 and len(parts[1]) == 4:
                t = f"{parts[0]}+{parts[1][:2]}:{parts[1][2:]}"
        return dt.datetime.fromisoformat(t).astimezone(dt.timezone.utc)
    except Exception:
        try:
            # Fallback for simpler formats
            return dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
        except Exception:
            return None

@dataclass
class IssueInfo:
    key: str
    url: str
    summary: str
    issuetype: str
    status: str
    priority: Optional[str]
    labels: List[str]
    assignee: Optional[str]
    updated: Optional[dt.datetime]
    description: str
    reporter: Optional[str] = None
    created: Optional[dt.datetime] = None
    parent_key: Optional[str] = None
    comment_count: int = 0
    commenters: List[str] = field(default_factory=list)
    comments: List[Dict[str, str]] = field(default_factory=list)

@dataclass
class PageInfo:
    id: str
    title: str
    url: str
    last_updated: Optional[dt.datetime] = None
    author: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    depth: Optional[int] = None
    parent_id: Optional[str] = None
    ancestors: List[str] = field(default_factory=list)
    children: List[str] = field(default_factory=list)

@dataclass
class AtlassianClient:
    base_url: str
    auth: Tuple[str, str]
    timeout: int = 30
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent
        }

    def get(self, path: str, params: Optional[dict] = None) -> Dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        resp = requests.get(url, params=params or {}, headers=self.headers, auth=self.auth, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: Dict[str, Any], params: Optional[dict] = None) -> Dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        resp = requests.post(url, auth=self.auth, data=json.dumps(payload), headers=self.headers, params=params, timeout=self.timeout)
        if resp.status_code >= 400:
            logger.debug(f"POST {url} failed ({resp.status_code}): {resp.text[:500]}")
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def put(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        resp = requests.put(url, auth=self.auth, data=json.dumps(payload), headers=self.headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

class JiraClient(AtlassianClient):
    def fetch_issues(self, jql: str, limit: int = 100) -> List[IssueInfo]:
        """Fetch issues using JQL with pagination."""
        start_at = 0
        all_issues = []
        while len(all_issues) < limit:
            chunk_size = min(100, limit - len(all_issues))
            payload = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": chunk_size,
                "fields": ["summary", "issuetype", "status", "priority", "labels", "assignee", "updated", "created", "description", "reporter", "comment", "parent"]
            }
            # Try POST /search first
            try:
                data = self.post("/rest/api/2/search", payload)
            except Exception:
                # Fallback to GET /search
                data = self.get("/rest/api/2/search", params={"jql": jql, "startAt": start_at, "maxResults": chunk_size, "fields": "summary,issuetype,status,priority,labels,assignee,updated,created,description,reporter,comment,parent"})
            
            issues = data.get("issues", [])
            if not issues:
                break
            
            for it in issues:
                all_issues.append(self._map_issue(it))
            
            if len(issues) < chunk_size:
                break
            start_at += len(issues)
        
        return all_issues[:limit]

    def _map_issue(self, raw: Dict[str, Any]) -> IssueInfo:
        fields = raw.get("fields", {})
        key = raw.get("key", "")
        
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
                            # Attempt a very crude text extraction from ADT if it's a dict
                            # This is usually for the 'doc' format
                            body_text = json.dumps(body)
                        except Exception:
                            body_text = str(body)
                    else:
                        body_text = str(body)
                    
                    comments.append({"author": author_name, "body": body_text})

        return IssueInfo(
            key=key,
            url=f"{self.base_url.rstrip('/')}/browse/{key}",
            summary=fields.get("summary") or "",
            issuetype=(fields.get("issuetype") or {}).get("name") or "",
            status=(fields.get("status") or {}).get("name") or "",
            priority=(fields.get("priority") or {}).get("name"),
            labels=fields.get("labels") or [],
            assignee=(fields.get("assignee") or {}).get("displayName") if fields.get("assignee") else None,
            updated=parse_atlassian_datetime(fields.get("updated")),
            created=parse_atlassian_datetime(fields.get("created")),
            parent_key=parent_key,
            description=fields.get("description") or "",
            reporter=reporter_name,
            comment_count=comment_count,
            commenters=commenters,
            comments=comments
        )

    def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        issue_type: str = "Task",
        description: str = "",
        fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        issue_fields = dict(fields or {})
        issue_fields.setdefault("project", {"key": project_key})
        issue_fields.setdefault("summary", summary)
        issue_fields.setdefault("issuetype", {"name": issue_type})
        if description:
            issue_fields.setdefault("description", description)
        data = self.post("/rest/api/2/issue", {"fields": issue_fields})
        issue_key_value = str(data.get("key") or "").strip()
        return {
            "issue_key": issue_key_value,
            "issue_id": str(data.get("id") or "").strip(),
            "url": f"{self.base_url.rstrip('/')}/browse/{issue_key_value}" if issue_key_value else "",
        }

    def update_issue(self, issue_key: str, *, fields: Dict[str, Any]) -> Dict[str, Any]:
        self.put(f"/rest/api/2/issue/{issue_key}", {"fields": dict(fields or {})})
        return {
            "issue_key": str(issue_key or "").strip(),
            "url": f"{self.base_url.rstrip('/')}/browse/{str(issue_key or '').strip()}",
        }

    def list_transitions(self, issue_key: str) -> List[Dict[str, Any]]:
        data = self.get(f"/rest/api/2/issue/{issue_key}/transitions")
        return data.get("transitions") or []

    def transition_issue(
        self,
        issue_key: str,
        *,
        transition_id: Optional[str] = None,
        transition_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_transition_id = str(transition_id or "").strip()
        if not resolved_transition_id and transition_name:
            wanted = str(transition_name).strip().casefold()
            for transition in self.list_transitions(issue_key):
                name = str(transition.get("name") or "").strip().casefold()
                if name == wanted:
                    resolved_transition_id = str(transition.get("id") or "").strip()
                    break
        if not resolved_transition_id:
            raise ValueError("A valid Jira transition_id or transition_name is required.")
        self.post(
            f"/rest/api/2/issue/{issue_key}/transitions",
            {"transition": {"id": resolved_transition_id}},
        )
        return {
            "issue_key": str(issue_key or "").strip(),
            "transition_id": resolved_transition_id,
            "url": f"{self.base_url.rstrip('/')}/browse/{str(issue_key or '').strip()}",
        }

    def upsert_comment(self, issue_key: str, marker_id: str, body: str) -> Dict[str, Any]:
        path = f"/rest/api/2/issue/{issue_key}/comment"
        # Search for existing comment with marker
        comments = self.get(path).get("comments", [])
        existing = next((c for c in comments if marker_id in (c.get("body") or "")), None)
        
        full_body = f"{body}\n\n[marker:{marker_id}]"
        if existing:
            self.put(f"{path}/{existing['id']}", {"body": full_body})
            return {"action": "updated", "comment_id": str(existing.get("id") or "").strip()}
        else:
            created = self.post(path, {"body": full_body})
            return {"action": "created", "comment_id": str(created.get("id") or "").strip()}

class ConfluenceClient(AtlassianClient):
    def _api_base_url(self) -> str:
        return normalise_confluence_base_url(self.base_url)

    def get(self, path: str, params: Optional[dict] = None) -> Dict[str, Any]:
        url = self._api_base_url().rstrip("/") + path
        resp = requests.get(url, params=params or {}, headers=self.headers, auth=self.auth, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: Dict[str, Any], params: Optional[dict] = None) -> Dict[str, Any]:
        url = self._api_base_url().rstrip("/") + path
        resp = requests.post(
            url,
            auth=self.auth,
            data=json.dumps(payload),
            headers=self.headers,
            params=params,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            logger.debug(f"POST {url} failed ({resp.status_code}): {resp.text[:500]}")
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def put(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self._api_base_url().rstrip("/") + path
        resp = requests.put(url, auth=self.auth, data=json.dumps(payload), headers=self.headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def fetch_space_pages(self, space_key: str, limit: int = 1000) -> List[PageInfo]:
        start = 0
        all_pages = []
        while len(all_pages) < limit:
            chunk = min(100, limit - len(all_pages))
            path = "/rest/api/content"
            params = {
                "spaceKey": space_key,
                "type": "page",
                "start": start,
                "limit": chunk,
                "expand": "version,metadata.labels,space"
            }
            data = self.get(path, params=params)
            results = data.get("results", [])
            if not results:
                break
            for r in results:
                all_pages.append(self._map_page(r))
            if len(results) < chunk:
                break
            start += len(results)
        return all_pages

    def _map_page(self, raw: Dict[str, Any]) -> PageInfo:
        history = raw.get("version", {})
        # last_updated from version.when
        last_updated = parse_atlassian_datetime(history.get("when"))
        labels = [l.get("name") for l in (raw.get("metadata", {}).get("labels", {}).get("results", []))]
        
        # URL construction
        space_key = (raw.get("space") or {}).get("key")
        title_encoded = requests.utils.quote(raw.get("title", ""))
        public_base = self._api_base_url().rstrip("/")
        url = f"{public_base}/spaces/{space_key}/pages/{raw.get('id')}/{title_encoded}"

        return PageInfo(
            id=raw.get("id", ""),
            title=raw.get("title", ""),
            url=url,
            last_updated=last_updated,
            labels=labels
        )

    def get_page_text(self, page_id: str) -> str:
        data = self.get(f"/rest/api/content/{page_id}", params={"expand": "body.storage"})
        body = data.get("body", {}).get("storage", {}).get("value", "")
        # Very crude HTML to text
        import re
        text = re.sub(r'<[^>]+>', ' ', body)
        return ' '.join(text.split())

    def get_page(self, page_id: str, *, expand: str = "version,space") -> Dict[str, Any]:
        return self.get(f"/rest/api/content/{page_id}", params={"expand": expand})

    def list_comments(self, page_id: str, *, limit: int = 1000) -> List[Dict[str, Any]]:
        comments: List[Dict[str, Any]] = []
        start = 0
        page_size = min(max(1, int(limit or 100)), 100)
        while len(comments) < limit:
            data = self.get(
                f"/rest/api/content/{page_id}/child/comment",
                params={"expand": "body.storage,version", "limit": page_size, "start": start},
            )
            results = data.get("results") or []
            if not results:
                break
            comments.extend(results)
            size = int(data.get("size", 0) or 0)
            if size <= 0:
                break
            start += size
            if size < page_size:
                break
        return comments[:limit]

    def create_page(
        self,
        *,
        space_key: str,
        title: str,
        body_storage: str,
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": body_storage, "representation": "storage"}},
        }
        parent = str(parent_id or "").strip()
        if parent:
            payload["ancestors"] = [{"id": parent}]
        created = self.post("/rest/api/content", payload)
        page_id = str(created.get("id") or "").strip()
        return {
            "page_id": page_id,
            "title": str(created.get("title") or title).strip(),
            "space_key": space_key,
            "url": f"{self._api_base_url().rstrip('/')}/pages/{page_id}",
        }

    def update_page(
        self,
        page_id: str,
        *,
        title: Optional[str],
        body_storage: str,
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.get_page(page_id, expand="version,space")
        version_number = int(((current.get("version") or {}).get("number") or 1)) + 1
        resolved_title = str(title or current.get("title") or "").strip()
        if not resolved_title:
            raise ValueError("A Confluence page title is required for updates.")
        payload: Dict[str, Any] = {
            "id": str(page_id or "").strip(),
            "type": "page",
            "title": resolved_title,
            "version": {"number": version_number},
            "body": {"storage": {"value": body_storage, "representation": "storage"}},
        }
        space_key = str(((current.get("space") or {}).get("key") or "")).strip()
        if space_key:
            payload["space"] = {"key": space_key}
        parent = str(parent_id or "").strip()
        if parent:
            payload["ancestors"] = [{"id": parent}]
        self.put(f"/rest/api/content/{page_id}", payload)
        return {
            "page_id": str(page_id or "").strip(),
            "title": resolved_title,
            "space_key": space_key,
            "url": f"{self._api_base_url().rstrip('/')}/pages/{str(page_id or '').strip()}",
        }

    def upsert_comment(self, page_id: str, marker_id: str, body_storage_html: str) -> Dict[str, Any]:
        existing = None
        for comment in self.list_comments(page_id, limit=500):
            value = ((comment.get("body") or {}).get("storage") or {}).get("value", "")
            if isinstance(value, str) and marker_id and marker_id in value:
                existing = comment
                break
        if existing:
            comment_id = str(existing.get("id") or "").strip()
            version_number = int(((existing.get("version") or {}).get("number") or 1)) + 1
            self.put(
                f"/rest/api/content/{comment_id}",
                {
                    "id": comment_id,
                    "type": "comment",
                    "version": {"number": version_number},
                    "body": {"storage": {"value": body_storage_html, "representation": "storage"}},
                },
            )
            return {"action": "updated", "comment_id": comment_id}
        created = self.post(
            "/rest/api/content",
            {
                "type": "comment",
                "container": {"type": "page", "id": page_id},
                "body": {"storage": {"value": body_storage_html, "representation": "storage"}},
            },
        )
        return {"action": "created", "comment_id": str(created.get("id") or "").strip()}
