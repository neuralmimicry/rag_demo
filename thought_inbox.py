"""Deterministic thought-intake helpers used by Refiner's Thought Inbox.

The inbox already captures raw notes, but imported always-on assistant systems
show that capture alone is not enough. The missing layer is a lightweight,
always-available intake pass that can:

- normalize near-duplicate captures,
- infer the likely intent of a thought without needing an LLM round-trip,
- retain enough routing metadata to bridge into existing assistant/job flows,
- and support cheap local search over previously captured thoughts.

The helpers in this module stay intentionally deterministic. They provide
stable structure and routing hints even when no model provider is configured,
while still mapping cleanly onto Refiner's richer LLM-backed endpoints when
the caller does want to refine or execute a thought later.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence

from security_utils import hash_identifier


TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./]{1,}")
QUESTION_RE = re.compile(
    r"^\s*(?:what|why|how|when|where|who|which|can you|could you|would you|should we|is|are|do|does)\b",
    re.IGNORECASE,
)
IMPLEMENTATION_RE = re.compile(
    r"\b(?:build|create|implement|fix|debug|refactor|rewrite|ship|deliver|code|feature|bug|"
    r"endpoint|api|module|function|script|repo|repository|frontend|backend|python|rust|test)\b",
    re.IGNORECASE,
)
RESEARCH_RE = re.compile(
    r"\b(?:research|investigate|analyse|analyze|compare|benchmark|explore|summari[sz]e|"
    r"find out|look up|verify|evidence|references?)\b",
    re.IGNORECASE,
)
REQUIREMENTS_RE = re.compile(
    r"\b(?:requirement|requirements|acceptance criteria|spec|specification|scope|"
    r"proposal|brief|draft|plan|roadmap|design)\b",
    re.IGNORECASE,
)
TASK_RE = re.compile(
    r"\b(?:todo|to do|remember|follow up|follow-up|need to|needs to|should|must|action item)\b",
    re.IGNORECASE,
)
IDEA_RE = re.compile(
    r"\b(?:idea|prototype|concept|mvp|dashboard|app|tool|workflow|assistant)\b",
    re.IGNORECASE,
)
HIGH_PRIORITY_RE = re.compile(
    r"\b(?:urgent|asap|critical|blocker|blocking|immediately|today|now|priority|hotfix|sev[0-2])\b",
    re.IGNORECASE,
)
MEDIUM_PRIORITY_RE = re.compile(
    r"\b(?:soon|next|follow up|follow-up|plan|review|draft|research|investigate|implement|fix)\b",
    re.IGNORECASE,
)
FILE_HINT_RE = re.compile(r"(?:^|[\s(])[\w./\\-]+\.(?:py|rs|js|ts|tsx|jsx|md|json|toml|ya?ml|html|css)\b")

# Keep the stopword set deliberately small. The goal is to discard filler while
# preserving short domain terms that materially affect routing and dedupe.
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "should",
    "that",
    "the",
    "this",
    "to",
    "we",
    "what",
    "when",
    "where",
    "why",
    "with",
    "you",
    "your",
}

PRIORITY_WEIGHTS = {"high": 3, "medium": 2, "low": 1}
KIND_WEIGHTS = {
    "implementation": 6,
    "research": 5,
    "requirements": 4,
    "question": 3,
    "task": 2,
    "idea": 1,
    "note": 0,
}

LINK_BUCKETS = {
    "session_id": "sessions",
    "session_ids": "sessions",
    "room_id": "rooms",
    "room_ids": "rooms",
    "job_id": "jobs",
    "job_ids": "jobs",
    "project_id": "projects",
    "project_ids": "projects",
    "team_id": "teams",
    "team_ids": "teams",
}


def normalize_text(text: str) -> str:
    """Collapse control characters and whitespace into a stable single-line form."""
    cleaned = re.sub(r"[\r\n\t]+", " ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _tokenize(text: str) -> List[str]:
    """Split text into lowercase terms while preserving useful path-like tokens."""
    tokens: List[str] = []
    for match in TOKEN_RE.findall(text or ""):
        token = match.lower().strip("._-/")
        if len(token) < 2:
            continue
        tokens.append(token)
        # Add path/compound subterms so search can match both the whole token and
        # the meaningful parts inside it.
        for part in re.split(r"[_./\\-]", token):
            part = part.strip()
            if len(part) >= 3:
                tokens.append(part)
    return tokens


def extract_keywords(text: str, max_terms: int = 6) -> List[str]:
    """Return the most useful non-filler terms for search and display."""
    keywords: List[str] = []
    seen = set()
    for token in _tokenize(text):
        if token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= max_terms:
            break
    return keywords


def build_fingerprint(text: str) -> str:
    """Create a stable fingerprint that collapses trivial phrasing differences.

    The fingerprint is based on normalized keyword content first, with a
    normalized-text fallback when the thought is too short to yield useful
    keywords. Using the hashed canonical form avoids exposing raw text when the
    fingerprint is later logged or indexed.
    """

    normalized = normalize_text(text).lower()
    keywords = extract_keywords(normalized, max_terms=12)
    canonical = " ".join(keywords) if keywords else normalized
    # The truncated hash is still long enough to avoid accidental collisions for
    # per-user inbox usage while keeping payloads compact and readable.
    return hash_identifier(canonical)[:24] or uuid.uuid4().hex[:24]


def infer_kind(text: str, *, source: Optional[str] = None) -> str:
    """Classify the likely intent of the captured thought."""
    normalized = normalize_text(text)
    lowered = normalized.lower()
    source_value = (source or "").strip().lower()
    if not lowered:
        return "note"
    if RESEARCH_RE.search(lowered):
        return "research"
    if IMPLEMENTATION_RE.search(lowered) or FILE_HINT_RE.search(normalized):
        return "implementation"
    if REQUIREMENTS_RE.search(lowered):
        return "requirements"
    if QUESTION_RE.search(lowered) or lowered.endswith("?"):
        return "question"
    if TASK_RE.search(lowered):
        return "task"
    if IDEA_RE.search(lowered):
        return "idea"
    if source_value in {"voice", "siri", "alexa", "google"}:
        return "task"
    return "note"


def infer_priority(text: str, *, kind: Optional[str] = None, defer_until_idle: bool = True) -> str:
    """Estimate priority from urgency language and inferred intent."""
    normalized = normalize_text(text)
    if HIGH_PRIORITY_RE.search(normalized):
        return "high"
    resolved_kind = kind or infer_kind(normalized)
    if resolved_kind in {"implementation", "research", "requirements"}:
        return "medium"
    if MEDIUM_PRIORITY_RE.search(normalized):
        return "medium"
    if not defer_until_idle and resolved_kind in {"task", "question"}:
        return "medium"
    return "low"


def _merge_unique(existing: Optional[Sequence[str]], incoming: Iterable[str], *, limit: int = 8) -> List[str]:
    """Merge ordered string sequences without duplication."""
    values: List[str] = []
    seen = set()
    for raw in list(existing or []) + [item for item in incoming]:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def _select_primary_text(existing_text: str, incoming_text: str) -> str:
    """Keep the more informative version of a duplicated thought.

    Prefer the variant with more keyword coverage first, then fall back to the
    longer normalized text. This retains extra context from a repeated voice or
    manual capture without letting duplicate noise overwrite a stronger earlier
    capture.
    """

    current = normalize_text(existing_text)
    candidate = normalize_text(incoming_text)
    if not current:
        return candidate
    if not candidate:
        return current
    current_keywords = extract_keywords(current, max_terms=12)
    candidate_keywords = extract_keywords(candidate, max_terms=12)
    if len(candidate_keywords) > len(current_keywords):
        return candidate
    if len(candidate_keywords) == len(current_keywords) and len(candidate) > len(current):
        return candidate
    return current


def _merge_links(existing: Optional[Dict[str, Any]], incoming: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Combine session/job/project linkage metadata into stable buckets."""
    merged: Dict[str, List[str]] = {}
    for payload in (existing or {}, incoming or {}):
        if not isinstance(payload, dict):
            continue
        # Stored items already use bucketed link names (`projects`, `jobs`, ...),
        # while incoming API payloads still use raw keys (`project_id`, `job_id`,
        # ...). Accept both forms so duplicate collapse never discards context.
        for bucket in set(LINK_BUCKETS.values()):
            value = payload.get(bucket)
            values = value if isinstance(value, list) else [value]
            cleaned = [str(item).strip() for item in values if str(item or "").strip()]
            if not cleaned:
                continue
            merged[bucket] = _merge_unique(merged.get(bucket), cleaned, limit=12)
        for key, bucket in LINK_BUCKETS.items():
            value = payload.get(key)
            values = value if isinstance(value, list) else [value]
            cleaned = [str(item).strip() for item in values if str(item or "").strip()]
            if not cleaned:
                continue
            merged[bucket] = _merge_unique(merged.get(bucket), cleaned, limit=12)
    return merged


