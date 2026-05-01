"""Web search and fetch utilities for topic research workflows.

Features include:
- provider-agnostic search engine interfaces,
- Google Custom Search integration with local caching,
- robust URL fetching with retry/header rotation/advice hooks, and
- content extraction helpers for text and binary documents.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import os
import re
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import requests

from refiner.file_converter import FileConverter
from refiner.llm_providers import LLMProvider, LLMQuotaError
from refiner.security_utils import url_allowed

logger = logging.getLogger(__name__)

DEFAULT_CACHE_ROOT = ".research_cache"
YOUTUBE_DEFAULT_LANGS = ("en", "en-US", "en-GB", "en-CA", "en-AU", "en-IN")
YOUTUBE_PLAYER_RESPONSE_MARKERS = (
    "var ytInitialPlayerResponse = ",
    "ytInitialPlayerResponse = ",
    "window['ytInitialPlayerResponse'] = ",
)


def normalize_query(query: str, *, max_chars: int = 512, drop_todo_fixme: bool = False) -> str:
    """Normalize a free-form search query with optional truncation/filters."""
    if not query:
        return ""
    cleaned = " ".join(str(query).split())
    lowered = cleaned.lower()
    if drop_todo_fixme:
        if lowered.startswith("todo/fixme") or lowered.startswith("todo summary"):
            return ""
        if "todo/fixme items" in lowered:
            return ""
    if max_chars > 0 and len(cleaned) > max_chars:
        return cleaned[:max_chars].rstrip()
    return cleaned


class WebResearchCache:
    """Simple JSON cache for search/fetch results keyed by hashed input."""

    def __init__(self, root: str, namespace: Optional[str] = None):
        """Create a cache namespace rooted under ``root``."""
        self.root = os.path.abspath(root)
        self.namespace = (namespace or "").strip()

    def _dir(self, kind: str) -> str:
        """Ensure and return the on-disk directory for one cache kind."""
        base = self.root
        if self.namespace:
            base = os.path.join(base, self.namespace)
        os.makedirs(base, exist_ok=True)
        target = os.path.join(base, kind)
        os.makedirs(target, exist_ok=True)
        return target

    def _path(self, kind: str, key: str) -> str:
        """Compute a stable cache path for ``kind`` and logical key."""
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self._dir(kind), f"{key_hash}.json")

    def read(self, kind: str, key: str, ttl_hours: int) -> Optional[Any]:
        """Read cached data if present and not expired."""
        try:
            path = self._path(kind, key)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            ts = float(payload.get("timestamp", 0))
            if ttl_hours > 0 and time.time() - ts > ttl_hours * 3600:
                return None
            return payload.get("data") if isinstance(payload, dict) else None
        except Exception:
            return None

    def write(self, kind: str, key: str, data: Any) -> None:
        """Write cache data with a timestamp envelope."""
        try:
            path = self._path(kind, key)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"timestamp": time.time(), "data": data}, handle, indent=2)
        except Exception:
            return


class SearchEngine:
    """Abstract search-engine interface used by ``search_web``."""

    def search(self, query: str) -> List[Dict[str, str]]:
        """Return normalized search results for a query."""
        raise NotImplementedError

    def provider_id(self) -> str:
        """Return a stable provider identifier for caching and logging."""
        return self.__class__.__name__.lower()

    def verify(self) -> Tuple[bool, str]:
        """Check provider readiness."""
        return True, "Success"


class MockSearchEngine(SearchEngine):
    """LLM-backed fallback that simulates web search snippets."""

    def __init__(
        self,
        llm: Optional[LLMProvider] = None,
        llm_params: Optional[Dict[str, Any]] = None,
        fallback_llm: Optional[LLMProvider] = None,
    ):
        self.llm = llm
        self.llm_params = llm_params or {}
        self.fallback_llm = fallback_llm
        self._quota_reached = False

    def provider_id(self) -> str:
        return "mock"

    def search(self, query: str) -> List[Dict[str, str]]:
        """Generate synthetic search results via the configured LLM."""
        if not self.llm:
            return [{"title": "No search engine", "snippet": "No LLM provided to simulate search.", "url": "#"}]

        logger.info(f"Simulating search for: {query}")
        prompt = (
            "Simulate a web search for the following query and provide 3-5 relevant snippets. "
            "Each snippet should include a title, a brief summary, and a plausible URL.\n"
            f"Query: {query}\n"
            "Format the output as a simple list of snippets in British English."
        )

        provider_to_use = self.llm
        if self._quota_reached and self.fallback_llm:
            provider_to_use = self.fallback_llm

        try:
            resp = provider_to_use.predict(
                [{"role": "user", "content": prompt}],
                system="You are a conservative, reserved British professional technical assistant providing simulated search results based on your training data. Maintain a formal, non-sycophantic tone and prioritize factual accuracy.",
                **self.llm_params,
            )
            return [{"title": f"Search result for: {query}", "snippet": resp.text, "url": "https://simulated-search.com"}]
        except LLMQuotaError as e:
            self._quota_reached = True
            if self.fallback_llm and provider_to_use != self.fallback_llm:
                logger.warning(f"Mock search primary LLM quota hit: {e}. Trying fallback.")
                try:
                    resp = self.fallback_llm.predict(
                        [{"role": "user", "content": prompt}],
                        system="You are a conservative, reserved British professional technical assistant providing simulated search results based on your training data. Maintain a formal, non-sycophantic tone and prioritize factual accuracy.",
                        **self.llm_params,
                    )
                    return [{"title": f"Search result for: {query}", "snippet": resp.text, "url": "https://simulated-search.com"}]
                except Exception as e2:
                    logger.error(f"Mock search fallback also failed: {e2}")
            logger.error(f"Search simulation failed due to quota: {e}")
            return [{"title": "Search Error", "snippet": f"Quota exceeded: {str(e)}", "url": "#"}]
        except Exception as e:
            logger.error(f"Search simulation failed: {e}")
            return [{"title": "Search Error", "snippet": f"Failed to simulate search: {str(e)}", "url": "#"}]


class GoogleSearchEngine(SearchEngine):
    """Google Custom Search API integration with response caching."""

    def __init__(
        self,
        api_key: str,
        cse_id: str,
        timeout: Optional[int] = None,
        cache_ttl_hours: int = 24,
        cache_root: Optional[str] = None,
    ):
        self.api_key = api_key
        self.cse_id = cse_id
        self.timeout = timeout or 10
        self.cache_ttl_hours = cache_ttl_hours
        self.cache_root = cache_root or DEFAULT_CACHE_ROOT

    def _cache_path(self, query: str) -> str:
        """Return the file path for cached query results."""
        os.makedirs(self.cache_root, exist_ok=True)
        cache_dir = os.path.join(self.cache_root, "search_cache")
        os.makedirs(cache_dir, exist_ok=True)
        q_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return os.path.join(cache_dir, f"google_{q_hash}.json")

    def _read_cache(self, query: str) -> Optional[List[Dict[str, str]]]:
        """Load cached results when still within TTL."""
        try:
            cache_path = self._cache_path(query)
            if not os.path.exists(cache_path):
                return None
            with open(cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            timestamp = float(payload.get("timestamp", 0))
            if time.time() - timestamp > self.cache_ttl_hours * 3600:
                return None
            results = payload.get("results")
            if isinstance(results, list):
                return results
        except Exception:
            return None
        return None

    def _write_cache(self, query: str, results: List[Dict[str, str]]) -> None:
        """Persist query results to disk cache."""
        try:
            cache_path = self._cache_path(query)
            with open(cache_path, "w", encoding="utf-8") as handle:
                json.dump({"timestamp": time.time(), "results": results}, handle, indent=2)
        except Exception:
            return

    @staticmethod
    def _format_error(resp: requests.Response) -> str:
        """Extract a human-readable error message from an HTTP response."""
        message = ""
        try:
            data = resp.json()
            if isinstance(data, dict):
                error = data.get("error") or {}
                if isinstance(error, dict):
                    message = str(error.get("message") or "").strip()
                    if not message:
                        errors = error.get("errors") or []
                        if errors and isinstance(errors, list):
                            message = str(errors[0].get("message") or "").strip()
        except Exception:
            message = ""
        if not message:
            text = (resp.text or "").strip()
            if text:
                message = text[:512]
        if not message:
            message = resp.reason or "Unknown error"
        return message

    def search(self, query: str) -> List[Dict[str, str]]:
        """Execute a Google Custom Search request and normalize results."""
        if not self.api_key or not self.cse_id:
            logger.warning("Google Search credentials missing. Falling back.")
            return []

        max_chars = _env_int("GOOGLE_SEARCH_MAX_QUERY_CHARS", _env_int("WEB_SEARCH_MAX_QUERY_CHARS", 512))
        normalized_query = normalize_query(query, max_chars=max_chars)
        if not normalized_query:
            return []
        if normalized_query != query:
            logger.info(f"Truncated Google Search query to {len(normalized_query)} chars (limit={max_chars}).")

        cached = self._read_cache(normalized_query)
        if cached is not None:
            logger.info(f"Retrieved cached Google Search results for: {normalized_query}")
            return cached

        logger.info(f"Performing Google Search for: {normalized_query}")
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self.api_key,
            "cx": self.cse_id,
            "q": normalized_query,
            "num": 5,
        }
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            try:
                status_code = int(getattr(resp, "status_code", 0) or 0)
            except Exception:
                status_code = 0
            if status_code in (413, 414) and max_chars > 0:
                fallback_limit = max(128, min(256, max_chars))
                reduced = normalize_query(normalized_query, max_chars=fallback_limit)
                if reduced and reduced != normalized_query:
                    logger.warning(
                        f"Google Search query too large; retrying with {fallback_limit} chars."
                    )
                    params["q"] = reduced
                    normalized_query = reduced
                    resp = requests.get(url, params=params, timeout=self.timeout)
                    try:
                        status_code = int(getattr(resp, "status_code", 0) or 0)
                    except Exception:
                        status_code = 0
            if status_code >= 400:
                message = self._format_error(resp)
                logger.error(f"Google Search failed: HTTP {status_code} {message}")
                return []
            data = resp.json()
            results = []
            for item in data.get("items", []):
                results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "url": item.get("link", "")
                })
            if results:
                self._write_cache(normalized_query, results)
            return results
        except Exception as e:
            message = e.__class__.__name__
            if hasattr(e, "response") and e.response is not None:
                message = self._format_error(e.response)
            logger.error(f"Google Search failed: {message}")
            return []

    def verify(self) -> Tuple[bool, str]:
        """Probe Google Search credentials/connectivity with a lightweight query."""
        if not self.api_key or not self.cse_id:
            return False, "Google Search credentials (API key or CSE ID) are missing."

        logger.info("Verifying Google Search connection...")
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self.api_key,
            "cx": self.cse_id,
            "q": "health_check",
            "num": 1,
        }
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {self._format_error(resp)}"
            return True, "Success"
        except Exception as e:
            message = e.__class__.__name__
            if hasattr(e, "response") and e.response is not None:
                message = self._format_error(e.response)
            return False, message

    def provider_id(self) -> str:
        return f"google:{self.cse_id or 'default'}"


def _extract_duckduckgo_url(raw_url: str) -> str:
    cleaned = str(raw_url or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    parsed = urlparse(cleaned)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        encoded = parse_qs(parsed.query).get("uddg") or []
        if encoded:
            return unquote(encoded[0])
    return cleaned


def _parse_duckduckgo_results(html_text: str) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    if not html_text:
        return results
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        pattern = re.compile(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'(?:<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|'
            r'<div[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>)',
            re.IGNORECASE | re.DOTALL,
        )
        for href, title, snippet_a, snippet_b in pattern.findall(html_text):
            results.append(
                {
                    "title": html_lib.unescape(re.sub(r"<[^>]+>", " ", title)).strip(),
                    "snippet": html_lib.unescape(re.sub(r"<[^>]+>", " ", snippet_a or snippet_b)).strip(),
                    "url": _extract_duckduckgo_url(href),
                }
            )
        return [item for item in results if item.get("url")]
    soup = BeautifulSoup(html_text, "html.parser")
    for result in soup.select(".result"):
        title_link = result.select_one(".result__a")
        if not title_link:
            continue
        snippet_node = result.select_one(".result__snippet")
        results.append(
            {
                "title": title_link.get_text(" ", strip=True),
                "snippet": snippet_node.get_text(" ", strip=True) if snippet_node else "",
                "url": _extract_duckduckgo_url(title_link.get("href") or ""),
            }
        )
    return [item for item in results if item.get("url")]


class DuckDuckGoSearchEngine(SearchEngine):
    """HTML DuckDuckGo search integration without API credentials."""

    def __init__(self, *, timeout: Optional[int] = None, max_results: int = 5):
        self.timeout = timeout or 10
        self.max_results = max(1, min(int(max_results or 5), 20))
        self.endpoint = "https://html.duckduckgo.com/html/"

    def provider_id(self) -> str:
        return "duckduckgo"

    def search(self, query: str) -> List[Dict[str, str]]:
        normalized_query = normalize_query(query, max_chars=_env_int("WEB_SEARCH_MAX_QUERY_CHARS", 512))
        if not normalized_query:
            return []
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; RefinerResearchBot/1.0; +https://duckduckgo.com/)",
        }
        try:
            resp = requests.post(
                self.endpoint,
                data={"q": normalized_query},
                headers=headers,
                timeout=self.timeout,
            )
            if int(getattr(resp, "status_code", 0) or 0) >= 400:
                logger.error("DuckDuckGo search failed: HTTP %s", getattr(resp, "status_code", "0"))
                return []
            return _parse_duckduckgo_results(getattr(resp, "text", ""))[: self.max_results]
        except Exception as exc:
            logger.error("DuckDuckGo search failed: %s", exc)
            return []


class BraveSearchEngine(SearchEngine):
    """Brave Search API integration."""

    def __init__(self, api_key: str, *, timeout: Optional[int] = None, max_results: int = 5):
        self.api_key = api_key
        self.timeout = timeout or 10
        self.max_results = max(1, min(int(max_results or 5), 20))
        self.endpoint = "https://api.search.brave.com/res/v1/web/search"

    def provider_id(self) -> str:
        return "brave"

    def verify(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "Brave Search API key is missing."
        return True, "Success"

    def search(self, query: str) -> List[Dict[str, str]]:
        if not self.api_key:
            return []
        normalized_query = normalize_query(query, max_chars=_env_int("WEB_SEARCH_MAX_QUERY_CHARS", 512))
        if not normalized_query:
            return []
        try:
            resp = requests.get(
                self.endpoint,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                params={"q": normalized_query, "count": self.max_results},
                timeout=self.timeout,
            )
            if int(getattr(resp, "status_code", 0) or 0) >= 400:
                logger.error("Brave search failed: HTTP %s", getattr(resp, "status_code", "0"))
                return []
            data = resp.json()
            results = []
            web_results = data.get("web", {}).get("results", []) if isinstance(data, dict) else []
            for item in web_results:
                if not isinstance(item, dict):
                    continue
                results.append(
                    {
                        "title": str(item.get("title") or ""),
                        "snippet": str(item.get("description") or item.get("snippet") or ""),
                        "url": str(item.get("url") or ""),
                    }
                )
            return [item for item in results if item.get("url")]
        except Exception as exc:
            logger.error("Brave search failed: %s", exc)
            return []


class TavilySearchEngine(SearchEngine):
    """Tavily Search API integration."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: Optional[int] = None,
        max_results: int = 5,
        search_depth: str = "basic",
    ):
        self.api_key = api_key
        self.timeout = timeout or 10
        self.max_results = max(1, min(int(max_results or 5), 20))
        self.search_depth = str(search_depth or "basic").strip() or "basic"
        self.endpoint = "https://api.tavily.com/search"

    def provider_id(self) -> str:
        return "tavily"

    def verify(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "Tavily API key is missing."
        return True, "Success"

    def search(self, query: str) -> List[Dict[str, str]]:
        if not self.api_key:
            return []
        normalized_query = normalize_query(query, max_chars=_env_int("WEB_SEARCH_MAX_QUERY_CHARS", 512))
        if not normalized_query:
            return []
        try:
            resp = requests.post(
                self.endpoint,
                json={
                    "api_key": self.api_key,
                    "query": normalized_query,
                    "max_results": self.max_results,
                    "search_depth": self.search_depth,
                    "include_answer": False,
                    "include_raw_content": False,
                },
                timeout=self.timeout,
            )
            if int(getattr(resp, "status_code", 0) or 0) >= 400:
                logger.error("Tavily search failed: HTTP %s", getattr(resp, "status_code", "0"))
                return []
            data = resp.json()
            results = []
            for item in data.get("results", []) if isinstance(data, dict) else []:
                if not isinstance(item, dict):
                    continue
                results.append(
                    {
                        "title": str(item.get("title") or item.get("url") or ""),
                        "snippet": str(item.get("content") or item.get("snippet") or ""),
                        "url": str(item.get("url") or ""),
                    }
                )
            return [item for item in results if item.get("url")]
        except Exception as exc:
            logger.error("Tavily search failed: %s", exc)
            return []


def build_search_engine(
    config: Dict[str, Any],
    *,
    llm: Optional[LLMProvider] = None,
    llm_params: Optional[Dict[str, Any]] = None,
    fallback_llm: Optional[LLMProvider] = None,
    timeout: Optional[int] = None,
    cache_ttl_hours: int = 24,
    cache_root: Optional[str] = None,
) -> Optional[SearchEngine]:
    """Create a search engine instance from a config dict."""
    if not isinstance(config, dict):
        return None

    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    search_type = str(config.get("type") or "google").strip().lower() or "google"
    name = str(config.get("name") or "").strip() or None
    max_results = max(1, min(_coerce_int(config.get("max_results") or config.get("num") or 5, 5), 20))
    resolved_timeout = _coerce_int(config.get("timeout") or timeout or 10, 10)

    if search_type == "google":
        api_key = str(config.get("api_key") or config.get("key") or "").strip()
        cse_id = str(config.get("cse_id") or config.get("cx") or config.get("engine_id") or "").strip()
        if not api_key or not cse_id:
            try:
                from refiner.credentials import get_search_credentials

                env_key, env_cse = get_search_credentials(name)
            except Exception:
                env_key, env_cse = "", ""
            api_key = api_key or str(env_key or "").strip()
            cse_id = cse_id or str(env_cse or "").strip()
        if not api_key or not cse_id:
            return None
        return GoogleSearchEngine(
            api_key,
            cse_id,
            timeout=resolved_timeout,
            cache_ttl_hours=cache_ttl_hours,
            cache_root=cache_root,
        )

    if search_type in {"duckduckgo", "ddg"}:
        return DuckDuckGoSearchEngine(timeout=resolved_timeout, max_results=max_results)

    if search_type == "brave":
        api_key = str(config.get("api_key") or config.get("key") or "").strip()
        if not api_key:
            try:
                from refiner.credentials import get_search_api_key

                api_key = str(get_search_api_key("brave", name) or "").strip()
            except Exception:
                api_key = ""
        if not api_key:
            return None
        return BraveSearchEngine(api_key, timeout=resolved_timeout, max_results=max_results)

    if search_type == "tavily":
        api_key = str(config.get("api_key") or config.get("key") or "").strip()
        if not api_key:
            try:
                from refiner.credentials import get_search_api_key

                api_key = str(get_search_api_key("tavily", name) or "").strip()
            except Exception:
                api_key = ""
        if not api_key:
            return None
        return TavilySearchEngine(
            api_key,
            timeout=resolved_timeout,
            max_results=max_results,
            search_depth=str(config.get("search_depth") or "basic").strip() or "basic",
        )

    if search_type == "mock":
        return MockSearchEngine(llm, llm_params=llm_params, fallback_llm=fallback_llm)

    return None


def search_web(
    engines: List[SearchEngine],
    query: str,
    *,
    max_results: int,
    cache: Optional[WebResearchCache] = None,
    cache_ttl_hours: int = 0,
    max_chars: int = 512,
    drop_todo_fixme: bool = False,
) -> List[Dict[str, str]]:
    """Run multiple search engines, normalize, deduplicate, and cache results."""
    normalized = normalize_query(query, max_chars=max_chars, drop_todo_fixme=drop_todo_fixme)
    if not normalized:
        return []
    cache_key = json.dumps(
        {
            "query": normalized,
            "providers": [engine.provider_id() for engine in engines],
        },
        sort_keys=True,
    )
    if cache:
        cached = cache.read("search", cache_key, cache_ttl_hours)
        if isinstance(cached, list):
            return cached
    results: List[Dict[str, str]] = []
    for engine in engines:
        try:
            batch = engine.search(normalized)
        except Exception:
            batch = []
        if isinstance(batch, list):
            for item in batch:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url or url == "#":
                    continue
                results.append(
                    {
                        "title": str(item.get("title") or ""),
                        "snippet": str(item.get("snippet") or ""),
                        "url": url,
                    }
                )
    deduped: List[Dict[str, str]] = []
    seen = set()
    for item in results:
        url = item.get("url")
        if not url:
            continue
        norm = url.strip().lower()
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(item)
        if max_results and len(deduped) >= max_results:
            break
    if cache and deduped:
        cache.write("search", cache_key, deduped)
    return deduped


def _strip_html_text(content: str) -> str:
    """Extract visible text from HTML with BeautifulSoup fallback."""
    if not content:
        return ""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        text = re.sub(r"<[^>]+>", " ", content)
        return html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return html_lib.unescape(re.sub(r"\s+", " ", text)).strip()


def extract_youtube_video_id(url: str) -> str:
    """Extract a canonical 11-character YouTube video id from supported URL forms."""
    cleaned = str(url or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")
    candidate = ""

    if host in {"youtu.be", "www.youtu.be"}:
        candidate = path.split("/", 1)[0]
    elif host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }:
        if path == "watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        elif path.startswith(("embed/", "shorts/", "live/")):
            candidate = path.split("/", 1)[1].split("/", 1)[0]

    candidate = (candidate or "").strip()
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else ""


def is_youtube_url(url: str) -> bool:
    """Return True when the supplied URL targets a supported YouTube video path."""
    return bool(extract_youtube_video_id(url))


def _youtube_watch_url(video_id: str) -> str:
    """Return the canonical watch URL for a YouTube video id."""
    return f"https://www.youtube.com/watch?v={video_id}"


def _youtube_primary_lang(lang: str) -> str:
    """Normalize a caption language code to its primary language subtag."""
    return str(lang or "").strip().lower().split("-", 1)[0]


def _youtube_dedup_langs(languages: Optional[List[str]] = None) -> List[str]:
    """Deduplicate explicit and default language preferences while preserving order."""
    ordered: List[str] = []
    for candidate in list(languages or []) + list(YOUTUBE_DEFAULT_LANGS):
        lang = str(candidate or "").strip()
        if lang and lang not in ordered:
            ordered.append(lang)
    return ordered


def _youtube_track_name(track: Dict[str, Any]) -> str:
    """Extract a readable caption track name from a YouTube caption track object."""
    name = track.get("name")
    if isinstance(name, dict):
        simple_text = str(name.get("simpleText") or "").strip()
        if simple_text:
            return simple_text
        runs = name.get("runs")
        if isinstance(runs, list):
            pieces = [str(run.get("text") or "") for run in runs if isinstance(run, dict)]
            joined = "".join(pieces).strip()
            if joined:
                return joined
    return str(track.get("trackName") or "").strip()


def _youtube_set_query_param(url: str, key: str, value: str) -> str:
    """Replace or append one query parameter on a URL."""
    parts = urlsplit(str(url or "").strip())
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    if value != "":
        pairs.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _extract_json_object_after_marker(body: str, marker: str) -> Optional[Dict[str, Any]]:
    """Extract and parse one JSON object embedded after a fixed marker string."""
    if not body or not marker:
        return None
    marker_index = body.find(marker)
    if marker_index < 0:
        return None
    start = body.find("{", marker_index + len(marker))
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(body)):
        char = body[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(body[start : index + 1])
                except Exception:
                    return None
                return payload if isinstance(payload, dict) else None
    return None


def _extract_youtube_player_response(body: str) -> Dict[str, Any]:
    """Parse the embedded ytInitialPlayerResponse object from a watch page."""
    for marker in YOUTUBE_PLAYER_RESPONSE_MARKERS:
        payload = _extract_json_object_after_marker(body, marker)
        if payload:
            return payload
    return {}


def _fetch_youtube_oembed_metadata(
    url: str,
    video_id: str,
    *,
    timeout: int,
    session: requests.Session,
) -> Dict[str, Any]:
    """Fetch lightweight YouTube video metadata via oEmbed when available."""
    endpoint = "https://www.youtube.com/oembed"
    if not url_allowed(endpoint):
        return {}
    try:
        response = session.get(
            endpoint,
            params={"url": _youtube_watch_url(video_id), "format": "json"},
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://www.youtube.com/",
            },
            timeout=timeout,
        )
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            return {}
        return {
            "title": str(data.get("title") or "").strip(),
            "channel_name": str(data.get("author_name") or "").strip(),
            "channel_url": str(data.get("author_url") or "").strip(),
            "thumbnail_url": str(data.get("thumbnail_url") or "").strip(),
        }
    except Exception:
        return {}


