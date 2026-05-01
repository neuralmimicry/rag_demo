from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import threading
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from refiner.rag_engine import RagDocument, RagIndex
from refiner.security_utils import ensure_dir_permissions

_ALLOWED_EXTENSIONS = {
    ".html",
    ".htm",
    ".md",
    ".txt",
    ".xml",
    ".json",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
}

_CODE_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".json"}
_SKIP_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".cache",
    "coverage",
    "__pycache__",
    ".venv",
    "venv",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"\bhttps?://[^\s<>()]+", re.IGNORECASE)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")
_LONG_NUMBER_RE = re.compile(r"\b\d{5,}\b")
_NAME_PHRASE_RE = re.compile(
    r"\b(my name is|i am|i'm|this is|call me)\s+[A-Za-z][A-Za-z .'-]{0,40}",
    re.IGNORECASE,
)

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "your",
    "you",
    "our",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "will",
    "would",
    "shall",
    "should",
    "can",
    "could",
    "into",
    "about",
    "over",
    "under",
    "after",
    "before",
    "there",
    "their",
    "they",
    "them",
    "than",
    "then",
    "when",
    "where",
    "what",
    "which",
    "while",
    "each",
    "more",
    "most",
    "other",
    "some",
    "many",
    "such",
    "only",
    "also",
    "been",
    "being",
    "not",
    "use",
    "used",
    "using",
    "into",
    "onto",
    "new",
    "now",
    "all",
    "any",
    "its",
    "get",
    "set",
    "out",
    "off",
    "per",
    "via",
    "app",
    "api",
    "www",
    "com",
    "org",
    "net",
    "class",
    "const",
    "function",
    "return",
    "import",
    "export",
    "true",
    "false",
    "null",
    "undefined",
    "default",
    "props",
    "state",
    "button",
    "card",
    "input",
    "value",
    "title",
    "description",
    "message",
}

_DISPLAY_OVERRIDES = {
    "neuralmimicry": "NeuralMimicry",
    "aarnn": "AARNN",
    "refiner": "Refiner",
    "continuum": "Continuum",
    "tracey": "Tracey",
    "neuromorphic": "neuromorphic",
    "sovereign": "sovereign",
}