def build_thought_item(
    text: str,
    *,
    now_iso: str,
    source: Optional[str] = None,
    device: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    defer_until_idle: bool = True,
) -> Dict[str, Any]:
    """Create a normalized inbox item from a raw capture."""
    cleaned_text = normalize_text(text)
    kind = infer_kind(cleaned_text, source=source)
    keywords = extract_keywords(cleaned_text)
    priority = infer_priority(cleaned_text, kind=kind, defer_until_idle=defer_until_idle)
    source_value = str(source or "").strip() or None
    device_value = str(device or "").strip() or None
    links = _merge_links({}, meta if isinstance(meta, dict) else {})
    item: Dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "text": cleaned_text,
        "status": "todo",
        "source": source_value,
        "device": device_value,
        "source_history": [source_value] if source_value else [],
        "device_history": [device_value] if device_value else [],
        "defer_until_idle": bool(defer_until_idle),
        "created_at": now_iso,
        "updated_at": now_iso,
        "first_captured_at": now_iso,
        "last_captured_at": now_iso,
        "occurrences": 1,
        "fingerprint": build_fingerprint(cleaned_text),
        "kind": kind,
        "priority": priority,
        "keywords": keywords,
        # The execution state is intentionally separate from the user-facing
        # status so the inbox can support internal claims/background handling
        # without forcing the UI into more states than it needs to display.
        "execution_state": "ready",
    }
    if links:
        item["links"] = links
    if isinstance(meta, dict) and meta:
        item["meta"] = dict(meta)
    return item