def _fetch_youtube_watch_context(
    video_id: str,
    *,
    timeout: int,
    session: requests.Session,
) -> Dict[str, Any]:
    """Fetch watch-page context so caption tracks and fallback metadata are available."""
    watch_url = _youtube_watch_url(video_id)
    if not url_allowed(watch_url):
        return {}
    try:
        response = session.get(
            watch_url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                "Accept-Language": "en-GB,en;q=0.9,en-US;q=0.8",
                "Referer": "https://www.youtube.com/",
            },
            timeout=timeout,
        )
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            return {}
    except Exception:
        return {}

    player_response = _extract_youtube_player_response(getattr(response, "text", "") or "")
    if not player_response:
        return {}

    tracklist = (
        player_response.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks")
        or []
    )
    tracks = [track for track in tracklist if isinstance(track, dict)]
    video_details = player_response.get("videoDetails") if isinstance(player_response.get("videoDetails"), dict) else {}
    microformat = (
        player_response.get("microformat", {})
        .get("playerMicroformatRenderer", {})
        if isinstance(player_response.get("microformat"), dict)
        else {}
    )
    thumbnails = []
    if isinstance(video_details.get("thumbnail"), dict):
        thumbnails = video_details.get("thumbnail", {}).get("thumbnails") or []
    thumbnail_url = ""
    if isinstance(thumbnails, list) and thumbnails:
        last = thumbnails[-1]
        if isinstance(last, dict):
            thumbnail_url = str(last.get("url") or "").strip()
    owner_profile_url = str(microformat.get("ownerProfileUrl") or "").strip()
    if owner_profile_url.startswith("/"):
        owner_profile_url = f"https://www.youtube.com{owner_profile_url}"
    return {
        "tracks": tracks,
        "title": str(video_details.get("title") or "").strip(),
        "channel_name": str(video_details.get("author") or "").strip(),
        "channel_id": str(video_details.get("channelId") or "").strip(),
        "channel_url": owner_profile_url,
        "thumbnail_url": thumbnail_url,
    }