class SttLearningStore:
    """Privacy-safe learning memory for STT and voice assistant contexts.

    - Seeds public/domain terms and context from website text.
    - Learns only redacted snippets from conversations over time.
    - Provides reusable prompt hints and queryable context blocks.
    """

    def __init__(
        self,
        root: str,
        *,
        seed_paths: Optional[List[str]] = None,
        seed_urls: Optional[List[str]] = None,
        allow_network: bool = False,
        request_timeout: float = 8.0,
        max_seed_files: int = 220,
        max_seed_docs: int = 220,
        max_seed_doc_bytes: int = 160_000,
        max_memory_docs: int = 350,
        max_terms: int = 4000,
        prompt_terms: int = 40,
        learn_min_count: int = 3,
        chunk_size: int = 900,
        chunk_overlap: int = 120,
        max_chunks: int = 2400,
    ):
        self.root = root
        self.state_path = os.path.join(root, "state.json")
        self.seed_index_path = os.path.join(root, "seed_index.json")
        self.lock = threading.RLock()

        self.seed_paths = [p.strip() for p in (seed_paths or []) if str(p).strip()]
        self.seed_urls = [u.strip() for u in (seed_urls or []) if str(u).strip()]
        self.allow_network = bool(allow_network)
        self.request_timeout = max(2.0, float(request_timeout))

        self.max_seed_files = max(1, int(max_seed_files))
        self.max_seed_docs = max(1, int(max_seed_docs))
        self.max_seed_doc_bytes = max(1024, int(max_seed_doc_bytes))
        self.max_memory_docs = max(50, int(max_memory_docs))
        self.max_terms = max(500, int(max_terms))
        self.prompt_terms = max(8, int(prompt_terms))
        self.learn_min_count = max(1, int(learn_min_count))

        self.chunk_size = max(250, int(chunk_size))
        self.chunk_overlap = max(0, min(int(chunk_overlap), self.chunk_size - 1))
        self.max_chunks = max(200, int(max_chunks))

        self.seed_terms: Dict[str, int] = {}
        self.learned_terms: Dict[str, int] = {}
        self.memory_docs: List[Dict[str, Any]] = []
        self.seed_index: Optional[RagIndex] = None
        self.memory_index: Optional[RagIndex] = None

        ensure_dir_permissions(self.root, mode=0o700)

        self._load_state()
        self._load_seed_index()
        self._rebuild_memory_index_locked()

        # First boot: build initial KB from local website files (and optional online crawl).
        if not self.seed_terms or not self.seed_index:
            self.refresh_seed(force=True)

    def _default_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "seeded_at": None,
            "updated_at": None,
            "seed_sources": {},
            "seed_terms": {},
            "learned_terms": {},
            "memory_docs": [],
        }

    def _now_iso(self) -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat()

    def _write_json_atomic(self, path: str, payload: Dict[str, Any]) -> None:
        tmp = f"{path}.tmp"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

    def _load_state(self) -> None:
        state = self._default_state()
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    state.update(loaded)
        except Exception:
            pass

        seed_terms = state.get("seed_terms") if isinstance(state.get("seed_terms"), dict) else {}
        learned_terms = state.get("learned_terms") if isinstance(state.get("learned_terms"), dict) else {}
        memory_docs = state.get("memory_docs") if isinstance(state.get("memory_docs"), list) else []

        self.seed_terms = {str(k): int(v) for k, v in seed_terms.items() if str(k).strip() and int(v) > 0}
        self.learned_terms = {str(k): int(v) for k, v in learned_terms.items() if str(k).strip() and int(v) > 0}
        self.memory_docs = [d for d in memory_docs if isinstance(d, dict) and isinstance(d.get("text"), str)]

    def _save_state_locked(self, seed_sources: Optional[Dict[str, int]] = None) -> None:
        payload = self._default_state()
        payload["seeded_at"] = payload.get("seeded_at")
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as handle:
                    prev = json.load(handle)
                if isinstance(prev, dict):
                    payload["seeded_at"] = prev.get("seeded_at")
                    payload["seed_sources"] = prev.get("seed_sources") if isinstance(prev.get("seed_sources"), dict) else {}
        except Exception:
            pass

        if seed_sources is not None:
            payload["seed_sources"] = seed_sources
        payload["seed_terms"] = self._trim_counts(self.seed_terms, self.max_terms)
        payload["learned_terms"] = self._trim_counts(self.learned_terms, self.max_terms)
        payload["memory_docs"] = list(self.memory_docs[-self.max_memory_docs :])
        payload["updated_at"] = self._now_iso()
        self._write_json_atomic(self.state_path, payload)

    def _load_seed_index(self) -> None:
        if not os.path.exists(self.seed_index_path):
            self.seed_index = None
            return
        try:
            with open(self.seed_index_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                self.seed_index = RagIndex.from_dict(payload)
        except Exception:
            self.seed_index = None

    def _save_seed_index_locked(self) -> None:
        if not self.seed_index:
            return
        self._write_json_atomic(self.seed_index_path, self.seed_index.to_dict())

    def _strip_markup(self, text: str) -> str:
        cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = html.unescape(cleaned)
        return self._normalise_ws(cleaned)

    def _normalise_ws(self, text: str, max_chars: int = 20_000) -> str:
        text = text.replace("\x00", " ").replace("\r", " ").replace("\t", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
        return text

    def _looks_human_string(self, value: str) -> bool:
        text = self._normalise_ws(value, max_chars=320)
        if len(text) < 3:
            return False
        if not re.search(r"[A-Za-z]", text):
            return False
        lower = text.lower()
        noisy_tokens = ("classname", "import ", "export ", "function(", "=>", "window.", "document.")
        if any(token in lower for token in noisy_tokens):
            return False
        symbol_count = sum(1 for ch in text if not ch.isalnum() and ch not in {" ", "-", "'", ",", ".", ":", "/"})
        if symbol_count > max(6, len(text) // 4):
            return False
        return True

    def _extract_from_code(self, text: str) -> str:
        snippets: List[str] = []
        for match in re.finditer(r"(?s)(['\"`])((?:\\.|(?!\1).){1,260})\1", text):
            candidate = match.group(2)
            candidate = candidate.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
            candidate = html.unescape(candidate)
            if self._looks_human_string(candidate):
                snippets.append(self._normalise_ws(candidate, max_chars=280))

        for match in re.finditer(r">\s*([^<>{}]{3,260})\s*<", text):
            candidate = html.unescape(match.group(1))
            if self._looks_human_string(candidate):
                snippets.append(self._normalise_ws(candidate, max_chars=280))

        seen = set()
        unique: List[str] = []
        for item in snippets:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return self._normalise_ws("\n".join(unique), max_chars=20_000)

    def _extract_file_text(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext not in _ALLOWED_EXTENSIONS:
            return ""
        try:
            if os.path.getsize(path) > self.max_seed_doc_bytes:
                return ""
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                raw = handle.read(self.max_seed_doc_bytes + 1)
        except Exception:
            return ""
        if not raw:
            return ""

        if ext in _CODE_EXTENSIONS:
            return self._extract_from_code(raw)
        if ext in {".html", ".htm", ".xml"}:
            return self._strip_markup(raw)
        if ext == ".md":
            stripped = re.sub(r"```.*?```", " ", raw, flags=re.DOTALL)
            stripped = re.sub(r"`[^`]+`", " ", stripped)
            stripped = re.sub(r"^#{1,6}\s*", "", stripped, flags=re.MULTILINE)
            return self._normalise_ws(stripped)
        return self._normalise_ws(raw)

    def _collect_local_docs(self) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        seen_paths = set()
        files_seen = 0

        for seed_path in self.seed_paths:
            if not seed_path or not os.path.exists(seed_path):
                continue
            if os.path.isfile(seed_path):
                candidates = [seed_path]
            else:
                candidates = []
                candidate_count = 0
                for root, dirs, files in os.walk(seed_path):
                    dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
                    for name in files:
                        if candidate_count >= self.max_seed_files:
                            break
                        ext = os.path.splitext(name)[1].lower()
                        if ext not in _ALLOWED_EXTENSIONS:
                            continue
                        path = os.path.join(root, name)
                        candidates.append(path)
                        candidate_count += 1
                    if candidate_count >= self.max_seed_files:
                        break

            for path in candidates:
                if files_seen >= self.max_seed_files or len(docs) >= self.max_seed_docs:
                    break
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                text = self._extract_file_text(path)
                files_seen += 1
                if len(text) < 32:
                    continue
                rel_source = path
                docs.append({"source": rel_source, "text": text, "type": "local"})

        return docs[: self.max_seed_docs]

    def _extract_links_from_sitemap(self, xml_text: str) -> List[str]:
        links = []
        for match in re.finditer(r"<loc>(.*?)</loc>", xml_text, flags=re.IGNORECASE | re.DOTALL):
            link = html.unescape(match.group(1)).strip()
            if link:
                links.append(link)
        return links

    def _fetch_url_text(self, url: str) -> str:
        if not requests:
            return ""
        try:
            resp = requests.get(url, timeout=self.request_timeout)
        except Exception:
            return ""
        if resp.status_code >= 400:
            return ""
        content_type = (resp.headers.get("Content-Type") or "").lower()
        body = resp.text or ""
        if "xml" in content_type or url.lower().endswith(".xml"):
            return self._normalise_ws(body)
        return self._strip_markup(body)

    def _collect_online_docs(self) -> List[Dict[str, Any]]:
        if not self.allow_network or not requests:
            return []

        docs: List[Dict[str, Any]] = []
        visited = set()
        queue: List[str] = []
        max_online_docs = min(40, self.max_seed_docs)

        for base in self.seed_urls:
            if not base:
                continue
            queue.append(base)
            if not base.lower().endswith(".xml"):
                queue.append(base.rstrip("/") + "/sitemap.xml")

        while queue and len(docs) < max_online_docs:
            url = queue.pop(0)
            if not url or url in visited:
                continue
            visited.add(url)
            raw_text = self._fetch_url_text(url)
            if not raw_text:
                continue

            if url.lower().endswith(".xml") or "<loc>" in raw_text:
                for link in self._extract_links_from_sitemap(raw_text):
                    if link not in visited and len(queue) < max_online_docs * 2:
                        queue.append(link)
                continue

            if len(raw_text) >= 32:
                docs.append({"source": url, "text": raw_text, "type": "online"})

        return docs[:max_online_docs]

    def _redact_sensitive(self, text: str) -> str:
        redacted = text or ""
        redacted = _EMAIL_RE.sub("[email]", redacted)
        redacted = _URL_RE.sub("[url]", redacted)
        redacted = _IP_RE.sub("[ip]", redacted)
        redacted = _PHONE_RE.sub("[phone]", redacted)
        redacted = _LONG_NUMBER_RE.sub("[number]", redacted)
        redacted = _NAME_PHRASE_RE.sub(lambda m: f"{m.group(1)} [name]", redacted)
        sensitive_words = (
            "passport",
            "social security",
            "ssn",
            "date of birth",
            "bank account",
            "credit card",
            "debit card",
            "national insurance",
            "driving licence",
            "home address",
        )
        for word in sensitive_words:
            redacted = re.sub(re.escape(word), "[redacted-sensitive]", redacted, flags=re.IGNORECASE)
        return self._normalise_ws(redacted, max_chars=6000)

    def _extract_terms(self, text: str) -> Dict[str, int]:
        redacted = self._redact_sensitive(text)
        counts: Dict[str, int] = {}
        for match in _WORD_RE.finditer(redacted):
            term = match.group(0).strip("-").lower()
            if not term or term in _STOPWORDS:
                continue
            if len(term) < 3 or len(term) > 32:
                continue
            if term.startswith(("http", "www")):
                continue
            if term.isdigit() or re.search(r"\d{3,}", term):
                continue
            counts[term] = counts.get(term, 0) + 1
        return counts

    def _trim_counts(self, counts: Dict[str, int], limit: int) -> Dict[str, int]:
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        trimmed = items[: max(1, limit)]
        return {k: int(v) for k, v in trimmed if int(v) > 0}

    def _build_index(self, name: str, docs: List[Dict[str, Any]], max_chunks: Optional[int] = None) -> Optional[RagIndex]:
        rag_docs: List[RagDocument] = []
        for idx, doc in enumerate(docs, start=1):
            text = str(doc.get("text") or "").strip()
            if len(text) < 24:
                continue
            rag_docs.append(
                RagDocument(
                    doc_id=f"{name}-{idx:04d}",
                    source=str(doc.get("source") or f"{name}:{idx}"),
                    text=text,
                    metadata={"type": doc.get("type") or name},
                )
            )
        if not rag_docs:
            return None
        return RagIndex.build(
            name=name,
            documents=rag_docs,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            max_chunks=max_chunks or self.max_chunks,
        )

    def _rebuild_memory_index_locked(self) -> None:
        self.memory_index = self._build_index("stt_memory", self.memory_docs, max_chunks=800)

    def refresh_seed(self, force: bool = False) -> Dict[str, Any]:
        with self.lock:
            # Keep a lightweight manual override path: no-op unless forced or no seed yet.
            if not force and self.seed_terms and self.seed_index is not None:
                return {
                    "seed_docs": 0,
                    "seed_terms": len(self.seed_terms),
                    "local_docs": 0,
                    "online_docs": 0,
                    "refreshed": False,
                }

            local_docs = self._collect_local_docs()
            online_docs = self._collect_online_docs()
            seed_docs = (local_docs + online_docs)[: self.max_seed_docs]

            seed_terms: Dict[str, int] = {}
            for doc in seed_docs:
                for term, count in self._extract_terms(doc.get("text") or "").items():
                    seed_terms[term] = seed_terms.get(term, 0) + count

            self.seed_terms = self._trim_counts(seed_terms, self.max_terms)
            self.seed_index = self._build_index("stt_seed", seed_docs)
            self._save_seed_index_locked()

            self._save_state_locked(
                seed_sources={
                    "local_docs": len(local_docs),
                    "online_docs": len(online_docs),
                    "seed_docs": len(seed_docs),
                }
            )

            # Persist seed metadata timestamp.
            try:
                with open(self.state_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:
                payload = self._default_state()
            payload["seeded_at"] = self._now_iso()
            self._write_json_atomic(self.state_path, payload)

            return {
                "seed_docs": len(seed_docs),
                "seed_terms": len(self.seed_terms),
                "local_docs": len(local_docs),
                "online_docs": len(online_docs),
                "refreshed": True,
            }

    def _clip_learning_text(self, text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        selected: List[str] = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 8:
                continue
            selected.append(sentence)
            if len(selected) >= 2:
                break
        if not selected:
            selected = [text.strip()]
        clipped = " ".join(selected)
        return self._normalise_ws(clipped, max_chars=360)

    def learn_from_text(self, text: str, *, source: str = "conversation") -> Dict[str, Any]:
        cleaned = self._redact_sensitive(text)
        if len(cleaned) < 12:
            return {"added_terms": 0, "added_doc": False}

        counts = self._extract_terms(cleaned)
        if not counts:
            return {"added_terms": 0, "added_doc": False}

        snippet = self._clip_learning_text(cleaned)
        digest = hashlib.sha1(snippet.encode("utf-8")).hexdigest()[:16]

        with self.lock:
            for term, count in counts.items():
                self.learned_terms[term] = self.learned_terms.get(term, 0) + count
            self.learned_terms = self._trim_counts(self.learned_terms, self.max_terms)

            existing_ids = {str(doc.get("id") or "") for doc in self.memory_docs}
            added_doc = False
            if digest not in existing_ids and len(snippet) >= 20:
                self.memory_docs.append(
                    {
                        "id": digest,
                        "source": source,
                        "text": snippet,
                        "created_at": self._now_iso(),
                    }
                )
                if len(self.memory_docs) > self.max_memory_docs:
                    self.memory_docs = self.memory_docs[-self.max_memory_docs :]
                added_doc = True
                self._rebuild_memory_index_locked()

            self._save_state_locked()

        return {"added_terms": len(counts), "added_doc": added_doc}

    def build_prompt_hint(self, context: str = "", *, max_terms: Optional[int] = None) -> str:
        with self.lock:
            seed_terms = dict(self.seed_terms)
            learned_terms = dict(self.learned_terms)

        scores: Dict[str, float] = {}
        for term, count in seed_terms.items():
            scores[term] = scores.get(term, 0.0) + min(float(count), 8.0)

        for term, count in learned_terms.items():
            if term in seed_terms or count >= self.learn_min_count:
                scores[term] = scores.get(term, 0.0) + float(count) * 2.0

        for term, count in self._extract_terms(context or "").items():
            if term in scores:
                scores[term] += 6.0 + float(count)
            elif count >= self.learn_min_count:
                scores[term] = 4.0 + float(count)

        limit = max_terms or self.prompt_terms
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        terms = [term for term, _ in ordered[:limit]]
        if not terms:
            return ""

        display_terms = [_DISPLAY_OVERRIDES.get(term, term) for term in terms]
        hint = "Domain vocabulary: " + ", ".join(display_terms)
        return self._normalise_ws(hint, max_chars=500)

    def query_context(self, query: str, *, limit: int = 4, min_score: float = 0.1) -> List[Dict[str, Any]]:
        query = self._normalise_ws(query or "", max_chars=800)
        if not query:
            return []

        with self.lock:
            seed_index = self.seed_index
            memory_index = self.memory_index

        rows: List[Dict[str, Any]] = []
        if seed_index:
            for match in seed_index.search(query, limit=max(2, limit * 2), min_score=min_score):
                rows.append(
                    {
                        "source": match.source,
                        "score": float(match.score),
                        "text": self._normalise_ws(match.text, max_chars=700),
                        "source_type": "seed",
                    }
                )

        if memory_index:
            for match in memory_index.search(query, limit=max(2, limit * 2), min_score=min_score):
                rows.append(
                    {
                        "source": match.source,
                        "score": float(match.score) * 1.1,
                        "text": self._normalise_ws(match.text, max_chars=500),
                        "source_type": "memory",
                    }
                )

        rows.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("source") or "")))

        seen = set()
        merged: List[Dict[str, Any]] = []
        for row in rows:
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            key = hashlib.sha1(text.lower().encode("utf-8")).hexdigest()[:16]
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= limit:
                break
        return merged

    def stats(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "seed_terms": len(self.seed_terms),
                "learned_terms": len(self.learned_terms),
                "memory_docs": len(self.memory_docs),
                "seed_index_chunks": len(self.seed_index.chunks) if self.seed_index else 0,
                "memory_index_chunks": len(self.memory_index.chunks) if self.memory_index else 0,
            }