def merge_duplicate_capture(
    existing: Dict[str, Any],
    *,
    text: str,
    now_iso: str,
    source: Optional[str] = None,
    device: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    defer_until_idle: bool = True,
) -> Dict[str, Any]:
    """Update an existing item with another capture of the same thought."""
    merged = dict(existing or {})
    merged_text = _select_primary_text(str(existing.get("text") or ""), text)
    merged["text"] = merged_text
    merged["updated_at"] = now_iso
    merged["last_captured_at"] = now_iso
    merged["occurrences"] = max(1, int(existing.get("occurrences") or 1)) + 1
    merged["defer_until_idle"] = bool(existing.get("defer_until_idle")) or bool(defer_until_idle)

    source_value = str(source or "").strip() or None
    device_value = str(device or "").strip() or None
    if source_value:
        merged["source"] = source_value
    if device_value:
        merged["device"] = device_value
    merged["source_history"] = _merge_unique(existing.get("source_history"), [source_value] if source_value else [])
    merged["device_history"] = _merge_unique(existing.get("device_history"), [device_value] if device_value else [])

    if isinstance(meta, dict) and meta:
        combined_meta = dict(existing.get("meta") or {})
        combined_meta.update(meta)
        merged["meta"] = combined_meta
    elif "meta" in existing:
        merged["meta"] = dict(existing.get("meta") or {})

    links = _merge_links(existing.get("links"), meta if isinstance(meta, dict) else {})
    if links:
        merged["links"] = links

    merged_kind = infer_kind(merged_text, source=source_value or existing.get("source"))
    if KIND_WEIGHTS.get(merged_kind, 0) < KIND_WEIGHTS.get(str(existing.get("kind") or ""), 0):
        merged_kind = str(existing.get("kind") or merged_kind)
    merged["kind"] = merged_kind

    merged_priority = infer_priority(
        merged_text,
        kind=merged_kind,
        defer_until_idle=bool(merged.get("defer_until_idle")),
    )
    if PRIORITY_WEIGHTS.get(merged_priority, 0) < PRIORITY_WEIGHTS.get(str(existing.get("priority") or ""), 0):
        merged_priority = str(existing.get("priority") or merged_priority)
    merged["priority"] = merged_priority
    merged["keywords"] = _merge_unique(existing.get("keywords"), extract_keywords(merged_text), limit=10)
    merged["fingerprint"] = build_fingerprint(merged_text)

    # Duplicated captures should immediately become eligible again if an earlier
    # claim went stale or the item had not yet been processed.
    if str(existing.get("status") or "todo").lower() == "todo":
        merged["execution_state"] = "ready"
        merged.pop("claim_expires_at", None)
        merged.pop("claimed_at", None)
    return merged