def parse_youtube_json3_transcript(payload: Dict[str, Any]) -> str:
    """Flatten a YouTube JSON3 transcript payload into plain text."""
    if not isinstance(payload, dict):
        return ""
    lines: List[str] = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        fragments = []
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            fragment = str(seg.get("utf8") or "")
            if fragment:
                fragments.append(fragment)
        line = html_lib.unescape("".join(fragments)).replace("\n", " ")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def parse_youtube_xml_transcript(payload_text: str) -> str:
    """Flatten a YouTube XML transcript payload into plain text."""
    raw = str(payload_text or "").strip()
    if not raw:
        return ""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return ""
    lines: List[str] = []
    for node in root.findall(".//text"):
        fragments = [fragment for fragment in node.itertext()]
        line = html_lib.unescape("".join(fragments)).replace("\n", " ")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def _parse_youtube_transcript_response(response: requests.Response) -> str:
    """Parse either JSON3 or XML caption responses into plain text."""
    body = getattr(response, "text", "") or ""
    if not body and getattr(response, "content", None):
        try:
            body = response.content.decode(getattr(response, "encoding", None) or "utf-8", errors="ignore")
        except Exception:
            body = ""
    stripped = body.strip()
    if not stripped:
        return ""
    payload = None
    if stripped.startswith("{"):
        try:
            payload = response.json()
        except Exception:
            try:
                payload = json.loads(body)
            except Exception:
                payload = None
    if isinstance(payload, dict):
        transcript = parse_youtube_json3_transcript(payload)
        if transcript:
            return transcript
    return parse_youtube_xml_transcript(body)


