from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import os
import re
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from file_converter import FileConverter
from llm_providers import LLMProvider, LLMQuotaError

logger = logging.getLogger(__name__)

DEFAULT_CACHE_ROOT = ".research_cache"


def normalize_query(query: str, *, max_chars: int = 512, drop_todo_fixme: bool = False) -> str:
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
    def __init__(self, root: str, namespace: Optional[str] = None):
        self.root = os.path.abspath(root)
        self.namespace = (namespace or "").strip()

    def _dir(self, kind: str) -> str:
        base = self.root
        if self.namespace:
            base = os.path.join(base, self.namespace)
        os.makedirs(base, exist_ok=True)
        target = os.path.join(base, kind)
        os.makedirs(target, exist_ok=True)
        return target

    def _path(self, kind: str, key: str) -> str:
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self._dir(kind), f"{key_hash}.json")

    def read(self, kind: str, key: str, ttl_hours: int) -> Optional[Any]:
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
        try:
            path = self._path(kind, key)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"timestamp": time.time(), "data": data}, handle, indent=2)
        except Exception:
            return


class SearchEngine:
    def search(self, query: str) -> List[Dict[str, str]]:
        raise NotImplementedError

    def verify(self) -> Tuple[bool, str]:
        return True, "Success"


class MockSearchEngine(SearchEngine):
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

    def search(self, query: str) -> List[Dict[str, str]]:
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
        os.makedirs(self.cache_root, exist_ok=True)
        cache_dir = os.path.join(self.cache_root, "search_cache")
        os.makedirs(cache_dir, exist_ok=True)
        q_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return os.path.join(cache_dir, f"google_{q_hash}.json")

    def _read_cache(self, query: str) -> Optional[List[Dict[str, str]]]:
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
        try:
            cache_path = self._cache_path(query)
            with open(cache_path, "w", encoding="utf-8") as handle:
                json.dump({"timestamp": time.time(), "results": results}, handle, indent=2)
        except Exception:
            return

    def search(self, query: str) -> List[Dict[str, str]]:
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
            if resp.status_code in (413, 414) and max_chars > 0:
                fallback_limit = max(128, min(256, max_chars))
                reduced = normalize_query(normalized_query, max_chars=fallback_limit)
                if reduced and reduced != normalized_query:
                    logger.warning(
                        f"Google Search query too large; retrying with {fallback_limit} chars."
                    )
                    params["q"] = reduced
                    normalized_query = reduced
                    resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
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
            logger.error(f"Google Search failed: {e}")
            return []

    def verify(self) -> Tuple[bool, str]:
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
            resp.raise_for_status()
            return True, "Success"
        except Exception as e:
            msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_data = e.response.json()
                    msg = error_data.get("error", {}).get("message", msg)
                except Exception:
                    pass
            return False, msg


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
    normalized = normalize_query(query, max_chars=max_chars, drop_todo_fixme=drop_todo_fixme)
    if not normalized:
        return []
    if cache:
        cached = cache.read("search", normalized, cache_ttl_hours)
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
        cache.write("search", normalized, deduped)
    return deduped


def _strip_html_text(content: str) -> str:
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


def fetch_url(
    url: str,
    *,
    timeout: int,
    session: Optional[requests.Session] = None,
    headers_list: Optional[List[Dict[str, str]]] = None,
    get_fetch_advice: Optional[Callable[[str, str], Dict[str, Any]]] = None,
) -> requests.Response:
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
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        },
    ]

    last_error = None
    session = session or requests.Session()
    for headers in headers_list:
        try:
            headers.update({
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            })
            resp = session.get(url, headers=headers, timeout=timeout)
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
                headers = advice.get("headers", headers_list[0])
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
    if not url:
        return ""
    if cache:
        cached = cache.read("fetch", url, cache_ttl_hours)
        if isinstance(cached, str):
            return cached
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
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default