def score_query_match(item: Dict[str, Any], query: str) -> float:
    """Return a simple weighted match score for local inbox search."""
    query_text = normalize_text(query).lower()
    if not query_text:
        return 1.0
    query_terms = [term for term in extract_keywords(query_text, max_terms=12)]
    if not query_terms:
        return 0.0
    haystack = " ".join(
        [
            normalize_text(str(item.get("text") or "")).lower(),
            " ".join(str(term) for term in item.get("keywords") or []),
            " ".join(str(term) for term in item.get("tags") or []),
            str(item.get("kind") or ""),
            str(item.get("priority") or ""),
            str(item.get("source") or ""),
        ]
    )
    score = 0.0
    if query_text in haystack:
        score += 4.0
    for term in query_terms:
        if term in haystack:
            score += 1.0
    return score


def _truncate_summary(text: str, limit: int = 72) -> str:
    cleaned = normalize_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _first_link(item: Dict[str, Any], bucket: str) -> Optional[str]:
    """Return the first normalized linked identifier from a bucketed link map."""
    links = item.get("links")
    if not isinstance(links, dict):
        return None
    values = links.get(bucket)
    if isinstance(values, list):
        for raw in values:
            value = str(raw or "").strip()
            if value:
                return value
        return None
    value = str(values or "").strip()
    return value or None


def build_route_suggestion(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map a captured thought onto the most relevant existing Refiner flow."""
    text = normalize_text(str(item.get("text") or ""))
    kind = str(item.get("kind") or infer_kind(text, source=item.get("source"))).strip().lower() or "note"
    priority = str(item.get("priority") or infer_priority(text, kind=kind)).strip().lower() or "low"
    summary = _truncate_summary(text, limit=84)
    linked_project_id = _first_link(item, "projects")

    def with_context(payload: Dict[str, Any]) -> Dict[str, Any]:
        # Carry linked project context forward so staging logic can preserve the
        # operator's original workspace instead of silently dropping it.
        if linked_project_id:
            payload["project_id"] = linked_project_id
        return payload

    if kind == "implementation":
        return {
            "target": "job",
            "label": "Queue Implementation Job",
            "workflow": "project_solver",
            "endpoint": "/api/jobs",
            "reason": "The thought reads like code or repository work, so the project-solver workflow is the closest fit.",
            "summary": summary,
            "priority": priority,
            "payload": with_context({
                "workflow": "project_solver",
                "project_name": summary or "Inbox Thought",
                "requirements_text": text,
                "include_global_requirements": True,
            }),
        }

    if kind == "research":
        return {
            "target": "job",
            "label": "Queue Research Job",
            "workflow": "topic_research",
            "endpoint": "/api/jobs",
            "reason": "The thought asks for investigation or comparison, which maps cleanly onto the topic research workflow.",
            "summary": summary,
            "priority": priority,
            "payload": with_context({
                "workflow": "topic_research",
                "topic_source": text,
            }),
        }

    if kind in {"requirements", "task"}:
        return {
            "target": "assistant",
            "label": "Draft Requirements",
            "workflow": "assistant_requirements",
            "endpoint": "/api/assistant/requirements",
            "reason": "The thought already looks like scope or requirements material, so requirements drafting is the best first refinement step.",
            "summary": summary,
            "priority": priority,
            "payload": with_context({
                "mode": "draft",
                "requirements_text": text,
            }),
        }

    if kind == "idea":
        return {
            "target": "assistant",
            "label": "Generate Mini Plan",
            "workflow": "playground_plan",
            "endpoint": "/api/playground/plan",
            "reason": "The thought looks like an idea or prototype prompt, so the playground planner can turn it into a small executable plan.",
            "summary": summary,
            "priority": priority,
            "payload": with_context({
                "prompt": text,
            }),
        }

    return {
        "target": "assistant",
        "label": "Refine In Assistant",
        "workflow": "assistant_requirements",
        "endpoint": "/api/assistant/requirements",
        "reason": "The safest default is to route the thought through the assistant for clarification before turning it into a job.",
        "summary": summary,
        "priority": priority,
        "payload": with_context({
            "mode": "ask",
            "prompt": text,
        }),
    }