def _build_youtube_caption_attempts(tracks: List[Dict[str, Any]], preferred_langs: List[str]) -> List[Dict[str, Any]]:
    """Order caption fetch attempts from strongest fit to broadest fallback."""
    attempts: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    preferred_exact = {lang.lower() for lang in preferred_langs if lang}
    preferred_primary = {_youtube_primary_lang(lang) for lang in preferred_langs if lang}

    def add_attempt(track: Dict[str, Any], *, translated_to: str = "") -> None:
        base_url = html_lib.unescape(str(track.get("baseUrl") or "").strip())
        if not base_url:
            return
        seen_key = (base_url, translated_to)
        if seen_key in seen:
            return
        seen.add(seen_key)
        caption_name = _youtube_track_name(track)
        source_lang = str(track.get("languageCode") or "").strip()
        transcript_lang = translated_to or source_lang
        candidate_urls = []
        for candidate in (_youtube_set_query_param(base_url, "fmt", "json3"), base_url):
            final_url = _youtube_set_query_param(candidate, "tlang", translated_to) if translated_to else candidate
            if final_url not in candidate_urls:
                candidate_urls.append(final_url)
        attempts.append(
            {
                "urls": candidate_urls,
                "caption_lang": transcript_lang,
                "caption_kind": "asr" if str(track.get("kind") or "").strip().lower() == "asr" else "standard",
                "caption_name": caption_name,
                "caption_source_language": source_lang,
                "caption_translated_to": translated_to,
            }
        )

    def ordered(predicate: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        return [track for track in tracks if predicate(track)]

    def is_manual(track: Dict[str, Any]) -> bool:
        return str(track.get("kind") or "").strip().lower() != "asr"

    def is_asr(track: Dict[str, Any]) -> bool:
        return not is_manual(track)

    def lang_exact(track: Dict[str, Any]) -> bool:
        return str(track.get("languageCode") or "").strip().lower() in preferred_exact

    def lang_primary(track: Dict[str, Any]) -> bool:
        return _youtube_primary_lang(track.get("languageCode") or "") in preferred_primary

    def is_english(track: Dict[str, Any]) -> bool:
        return _youtube_primary_lang(track.get("languageCode") or "") == "en"

    for track in ordered(lambda track: is_manual(track) and lang_exact(track)):
        add_attempt(track)
    for track in ordered(lambda track: is_asr(track) and lang_exact(track)):
        add_attempt(track)
    for track in ordered(lambda track: is_manual(track) and lang_primary(track)):
        add_attempt(track)
    for track in ordered(lambda track: is_asr(track) and lang_primary(track)):
        add_attempt(track)
    for track in ordered(lambda track: is_manual(track) and is_english(track)):
        add_attempt(track)
    for track in ordered(lambda track: is_asr(track) and is_english(track)):
        add_attempt(track)
    for track in ordered(
        lambda track: bool(track.get("isTranslatable")) and _youtube_primary_lang(track.get("languageCode") or "") != "en"
    ):
        add_attempt(track, translated_to="en")
    for track in ordered(is_manual):
        add_attempt(track)
    for track in ordered(is_asr):
        add_attempt(track)
    return attempts


def _build_direct_youtube_attempts(video_id: str, preferred_langs: List[str]) -> List[Dict[str, Any]]:
    """Fallback caption requests when watch-page caption tracks are unavailable."""
    attempts: List[Dict[str, Any]] = []
    for lang in preferred_langs:
        for kind in ("standard", "asr"):
            params = {"v": video_id, "lang": lang}
            if kind == "asr":
                params["kind"] = "asr"
            direct_url = "https://www.youtube.com/api/timedtext?" + urlencode(params)
            attempts.append(
                {
                    "urls": [_youtube_set_query_param(direct_url, "fmt", "json3"), direct_url],
                    "caption_lang": lang,
                    "caption_kind": kind,
                    "caption_name": "",
                    "caption_source_language": lang,
                    "caption_translated_to": "",
                }
            )
    return attempts


def fetch_youtube_transcript(
    url: str,
    *,
    timeout: int,
    session: Optional[requests.Session] = None,
    languages: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Fetch a plain-text YouTube transcript from the timedtext JSON3 endpoint."""
    video_id = extract_youtube_video_id(url)
    if not video_id:
        raise requests.exceptions.RequestException("URL is not a supported YouTube video link.")
    if not url_allowed("https://www.youtube.com/api/timedtext"):
        raise requests.exceptions.RequestException("YouTube transcript endpoint blocked by policy")

    preferred_langs = _youtube_dedup_langs(languages)
    session = session or requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.youtube.com/",
    }
    watch_context = _fetch_youtube_watch_context(video_id, timeout=timeout, session=session)
    oembed_metadata = _fetch_youtube_oembed_metadata(url, video_id, timeout=timeout, session=session)
    available_languages = []
    for track in watch_context.get("tracks") or []:
        language_code = str(track.get("languageCode") or "").strip()
        if language_code and language_code not in available_languages:
            available_languages.append(language_code)

    base_metadata = {
        "source_type": "youtube_transcript",
        "source_url": url,
        "video_id": video_id,
        "video_url": _youtube_watch_url(video_id),
        "title": str(oembed_metadata.get("title") or watch_context.get("title") or "").strip(),
        "channel_name": str(oembed_metadata.get("channel_name") or watch_context.get("channel_name") or "").strip(),
        "channel_url": str(oembed_metadata.get("channel_url") or watch_context.get("channel_url") or "").strip(),
        "thumbnail_url": str(oembed_metadata.get("thumbnail_url") or watch_context.get("thumbnail_url") or "").strip(),
    }
    if available_languages:
        base_metadata["available_caption_languages"] = available_languages

    attempts = _build_youtube_caption_attempts(watch_context.get("tracks") or [], preferred_langs)
    attempts.extend(_build_direct_youtube_attempts(video_id, preferred_langs))

    last_error = "Transcript unavailable."
    for attempt in attempts:
        for transcript_url in attempt.get("urls") or []:
            try:
                resp = session.get(transcript_url, headers=headers, timeout=timeout)
            except Exception as exc:
                last_error = str(exc)
                continue
            status_code = int(getattr(resp, "status_code", 0) or 0)
            if status_code >= 400:
                last_error = f"HTTP {status_code}"
                continue
            transcript = _parse_youtube_transcript_response(resp)
            if transcript:
                metadata = dict(base_metadata)
                metadata.update(
                    {
                        "caption_lang": str(attempt.get("caption_lang") or ""),
                        "caption_kind": str(attempt.get("caption_kind") or "standard"),
                        "caption_name": str(attempt.get("caption_name") or "").strip(),
                        "caption_source_language": str(attempt.get("caption_source_language") or ""),
                        "caption_translated_to": str(attempt.get("caption_translated_to") or ""),
                    }
                )
                return transcript, metadata
            last_error = "Transcript payload did not contain caption text."
    raise requests.exceptions.RequestException(last_error)


def fetch_url(
    url: str,
    *,
    timeout: int,
    session: Optional[requests.Session] = None,
    headers_list: Optional[List[Dict[str, str]]] = None,
    get_fetch_advice: Optional[Callable[[str, str], Dict[str, Any]]] = None,
) -> requests.Response:
    """Fetch URL content using rotating browser headers and optional advice."""
    if not url_allowed(url):
        raise requests.exceptions.RequestException("URL blocked by policy")
    headers_list = headers_list or [
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-GB,en;q=0.9,en-US;q=0.8",
            "Referer": "https://www.google.com/",
            "DNT": "1",
        },
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.5",
            "Referer": "https://www.bing.com/",
        },
    ]

    last_error = None
    session = session or requests.Session()
    for headers in headers_list:
        attempt_headers = dict(headers or {})
        try:
            attempt_headers.update({
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            })
            resp = session.get(url, headers=attempt_headers, timeout=timeout)
            if resp.status_code < 400:
                return resp
            last_error = f"HTTP {resp.status_code}: {resp.reason}"
            if resp.status_code not in (403, 405):
                break
        except Exception as e:
            last_error = str(e)
            continue

    if last_error and ("403" in last_error or "405" in last_error) and get_fetch_advice:
        logger.info(f"Standard fetch failed for {url} ({last_error}). Asking for fetch advice.")
        advice = get_fetch_advice(url, last_error)
        if advice:
            try:
                headers = dict(advice.get("headers") or headers_list[0])
                cookies = advice.get("cookies", {})
                params = advice.get("params", {})
                reasoning = advice.get("reasoning", "No reasoning provided.")
                logger.info(f"Retrying {url} with suggested strategy. Reasoning: {reasoning}")
                resp = session.get(url, headers=headers, cookies=cookies, params=params, timeout=timeout)
                if resp.status_code < 400:
                    logger.info(f"Successfully fetched {url} using fetch advice.")
                    return resp
                last_error = f"HTTP {resp.status_code}: {resp.reason} (after fetch advice)"
            except Exception as e:
                last_error = f"{str(e)} (after fetch advice)"

    if "resp" in locals() and resp is not None:
        resp.raise_for_status()
    raise requests.exceptions.RequestException(last_error)


def fetch_url_content(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    cache: Optional[WebResearchCache] = None,
    cache_ttl_hours: int = 0,
    file_converter: Optional[FileConverter] = None,
    get_fetch_advice: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    raise_on_error: bool = False,
) -> str:
    """Fetch URL and return cleaned textual content (including binary conversion)."""
    if not url:
        return ""
    if cache:
        cached = cache.read("fetch", url, cache_ttl_hours)
        if isinstance(cached, str):
            return cached
    if is_youtube_url(url):
        try:
            transcript, _ = fetch_youtube_transcript(url, timeout=timeout)
        except Exception:
            if raise_on_error:
                raise
            return ""
        cleaned = transcript.strip()
        if cache and cleaned:
            cache.write("fetch", url, cleaned)
        return cleaned
    try:
        resp = fetch_url(url, timeout=timeout, get_fetch_advice=get_fetch_advice)
    except Exception:
        if raise_on_error:
            raise
        return ""
    content_type = (resp.headers.get("content-type") or "").lower()
    raw = resp.content or b""
    truncated = False
    if max_bytes and len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True

    is_likely_text = any(t in content_type for t in ["text/", "json", "javascript", "xml"])
    is_binary_ext = any(ext in url.lower() for ext in [".pdf", ".docx", ".odf", ".odt", ".jpg", ".png", ".mp3", ".mp4"])
    if is_likely_text and not is_binary_ext:
        try:
            text = raw.decode(resp.encoding or "utf-8", errors="ignore")
        except Exception:
            text = raw.decode("utf-8", errors="ignore")
    else:
        if truncated:
            if raise_on_error:
                raise requests.exceptions.RequestException("Truncated binary response.")
            return ""
        if not file_converter:
            if raise_on_error:
                raise requests.exceptions.RequestException("No converter available for binary response.")
            return ""
        suffix = os.path.splitext(urlparse(url).path)[1]
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("wb", delete=False, suffix=suffix) as handle:
                handle.write(raw)
                tmp_path = handle.name
            text = file_converter.convert(tmp_path, mime_type=content_type)
        except Exception:
            if raise_on_error:
                raise
            text = ""
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    if not text:
        return ""
    if "html" in content_type or "<html" in text.lower():
        text = _strip_html_text(text)
    cleaned = text.strip()
    if cache and cleaned:
        cache.write("fetch", url, cleaned)
    return cleaned


def summarize_web_research(
    provider: Any,
    *,
    query: str,
    documents: List[Dict[str, str]],
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
) -> str:
    """Ask an LLM to summarize collected web findings with citations."""
    if not documents:
        return ""
    lines = [f"Query: {query}", "Sources:"]
    for doc in documents:
        title = str(doc.get("title") or "")
        url = str(doc.get("url") or "")
        snippet = str(doc.get("snippet") or "")
        content = str(doc.get("content") or "")
        block = f"- {title}\n  URL: {url}\n  Snippet: {snippet}"
        if content:
            block += f"\n  Extract:\n  {content[:1200]}"
        lines.append(block)
    user_prompt = "\n".join(lines)
    system_prompt = (
        "You are a technical researcher. Summarise the web findings with actionable guidance "
        "to help resolve errors or clarify requirements. Be concise, avoid speculation, and "
        "cite URLs in the summary."
    )
    resp = provider.predict(
        [{"role": "user", "content": user_prompt}],
        system=system_prompt,
        max_tokens=llm_max_tokens or 400,
        temperature=min(0.2, llm_temperature),
        timeout=llm_timeout,
        reasoning_effort=llm_reasoning_effort,
    )
    return (resp.text or "").strip()


def heuristic_relevance_check(content: str, topic: str, requirements: str) -> bool:
    """Cheap keyword overlap check to filter irrelevant fetched content."""
    if not content or not topic:
        return False

    def get_keywords(text: str) -> set[str]:
        text = re.sub(r"[^\w\s]", " ", text.lower())
        words = set(text.split())
        stop_words = {
            "a", "an", "the", "and", "or", "but", "if", "then", "else", "is", "are",
            "to", "for", "of", "in", "on", "with",
        }
        return {w for w in words if len(w) > 3 and w not in stop_words}

    topic_keywords = get_keywords(topic)
    req_keywords = get_keywords(requirements)
    combined_keywords = topic_keywords.union(req_keywords)

    if not combined_keywords:
        return True

    content_lower = content.lower()
    if topic.lower() in content_lower:
        return True
    matches = [kw for kw in combined_keywords if kw in content_lower]
    return len(matches) >= 2


def _env_int(name: str, default: int) -> int:
    """Read an integer env var with safe default fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default
