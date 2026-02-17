"""
Lightweight LLM provider adapters: OpenAI, Gemini, and Ollama.

No heavy SDK dependencies; uses requests to call HTTP endpoints.

Environment variables:
- OPENAI_API_KEY
- GEMINI_API_KEY
- OLLAMA_BASE_URL (optional, defaults to http://localhost:11434)
OpenAI timeout overrides (optional):
- OPENAI_TIMEOUT_SECONDS (default: LLM_TIMEOUT_SECONDS or 180)
- OPENAI_HIGH_REASONING_TIMEOUT (default 300)
- OPENAI_XHIGH_REASONING_TIMEOUT (default 600)
- OPENAI_CONNECT_TIMEOUT_SECONDS (default 10)
- OPENAI_READ_TIMEOUT_SECONDS (override read timeout if set)
- OPENAI_MAX_RETRIES (override LLM_MAX_RETRIES for OpenAI requests)

Robustness env defaults (optional):
- LLM_TIMEOUT_SECONDS (default 180)
- LLM_MAX_RETRIES (default 2)
- LLM_BACKOFF_BASE (seconds, default 0.5)
- LLM_BACKOFF_MAX (seconds, default 8)

Public factory:
- get_provider(name: str, model: str | None = None, base_url: str | None = None) -> LLMProvider
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Any, Optional, List, Tuple
import os
import time
import hashlib
import json
import requests
import random
import logging
import re
import threading

logger = logging.getLogger(__name__)

ALLOWED_REASONING_EFFORT = {"none", "low", "medium", "high", "xhigh"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    cleaned = str(raw).strip().lower()
    if cleaned in ("1", "true", "yes", "on"):
        return True
    if cleaned in ("0", "false", "no", "off"):
        return False
    return default


def _openai_service_tier(kind: str) -> Optional[str]:
    if kind == "responses":
        tier = os.getenv("OPENAI_RESPONSES_SERVICE_TIER") or os.getenv("OPENAI_SERVICE_TIER")
    else:
        tier = os.getenv("OPENAI_CHAT_SERVICE_TIER") or os.getenv("OPENAI_SERVICE_TIER")
    if tier:
        tier = str(tier).strip()
    return tier or None


def _effort_rank(effort: Optional[str]) -> int:
    if not effort:
        return 0
    lowered = str(effort).strip().lower()
    if lowered in ("none", ""):
        return 0
    if lowered == "low":
        return 1
    if lowered == "medium":
        return 2
    if lowered == "high":
        return 3
    if lowered == "xhigh":
        return 4
    return 0


def _total_input_chars(messages: List[Dict[str, Any]], system: Optional[str]) -> int:
    total = len(system or "")
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        total += len(str(msg.get("content", "")))
    return total


def _estimate_tokens_from_chars(chars: int) -> int:
    return max(1, int(chars / 4)) if chars else 0


def _should_use_background_auto(
    *,
    total_chars: int,
    estimated_tokens: int,
    max_tokens: Optional[int],
    reasoning_effort: Optional[str],
    service_tier: Optional[str],
    base_timeout: int,
) -> Tuple[bool, List[str]]:
    if not _env_bool("OPENAI_BACKGROUND_AUTO", True):
        return False, ["auto_disabled"]
    reasons: List[str] = []
    min_chars = _env_int("OPENAI_BACKGROUND_AUTO_MIN_INPUT_CHARS", 20000)
    min_tokens = _env_int("OPENAI_BACKGROUND_AUTO_MIN_INPUT_TOKENS", 4000)
    min_output = _env_int("OPENAI_BACKGROUND_AUTO_MIN_OUTPUT_TOKENS", 1200)
    min_chars_reasoning = _env_int("OPENAI_BACKGROUND_AUTO_MIN_CHARS_FOR_REASONING", 12000)
    min_chars_flex = _env_int("OPENAI_BACKGROUND_AUTO_MIN_CHARS_FLEX", 8000)
    min_timeout = _env_int("OPENAI_BACKGROUND_AUTO_MIN_TIMEOUT_SECONDS", 120)
    min_effort = os.getenv("OPENAI_BACKGROUND_AUTO_MIN_REASONING", "medium")
    effort_rank = _effort_rank(reasoning_effort)
    min_effort_rank = _effort_rank(min_effort)

    if max_tokens and max_tokens >= min_output:
        reasons.append(f"max_tokens>={min_output}")
    if total_chars >= min_chars:
        reasons.append(f"input_chars>={min_chars}")
    if estimated_tokens >= min_tokens:
        reasons.append(f"input_tokens>={min_tokens}")
    if effort_rank >= min_effort_rank and total_chars >= min_chars_reasoning:
        reasons.append(f"reasoning_{reasoning_effort or 'none'}+chars>={min_chars_reasoning}")
    if service_tier and service_tier.lower() == "flex" and total_chars >= min_chars_flex:
        reasons.append(f"flex_chars>={min_chars_flex}")
    if base_timeout <= min_timeout and effort_rank >= min_effort_rank:
        reasons.append(f"timeout<={min_timeout}s")

    return bool(reasons), reasons

_REQUEST_CATEGORY: ContextVar[str] = ContextVar("llm_request_category", default="llm")
_TOKEN_TOTALS: Dict[str, Dict[str, int]] = {}
_TOKEN_TOTALS_LOCK = threading.Lock()


@contextmanager
def request_category(label: Optional[str]):
    token = _REQUEST_CATEGORY.set(label or "llm")
    try:
        yield
    finally:
        _REQUEST_CATEGORY.reset(token)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _normalize_reasoning_effort(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned.lower()


def _validate_reasoning_effort(effort: Optional[str], *, model: Optional[str]) -> List[str]:
    issues: List[str] = []
    if effort is None:
        return issues
    if effort not in ALLOWED_REASONING_EFFORT:
        issues.append(f"reasoning.effort '{effort}' is not in {sorted(ALLOWED_REASONING_EFFORT)}")
    if effort == "xhigh" and model and "codex" not in model.lower():
        issues.append("reasoning.effort 'xhigh' used with non-codex model")
    return issues


def _normalize_prompt_cache_retention(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip().lower()
    if not cleaned:
        return None
    if cleaned in {"in_memory", "in-memory"}:
        return "in_memory"
    if cleaned in {"24h", "24hr", "24hrs", "24hours"}:
        return "24h"
    return None


def _build_prompt_cache_key(
    system: Optional[str],
    messages: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    kind: str = "responses",
) -> Optional[str]:
    explicit = (
        os.getenv("OPENAI_PROMPT_CACHE_KEY")
        or os.getenv("PROMPT_CACHE_KEY")
        or os.getenv("LLM_PROMPT_CACHE_KEY")
    )
    if explicit:
        return explicit
    auto = _env_bool("OPENAI_PROMPT_CACHE_KEY_AUTO", _env_bool("PROMPT_CACHE_KEY_AUTO", True))
    if not auto:
        return None
    mode = os.getenv("OPENAI_PROMPT_CACHE_KEY_MODE", "system").strip().lower()
    basis = ""
    if mode == "model":
        basis = model or ""
    elif mode == "prefix":
        basis = system or ""
        if messages:
            first = messages[0].get("content") if isinstance(messages[0], dict) else ""
            if isinstance(first, str):
                basis = f"{basis}\n{first[:512]}"
    else:
        basis = system or ""
    if not basis:
        return None
    return f"pcache:{_hash(f'{kind}:{basis}')}"


def _current_request_category() -> str:
    try:
        value = _REQUEST_CATEGORY.get()
    except LookupError:
        value = "llm"
    return value or "llm"


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _extract_openai_usage(data: Dict[str, Any]) -> Optional[Dict[str, Optional[int]]]:
    if not isinstance(data, dict):
        return None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = _coerce_int(usage.get("input_tokens"))
    if prompt is None:
        prompt = _coerce_int(usage.get("prompt_tokens"))
    completion = _coerce_int(usage.get("output_tokens"))
    if completion is None:
        completion = _coerce_int(usage.get("completion_tokens"))
    total = _coerce_int(usage.get("total_tokens"))
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    cached = None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _coerce_int(details.get("cached_tokens"))
    return {"prompt": prompt, "completion": completion, "total": total, "cached": cached}


def _extract_gemini_usage(data: Dict[str, Any]) -> Optional[Dict[str, Optional[int]]]:
    if not isinstance(data, dict):
        return None
    usage = data.get("usageMetadata") or data.get("usage_metadata")
    if not isinstance(usage, dict):
        return None
    prompt = _coerce_int(usage.get("promptTokenCount"))
    completion = _coerce_int(usage.get("candidatesTokenCount"))
    total = _coerce_int(usage.get("totalTokenCount"))
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    return {"prompt": prompt, "completion": completion, "total": total}


def _extract_ollama_usage(data: Dict[str, Any]) -> Optional[Dict[str, Optional[int]]]:
    if not isinstance(data, dict):
        return None
    prompt = _coerce_int(data.get("prompt_eval_count"))
    completion = _coerce_int(data.get("eval_count"))
    total = None
    if prompt is not None or completion is not None:
        total = (prompt or 0) + (completion or 0)
    if total is None:
        total = _coerce_int(data.get("total_tokens"))
    if prompt is None and completion is None and total is None:
        return None
    return {"prompt": prompt, "completion": completion, "total": total}


def _format_token_value(value: Optional[int]) -> str:
    return "?" if value is None else str(value)


def _log_token_usage(provider: str, model: Optional[str], usage: Optional[Dict[str, Optional[int]]]) -> None:
    if not usage:
        return
    category = _current_request_category()
    prompt = usage.get("prompt")
    completion = usage.get("completion")
    total = usage.get("total")
    cached = usage.get("cached")
    total_for_add = total
    if total_for_add is None and (prompt is not None or completion is not None):
        total_for_add = (prompt or 0) + (completion or 0)

    with _TOKEN_TOTALS_LOCK:
        totals = _TOKEN_TOTALS.setdefault(category, {"prompt": 0, "completion": 0, "total": 0})
        if prompt is not None:
            totals["prompt"] += prompt
        if completion is not None:
            totals["completion"] += completion
        if total_for_add is not None:
            totals["total"] += total_for_add
        running = dict(totals)

    cached_note = f" cached={_format_token_value(cached)}" if cached is not None else ""
    logger.info(
        "Token usage [%s] %s/%s: sent=%s received=%s used=%s%s | running total [%s]: sent=%s received=%s used=%s",
        category,
        provider,
        model or "unknown",
        _format_token_value(prompt),
        _format_token_value(completion),
        _format_token_value(total_for_add),
        cached_note,
        category,
        running.get("prompt", 0),
        running.get("completion", 0),
        running.get("total", 0),
    )


def _messages_to_responses_input(messages: List[Dict[str, Any]], system: Optional[str]) -> List[Dict[str, Any]]:
    input_items: List[Dict[str, Any]] = []
    if system:
        input_items.append({"role": "system", "content": system})
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else msg
        if role is None:
            role = "user"
        if not isinstance(content, str):
            try:
                content = json.dumps(content)
            except Exception:
                content = str(content)
        input_items.append({"role": role, "content": content})
    return input_items


def _extract_openai_response_text(data: Dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text
    output = data.get("output") or []
    chunks: List[str] = []

    def _extract_text(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            if value.strip():
                chunks.append(value)
            return
        if isinstance(value, dict):
            if isinstance(value.get("text"), str):
                if value.get("text").strip():
                    chunks.append(value.get("text"))
                return
            if isinstance(value.get("text"), dict):
                nested = value.get("text")
                if isinstance(nested.get("value"), str) and nested.get("value").strip():
                    chunks.append(nested.get("value"))
                return
            if isinstance(value.get("output_text"), str) and value.get("output_text").strip():
                chunks.append(value.get("output_text"))
                return
            if isinstance(value.get("refusal"), str) and value.get("refusal").strip():
                chunks.append(value.get("refusal"))
                return
            if isinstance(value.get("summary"), str) and value.get("summary").strip():
                chunks.append(value.get("summary"))
                return
            content = value.get("content")
            if isinstance(content, (list, dict, str)):
                _extract_text(content)
            return
        if isinstance(value, list):
            for item in value:
                _extract_text(item)
            return

    if isinstance(output, list):
        for item in output:
            _extract_text(item)
    return "\n".join([c for c in chunks if isinstance(c, str) and c.strip()])


def _summarize_openai_payload(payload: Dict[str, Any], *, kind: str) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"kind": kind}
    if not isinstance(payload, dict):
        summary["invalid_payload"] = True
        return summary
    summary["model"] = payload.get("model")
    try:
        summary["payload_bytes"] = len(json.dumps(payload))
    except Exception:
        summary["payload_bytes"] = None
    if kind == "chat":
        msgs = payload.get("messages", [])
        summary["message_count"] = len(msgs) if isinstance(msgs, list) else 0
        summary["roles"] = [m.get("role") for m in msgs if isinstance(m, dict)][:5]
        summary["total_chars"] = sum(
            len(str(m.get("content", ""))) for m in msgs if isinstance(m, dict)
        )
        summary["temperature"] = payload.get("temperature")
    else:
        inputs = payload.get("input", [])
        summary["input_count"] = len(inputs) if isinstance(inputs, list) else 0
        summary["roles"] = [m.get("role") for m in inputs if isinstance(m, dict)][:5]
        summary["total_chars"] = sum(
            len(str(m.get("content", ""))) for m in inputs if isinstance(m, dict)
        )
        summary["reasoning_effort"] = payload.get("reasoning", {}).get("effort") if isinstance(payload.get("reasoning"), dict) else None
        summary["temperature"] = payload.get("temperature")
    if "max_tokens" in payload:
        summary["max_tokens"] = payload.get("max_tokens")
    if "max_output_tokens" in payload:
        summary["max_output_tokens"] = payload.get("max_output_tokens")
    return summary


def _check_openai_payload_semantics(payload: Dict[str, Any], *, kind: str) -> List[str]:
    issues: List[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a dict"]
    model = payload.get("model")
    effort = None
    if kind == "chat":
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            issues.append("messages is empty")
        total_chars = sum(
            len(str(m.get("content", ""))) for m in messages if isinstance(m, dict)
        ) if isinstance(messages, list) else 0
        if total_chars == 0:
            issues.append("messages content is empty")
    else:
        inputs = payload.get("input")
        if not isinstance(inputs, list) or not inputs:
            issues.append("input is empty")
        total_chars = sum(
            len(str(m.get("content", ""))) for m in inputs if isinstance(m, dict)
        ) if isinstance(inputs, list) else 0
        if total_chars == 0:
            issues.append("input content is empty")
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, dict):
            effort = reasoning.get("effort")
    issues.extend(_validate_reasoning_effort(effort, model=model if isinstance(model, str) else None))
    return issues


def _validate_openai_chat_payload(payload: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a dict"]
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        issues.append("model must be a non-empty string")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        issues.append("messages must be a list")
        return issues
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            issues.append(f"messages[{idx}] is not a dict")
            continue
        if "role" not in msg or not isinstance(msg.get("role"), str):
            issues.append(f"messages[{idx}].role must be a string")
        if "content" not in msg or not isinstance(msg.get("content"), str):
            issues.append(f"messages[{idx}].content must be a string")
    return issues


def _validate_openai_responses_payload(payload: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a dict"]
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        issues.append("model must be a non-empty string")
    input_items = payload.get("input")
    if not isinstance(input_items, list):
        issues.append("input must be a list")
        return issues
    for idx, item in enumerate(input_items):
        if not isinstance(item, dict):
            issues.append(f"input[{idx}] is not a dict")
            continue
        if "role" not in item or not isinstance(item.get("role"), str):
            issues.append(f"input[{idx}].role must be a string")
        if "content" not in item or not isinstance(item.get("content"), str):
            issues.append(f"input[{idx}].content must be a string")
    reasoning = payload.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, dict):
        issues.append("reasoning must be an object when provided")
    if isinstance(reasoning, dict) and "effort" in reasoning and not isinstance(reasoning.get("effort"), str):
        issues.append("reasoning.effort must be a string")
    return issues


class LLMError(RuntimeError):
    pass


class LLMQuotaError(LLMError):
    """Raised when the LLM provider returns a 429 (Quota Exceeded) error."""
    pass


@dataclass
class LLMResponse:
    text: str
    raw: Dict[str, Any]
    latency_ms: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class LLMProvider:
    name: str
    model: str

    def __init__(self, inter_request_gap: float = 0.0):
        self.inter_request_gap = inter_request_gap
        self._last_request_time = 0.0
        self._lock = threading.Lock()

    def _wait_for_gap(self):
        """Ensures that the configured inter-request gap is respected."""
        if self.inter_request_gap <= 0:
            return

        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self.inter_request_gap:
                delay = self.inter_request_gap - elapsed
                logger.info(f"Respecting LLM inter-request gap: sleeping {delay:.2f}s")
                time.sleep(delay)
            self._last_request_time = time.time()

    def predict(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        system: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResponse:
        raise NotImplementedError

    def transcribe(self, file_path: str, timeout: Optional[int] = None) -> str:
        """Transcribe audio/video to text."""
        raise NotImplementedError

    def health_check(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Perform a lightweight availability probe for the provider.
        Returns a dict: {"ok": bool, "status_code": int|None, "latency_ms": int|None, "message": str}
        """
        raise NotImplementedError

    def estimate_tokens(self, text: str) -> int:
        # Simple heuristic: ~1 token ≈ 4 chars in English
        return max(1, int(len(text) / 4))

    def get_context_window(self) -> int:
        """Returns the context window size for the model."""
        return 4096


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_optional_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None:
        return None
    cleaned = str(raw).strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except Exception:
        return None


def _openai_timeout_tuple(base_timeout: int, service_tier: Optional[str] = None) -> tuple[int, int]:
    connect_timeout = _env_int("OPENAI_CONNECT_TIMEOUT_SECONDS", 10)
    read_override = _env_optional_int("OPENAI_READ_TIMEOUT_SECONDS")
    read_timeout = base_timeout
    if read_override is not None:
        read_timeout = max(read_timeout, read_override)
    if service_tier and service_tier.lower() == "flex":
        flex_timeout = _env_int("OPENAI_FLEX_TIMEOUT_SECONDS", 900)
        read_timeout = max(read_timeout, flex_timeout)
    return (connect_timeout, read_timeout)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _should_retry(status: Optional[int], exc: Optional[Exception]) -> bool:
    if exc is not None:
        # Network issues/timeouts
        return True
    if status is None:
        return False
    # Retry on 429 and 5xx
    return status == 429 or (500 <= status < 600)


def _retry_after_seconds(resp: Optional[requests.Response]) -> Optional[float]:
    if not resp:
        return None
    val = resp.headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except Exception:
        return None


def _http_post(
    url: str,
    *,
    headers: Dict[str, str],
    json_payload: Dict[str, Any],
    timeout: Optional[object] = None,
    session: Optional[requests.Session] = None,
    max_retries: Optional[int] = None,
) -> requests.Response:
    """
    POST with exponential backoff + jitter on transient errors.
    Honors Retry-After when present. Raises LLMError after exhausting retries.
    """
    max_retries = _env_int("LLM_MAX_RETRIES", 2) if max_retries is None else max_retries
    base = _env_float("LLM_BACKOFF_BASE", 0.5)
    backoff_max = _env_float("LLM_BACKOFF_MAX", 8.0)
    tmt = timeout if timeout is not None else _env_int("LLM_TIMEOUT_SECONDS", 180)

    logger.debug(f"HTTP POST to {url} (timeout={tmt})")
    if logger.isEnabledFor(logging.DEBUG):
        # Avoid serializing large payloads if not in debug
        logger.debug(f"Payload: {json.dumps(json_payload)[:1000]}...")

    attempt = 0
    last_exc: Optional[Exception] = None
    last_status: Optional[int] = None
    last_resp: Optional[requests.Response] = None
    while True:
        try:
            post_fn = session.post if session is not None else requests.post
            resp = post_fn(url, headers=headers, data=json.dumps(json_payload), timeout=tmt)
            last_resp = resp
            last_status = resp.status_code
            if resp.status_code < 300:
                if logger.isEnabledFor(logging.DEBUG):
                    req_id = resp.headers.get("x-request-id") or resp.headers.get("x-request-id".title())
                    proc_ms = resp.headers.get("openai-processing-ms")
                    if req_id or proc_ms:
                        logger.debug(
                            "HTTP response meta: request_id=%s openai_processing_ms=%s",
                            req_id,
                            proc_ms,
                        )
                logger.debug(f"HTTP POST success (status={resp.status_code})")
                return resp
            if not _should_retry(resp.status_code, None):
                # Non-retryable status
                logger.debug(f"HTTP POST failed with non-retriable status {resp.status_code}")
                return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            resp = None
            last_status = None

        if attempt >= max_retries:
            if last_exc:
                raise LLMError(f"HTTP POST failed after {attempt+1} attempts: {last_exc}")
            else:
                text = (last_resp.text[:200] if last_resp is not None else "")
                if last_status == 429:
                    raise LLMQuotaError(f"HTTP POST failed after {attempt+1} attempts, status 429: {text}")
                raise LLMError(f"HTTP POST failed after {attempt+1} attempts, status {last_status}: {text}")

        # Compute sleep seconds from either Retry-After or exponential backoff
        ra = _retry_after_seconds(last_resp)
        if ra is not None:
            delay = min(backoff_max, ra)
        else:
            delay = min(backoff_max, base * (2 ** attempt))
            # add jitter 0-200ms
            delay += random.uniform(0, 0.2)
        
        logger.info(f"Retrying LLM request in {delay:.2f}s (attempt {attempt+1}/{max_retries+1}) due to {last_exc or f'HTTP {last_status}'}")
        time.sleep(delay)
        attempt += 1


def _http_get(
    url: str,
    *,
    headers: Dict[str, str],
    timeout: Optional[object] = None,
    session: Optional[requests.Session] = None,
    max_retries: Optional[int] = None,
) -> requests.Response:
    """
    GET with exponential backoff + jitter on transient errors.
    Honors Retry-After when present. Raises LLMError after exhausting retries.
    """
    max_retries = _env_int("LLM_MAX_RETRIES", 2) if max_retries is None else max_retries
    base = _env_float("LLM_BACKOFF_BASE", 0.5)
    backoff_max = _env_float("LLM_BACKOFF_MAX", 8.0)
    tmt = timeout if timeout is not None else _env_int("LLM_TIMEOUT_SECONDS", 180)

    logger.debug(f"HTTP GET to {url} (timeout={tmt})")

    attempt = 0
    last_exc: Optional[Exception] = None
    last_status: Optional[int] = None
    last_resp: Optional[requests.Response] = None
    while True:
        try:
            get_fn = session.get if session is not None else requests.get
            resp = get_fn(url, headers=headers, timeout=tmt)
            last_resp = resp
            last_status = resp.status_code
            if resp.status_code < 300:
                if logger.isEnabledFor(logging.DEBUG):
                    req_id = resp.headers.get("x-request-id") or resp.headers.get("x-request-id".title())
                    proc_ms = resp.headers.get("openai-processing-ms")
                    if req_id or proc_ms:
                        logger.debug(
                            "HTTP response meta: request_id=%s openai_processing_ms=%s",
                            req_id,
                            proc_ms,
                        )
                logger.debug(f"HTTP GET success (status={resp.status_code})")
                return resp
            if not _should_retry(resp.status_code, None):
                logger.debug(f"HTTP GET failed with non-retriable status {resp.status_code}")
                return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            resp = None
            last_status = None

        if attempt >= max_retries:
            if last_exc:
                raise LLMError(f"HTTP GET failed after {attempt+1} attempts: {last_exc}")
            else:
                text = (last_resp.text[:200] if last_resp is not None else "")
                if last_status == 429:
                    raise LLMQuotaError(f"HTTP GET failed after {attempt+1} attempts, status 429: {text}")
                raise LLMError(f"HTTP GET failed after {attempt+1} attempts, status {last_status}: {text}")

        ra = _retry_after_seconds(last_resp)
        if ra is not None:
            delay = min(backoff_max, ra)
        else:
            delay = min(backoff_max, base * (2 ** attempt))
            delay += random.uniform(0, 0.2)
        logger.info(f"Retrying LLM request in {delay:.2f}s (attempt {attempt+1}/{max_retries+1}) due to {last_exc or f'HTTP {last_status}'}")
        time.sleep(delay)
        attempt += 1


def _extract_error_message(resp: requests.Response) -> str:
    if resp is None:
        return ""
    text = resp.text or ""
    try:
        data = resp.json()
    except Exception:
        return text
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or "")
            msg = str(err.get("message") or "")
            if code or msg:
                return f"{code} {msg}".strip()
        msg = data.get("message")
        if isinstance(msg, str) and msg:
            return msg
    return text


def _is_model_not_found(resp: requests.Response) -> bool:
    if resp is None:
        return False
    if resp.status_code not in (400, 404, 422):
        return False
    message = _extract_error_message(resp)
    haystack = (message or resp.text or "").lower()
    if not haystack:
        return False
    if "model_not_found" in haystack or "not_found" in haystack:
        return True
    if "model" in haystack and ("not found" in haystack or "does not exist" in haystack or "unknown" in haystack or "not supported" in haystack):
        return True
    return False


def _is_cached_content_error(resp: requests.Response) -> bool:
    if resp is None:
        return False
    if resp.status_code not in (400, 403, 404):
        return False
    message = _extract_error_message(resp)
    haystack = (message or resp.text or "").lower()
    if not haystack:
        return False
    if "cachedcontent" in haystack or "cached content" in haystack:
        if "not found" in haystack or "permission denied" in haystack or "invalid" in haystack:
            return True
    return False


class OpenAIProvider(LLMProvider):
    def __init__(self, model: Optional[str] = None, inter_request_gap: float = 0.0, api_key: Optional[str] = None):
        super().__init__(inter_request_gap=inter_request_gap)
        self.name = "openai"
        self.default_model = os.getenv("OPENAI_DEFAULT_MODEL", "gpt-4o-mini")
        self.model = model or os.getenv("OPENAI_MODEL", self.default_model)
        self.api_key_source = "param" if api_key else "env"
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY not set")
        self._session = requests.Session()

    def predict(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        system: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResponse:
        self._wait_for_gap()
        effort = _normalize_reasoning_effort(reasoning_effort)
        use_responses = bool(effort) or os.getenv("OPENAI_USE_RESPONSES", "").strip().lower() in ("1", "true", "yes")
        service_tier = _openai_service_tier("responses" if use_responses else "chat")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        base_timeout = timeout or _env_int("OPENAI_TIMEOUT_SECONDS", _env_int("LLM_TIMEOUT_SECONDS", 180))
        if effort in {"high", "xhigh"}:
            base_timeout = max(base_timeout, _env_int("OPENAI_HIGH_REASONING_TIMEOUT", 300))
        if effort == "xhigh":
            base_timeout = max(base_timeout, _env_int("OPENAI_XHIGH_REASONING_TIMEOUT", 600))
        openai_max_retries = _env_optional_int("OPENAI_MAX_RETRIES")
        openai_timeout = _openai_timeout_tuple(base_timeout, service_tier=service_tier)
        total_chars = _total_input_chars(messages, system)
        estimated_tokens = _estimate_tokens_from_chars(total_chars)
        if use_responses:
            url = "https://api.openai.com/v1/responses"
            max_tokens_override = max_tokens
            effort_override = effort
            empty_retry_used = False
            incomplete_retry_used = False
            background_forced = _env_bool("OPENAI_RESPONSES_BACKGROUND", False) or _env_bool("OPENAI_BACKGROUND", False)
            background_enabled, background_reasons = _should_use_background_auto(
                total_chars=total_chars,
                estimated_tokens=estimated_tokens,
                max_tokens=max_tokens_override,
                reasoning_effort=effort_override,
                service_tier=service_tier,
                base_timeout=base_timeout,
            )
            if background_forced:
                background_enabled = True
                background_reasons = ["forced"]
            poll_interval = _env_float("OPENAI_BACKGROUND_POLL_INTERVAL_SECONDS", 20.0)
            poll_timeout_default = max(base_timeout, 600)
            poll_timeout = _env_int("OPENAI_BACKGROUND_POLL_TIMEOUT_SECONDS", poll_timeout_default)
            poll_timeout_multiplier = _env_float("OPENAI_BACKGROUND_POLL_TIMEOUT_MULTIPLIER", 2.0)
            if background_enabled:
                logger.debug(
                    "OpenAI responses background enabled: poll_interval=%.2fs poll_timeout=%ss multiplier=%.2f service_tier=%s reasons=%s",
                    poll_interval,
                    poll_timeout,
                    poll_timeout_multiplier,
                    service_tier or "default",
                    ",".join(background_reasons) if background_reasons else "auto",
                )

            def _poll_background(response_id: str) -> Dict[str, Any]:
                if not response_id:
                    raise LLMError("OpenAI background polling requested but response id missing.")
                start_time = time.time()
                last_status = None
                last_log_time = 0.0
                extended_timeout = False
                logger.debug("OpenAI background polling started: response_id=%s", response_id)
                while True:
                    elapsed = time.time() - start_time
                    multiplier = poll_timeout_multiplier if poll_timeout_multiplier and poll_timeout_multiplier > 1 else 2.0
                    hard_timeout = poll_timeout * multiplier if poll_timeout else None
                    if poll_timeout and elapsed > (hard_timeout if extended_timeout else poll_timeout):
                        raise LLMError(
                            f"OpenAI background poll timed out after {elapsed:.1f}s "
                            f"(timeout={poll_timeout}s, extended={extended_timeout})."
                        )
                    poll_resp = _http_get(
                        f"{url}/{response_id}",
                        headers=headers,
                        timeout=openai_timeout,
                        session=self._session,
                        max_retries=openai_max_retries,
                    )
                    if poll_resp.status_code >= 300:
                        raise LLMError(f"OpenAI background poll failed {poll_resp.status_code}: {poll_resp.text[:200]}")
                    data = poll_resp.json()
                    status = data.get("status") if isinstance(data, dict) else None
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "OpenAI background poll tick: status=%s elapsed=%.1fs",
                            status,
                            elapsed,
                        )
                    now = time.time()
                    if status and (status != last_status or now - last_log_time >= 30):
                        logger.info(
                            "OpenAI background status=%s elapsed=%.1fs",
                            status,
                            elapsed,
                        )
                        last_status = status
                        last_log_time = now
                    if poll_timeout and elapsed > poll_timeout and not extended_timeout:
                        if status == "in_progress":
                            extended_timeout = True
                            if hard_timeout:
                                logger.info(
                                    "Extending OpenAI background poll timeout to %.1fs due to in_progress status.",
                                    hard_timeout,
                                )
                        else:
                            raise LLMError(
                                f"OpenAI background poll timed out after {elapsed:.1f}s "
                                f"(timeout={poll_timeout}s, status={status})."
                            )
                    if status in ("completed", "failed", "cancelled", "incomplete"):
                        return data
                    time.sleep(poll_interval)

            def _build_payload() -> Dict[str, Any]:
                payload: Dict[str, Any] = {
                    "model": self.model,
                    "input": _messages_to_responses_input(messages, system),
                }
                if effort_override:
                    payload["reasoning"] = {"effort": effort_override}
                if max_tokens_override:
                    payload["max_output_tokens"] = max_tokens_override
                if service_tier:
                    payload["service_tier"] = service_tier
                if background_enabled:
                    payload["background"] = True
                    payload["store"] = True
                if effort in (None, "", "none"):
                    payload["temperature"] = temperature
                cache_key = _build_prompt_cache_key(system, messages, model=self.model, kind="responses")
                if cache_key:
                    payload["prompt_cache_key"] = cache_key
                cache_retention = _normalize_prompt_cache_retention(
                    os.getenv("OPENAI_PROMPT_CACHE_RETENTION") or os.getenv("PROMPT_CACHE_RETENTION")
                )
                if cache_retention:
                    payload["prompt_cache_retention"] = cache_retention
                issues = _validate_openai_responses_payload(payload)
                issues.extend(_check_openai_payload_semantics(payload, kind="responses"))
                if issues:
                    logger.warning(f"OpenAI responses payload issues: {issues}")
                    if os.getenv("OPENAI_STRICT_PAYLOAD", "").strip().lower() in ("1", "true", "yes"):
                        raise LLMError(f"OpenAI responses payload validation failed: {issues}")
                return payload

            for attempt in range(3):
                payload = _build_payload()
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "OpenAI request (responses) summary: %s",
                        _summarize_openai_payload(payload, kind="responses"),
                    )
                    logger.debug(
                        "OpenAI request (responses) meta: model=%s api_key_hash=%s source=%s timeout=%s",
                        self.model,
                        _hash(self.api_key),
                        self.api_key_source,
                        openai_timeout,
                    )
                start = time.time()
                try:
                    resp = _http_post(
                        url,
                        headers=headers,
                        json_payload=payload,
                        timeout=openai_timeout,
                        session=self._session,
                        max_retries=openai_max_retries,
                    )
                except LLMError as exc:
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "OpenAI request (responses) failed: %s | summary=%s",
                            exc,
                            _summarize_openai_payload(payload, kind="responses"),
                        )
                    raise
                latency_ms = int((time.time() - start) * 1000)
                if resp.status_code >= 300:
                    if attempt == 0 and _is_model_not_found(resp) and self.model != self.default_model:
                        logger.warning(
                            "OpenAI model not found (%s); falling back to default model %s.",
                            self.model,
                            self.default_model,
                        )
                        self.model = self.default_model
                        continue
                    raise LLMError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
                if isinstance(data, dict) and data.get("error"):
                    raise LLMError(f"OpenAI responses error: {data.get('error')}")
                if background_enabled and isinstance(data, dict):
                    status = data.get("status")
                    if status in ("queued", "in_progress"):
                        data = _poll_background(str(data.get("id") or ""))
                _log_token_usage(self.name, self.model, _extract_openai_usage(data))
                text = _extract_openai_response_text(data)
                if not isinstance(text, str):
                    text = str(text)
                if isinstance(data, dict):
                    incomplete = data.get("incomplete_details")
                    reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
                    if reason == "max_output_tokens":
                        usage = _extract_openai_usage(data) or {}
                        current_max = payload.get("max_output_tokens") or max_tokens_override or 0
                        recommended = None
                        output_tokens = usage.get("completion")
                        if isinstance(incomplete, dict):
                            for key in (
                                "recommended",
                                "recommended_max_output_tokens",
                                "recommended_output_tokens",
                                "recommended_max_tokens",
                            ):
                                recommended = _coerce_int(incomplete.get(key))
                                if recommended:
                                    break
                            if output_tokens is None:
                                output_tokens = _coerce_int(incomplete.get("output_tokens"))
                        bump = _env_int("OPENAI_RESPONSES_RETRY_EXTRA_TOKENS", 512)
                        if not recommended:
                            if current_max:
                                recommended = max(current_max * 2, current_max + bump)
                            elif output_tokens:
                                recommended = max(output_tokens * 2, output_tokens + bump)
                            else:
                                recommended = _env_int("OPENAI_RESPONSES_MIN_OUTPUT_TOKENS", 10240)
                        logger.warning(
                            "OpenAI response incomplete due to max_output_tokens; output_tokens=%s max_output_tokens=%s recommended=%s",
                            output_tokens,
                            current_max or "unknown",
                            recommended or "increase max_output_tokens",
                        )
                        if (
                            not incomplete_retry_used
                            and _env_bool("OPENAI_RESPONSES_AUTO_RETRY_INCOMPLETE", True)
                        ):
                            incomplete_retry_used = True
                            cap = _env_int("OPENAI_RESPONSES_RETRY_MAX_OUTPUT_TOKENS", 4096)
                            target = recommended or 0
                            if current_max and target <= current_max:
                                target = max(current_max + bump, current_max * 2)
                            if cap:
                                target = min(target, cap)
                            if target and target != current_max:
                                max_tokens_override = target
                                logger.info(
                                    "Retrying OpenAI response after incomplete output; max_output_tokens=%s",
                                    max_tokens_override,
                                )
                                continue
                if not text:
                    status = data.get("status") if isinstance(data, dict) else None
                    output_len = None
                    if isinstance(data, dict) and isinstance(data.get("output"), list):
                        output_len = len(data.get("output"))
                    error = data.get("error") if isinstance(data, dict) else None
                    incomplete = data.get("incomplete_details") if isinstance(data, dict) else None
                    incomplete_reason = None
                    if isinstance(incomplete, dict):
                        incomplete_reason = incomplete.get("reason")
                    output_types = None
                    if isinstance(data, dict) and isinstance(data.get("output"), list):
                        output_types = [
                            item.get("type")
                            for item in data.get("output")
                            if isinstance(item, dict) and item.get("type")
                        ]
                    allow_empty = os.getenv("OPENAI_ALLOW_EMPTY_TEXT", "").strip().lower() in ("1", "true", "yes")
                    if (
                        not allow_empty
                        and not empty_retry_used
                        and incomplete_reason == "max_output_tokens"
                    ):
                        empty_retry_used = True
                        current_max = payload.get("max_output_tokens") or max_tokens_override or 0
                        base = current_max or _env_int("OPENAI_RESPONSES_MIN_OUTPUT_TOKENS", 10240)
                        multiplier = _env_int("OPENAI_RESPONSES_RETRY_MULTIPLIER", 2)
                        extra = _env_int("OPENAI_RESPONSES_RETRY_EXTRA_TOKENS", 512)
                        cap = _env_int("OPENAI_RESPONSES_RETRY_MAX_OUTPUT_TOKENS", 49152)
                        target = max(base * max(1, multiplier), base + extra)
                        max_tokens_override = min(target, cap)
                        if _env_bool("OPENAI_RESPONSES_RETRY_LOWER_EFFORT", True) and effort_override in {"medium", "high", "xhigh"}:
                            effort_override = "low"
                        logger.info(
                            "OpenAI responses returned empty text (reason=%s); retrying with max_output_tokens=%s effort=%s",
                            incomplete_reason,
                            max_tokens_override,
                            effort_override,
                        )
                        continue
                    logger.warning(
                        "OpenAI responses payload returned empty text; status=%s output_len=%s error=%s incomplete=%s output_types=%s keys=%s",
                        status,
                        output_len,
                        error,
                        incomplete,
                        output_types,
                        list(data.keys()) if isinstance(data, dict) else None,
                    )
                    if not allow_empty:
                        raise LLMError(
                            "OpenAI responses returned empty text "
                            f"(status={status}, incomplete={incomplete}, output_types={output_types})"
                        )
                logger.debug(f"OpenAI Response (responses): {text[:500]}...")
                return LLMResponse(text=text, raw=data, latency_ms=latency_ms, provider=self.name, model=self.model)

        url = "https://api.openai.com/v1/chat/completions"
        if system:
            messages = [{"role": "system", "content": system}] + messages

        def _build_chat_payload() -> Dict[str, Any]:
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                payload["max_tokens"] = max_tokens
            if service_tier:
                payload["service_tier"] = service_tier
            cache_key = _build_prompt_cache_key(system, messages, model=self.model, kind="chat")
            if cache_key:
                payload["prompt_cache_key"] = cache_key
            cache_retention = _normalize_prompt_cache_retention(
                os.getenv("OPENAI_PROMPT_CACHE_RETENTION") or os.getenv("PROMPT_CACHE_RETENTION")
            )
            if cache_retention:
                payload["prompt_cache_retention"] = cache_retention
            issues = _validate_openai_chat_payload(payload)
            issues.extend(_check_openai_payload_semantics(payload, kind="chat"))
            if issues:
                logger.warning(f"OpenAI chat payload issues: {issues}")
                if os.getenv("OPENAI_STRICT_PAYLOAD", "").strip().lower() in ("1", "true", "yes"):
                    raise LLMError(f"OpenAI chat payload validation failed: {issues}")
            return payload

        for attempt in range(2):
            payload = _build_chat_payload()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "OpenAI request (chat) summary: %s",
                    _summarize_openai_payload(payload, kind="chat"),
                )
                logger.debug(
                    "OpenAI request (chat) meta: model=%s api_key_hash=%s source=%s timeout=%s",
                    self.model,
                    _hash(self.api_key),
                    self.api_key_source,
                    openai_timeout,
                )
            start = time.time()
            try:
                resp = _http_post(
                    url,
                    headers=headers,
                    json_payload=payload,
                    timeout=openai_timeout,
                    session=self._session,
                    max_retries=openai_max_retries,
                )
            except LLMError as exc:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "OpenAI request (chat) failed: %s | summary=%s",
                        exc,
                        _summarize_openai_payload(payload, kind="chat"),
                    )
                raise
            latency_ms = int((time.time() - start) * 1000)
            if resp.status_code >= 300:
                if attempt == 0 and _is_model_not_found(resp) and self.model != self.default_model:
                    logger.warning(
                        "OpenAI model not found (%s); falling back to default model %s.",
                        self.model,
                        self.default_model,
                    )
                    self.model = self.default_model
                    continue
                raise LLMError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            _log_token_usage(self.name, self.model, _extract_openai_usage(data))
            text = ""
            try:
                choices = data.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    text = msg.get("content") or ""
            except Exception:
                text = ""
            if not isinstance(text, str):
                text = str(text)
            if not text:
                logger.warning(
                    "OpenAI chat payload returned empty text; keys=%s",
                    list(data.keys()) if isinstance(data, dict) else None,
                )

            logger.debug(f"OpenAI Response: {text[:500]}...")
            return LLMResponse(text=text, raw=data, latency_ms=latency_ms, provider=self.name, model=self.model)

    def transcribe(self, file_path: str, timeout: Optional[int] = None) -> str:
        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # Note: requests handles multipart/form-data when files is provided
        tmt = timeout or _env_int("LLM_TIMEOUT_SECONDS", 60)
        try:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f)}
                data = {"model": "whisper-1"}
                resp = requests.post(url, headers=headers, files=files, data=data, timeout=tmt)
                resp.raise_for_status()
                return resp.json().get("text", "")
        except Exception as e:
            raise LLMError(f"OpenAI transcription failed: {e}")

    def health_check(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {}
        start = time.time()
        try:
            resp = _http_post(
                url,
                headers=headers,
                json_payload=payload,
                timeout=_openai_timeout_tuple(timeout or _env_int("LLM_TIMEOUT_SECONDS", 60)),
                session=self._session,
                max_retries=_env_optional_int("OPENAI_MAX_RETRIES"),
            )
            latency_ms = int((time.time() - start) * 1000)
            ok = resp.status_code < 300
            return {"ok": ok, "status_code": resp.status_code, "latency_ms": latency_ms, "message": "ok" if ok else resp.text[:200]}
        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            return {"ok": False, "status_code": None, "latency_ms": latency_ms, "message": str(e)}

    def get_context_window(self) -> int:
        model = self.model.lower()
        if "gpt-4o" in model: # covers gpt-4o, gpt-4o-mini
            return 128000
        if "gpt-4-turbo" in model:
            return 128000
        if "gpt-4" in model:
            if "32k" in model:
                return 32768
            return 8192
        if "gpt-3.5-turbo" in model:
            if "16k" in model:
                return 16385
            return 4096
        if "o1-" in model:
            return 128000
        return super().get_context_window()


class GeminiProvider(LLMProvider):
    def __init__(self, model: Optional[str] = None, inter_request_gap: float = 0.0, api_key: Optional[str] = None, access_token: Optional[str] = None):
        super().__init__(inter_request_gap=inter_request_gap)
        self.name = "gemini"
        self.default_model = os.getenv("GEMINI_DEFAULT_MODEL", "gemini-2.5-flash")
        self.model = model or os.getenv("GEMINI_MODEL", self.default_model)
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.access_token = access_token or os.getenv("GEMINI_ACCESS_TOKEN") or os.getenv("GOOGLE_ACCESS_TOKEN")
        if not self.api_key and not self.access_token:
            raise LLMError("Neither GEMINI_API_KEY nor GEMINI_ACCESS_TOKEN (or GOOGLE_ACCESS_TOKEN) is set")
        self._cached_contents: Dict[str, str] = {}
        self._created_cached_contents: List[str] = []
        self._cache_lock = threading.Lock()
        self._disable_cached_content = False
        self._cached_override_name: Optional[str] = None

    def _gemini_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        elif self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _gemini_model_name(self) -> str:
        model = self.model or ""
        return model if model.startswith("models/") else f"models/{model}"

    def _explicit_cache_enabled(self) -> bool:
        if _env_bool("GEMINI_EXPLICIT_CACHE", True) is False:
            return False
        if self._disable_cached_content:
            return False
        return True

    def _min_cache_tokens(self) -> int:
        override = os.getenv("GEMINI_CACHE_MIN_TOKENS")
        if override:
            try:
                value = int(override)
                if value > 0:
                    return value
            except Exception:
                pass
        model = (self.model or "").lower()
        if "pro" in model and "flash" not in model:
            return 4096
        return 1024

    def _cache_seed_text(self, system: Optional[str], *, force_min_tokens: bool = False) -> str:
        seed = os.getenv("GEMINI_CACHE_SEED", "")
        base = seed.strip() if seed else "."
        if not force_min_tokens:
            return base
        min_tokens = self._min_cache_tokens()
        base_tokens = self.estimate_tokens(base) if base else 0
        missing = max(0, min_tokens - base_tokens)
        if missing <= 0:
            return base
        target_chars = missing * 8
        filler = "x " * (target_chars // 2 + 1)
        return f"{base}\n{filler[:target_chars]}"

    def _cached_content_key(self, system: Optional[str]) -> str:
        model_key = self._gemini_model_name()
        return _hash(f"{model_key}::{system or 'gemini-default-cache'}")

    def _evict_cached_content(self, system: Optional[str], name: Optional[str]) -> None:
        key = self._cached_content_key(system)
        with self._cache_lock:
            if key in self._cached_contents and (name is None or self._cached_contents.get(key) == name):
                self._cached_contents.pop(key, None)
            if name and name in self._created_cached_contents:
                try:
                    self._created_cached_contents.remove(name)
                except ValueError:
                    pass

    def _cache_ttl(self) -> Optional[str]:
        ttl = os.getenv("GEMINI_CACHE_TTL")
        if ttl:
            return ttl
        hours = os.getenv("GEMINI_CACHE_TTL_HOURS")
        if hours:
            try:
                hours_val = int(hours)
                if hours_val > 0:
                    return f"{hours_val * 3600}s"
            except Exception:
                pass
        return None

    def _create_cached_content(self, system: Optional[str]) -> Optional[str]:
        if not self._explicit_cache_enabled():
            return None
        override = os.getenv("GEMINI_CACHED_CONTENT") or os.getenv("GEMINI_CACHED_CONTENT_NAME")
        if override:
            self._cached_override_name = override
            return override
        cache_key = self._cached_content_key(system)
        with self._cache_lock:
            if cache_key in self._cached_contents:
                return self._cached_contents[cache_key]
        url = "https://generativelanguage.googleapis.com/v1beta/cachedContents"
        headers = self._gemini_headers()

        def _attempt_create(force_min_tokens: bool) -> Tuple[Optional[str], Optional[requests.Response]]:
            payload: Dict[str, Any] = {
                "model": self._gemini_model_name(),
                "contents": [
                    {"role": "user", "parts": [{"text": self._cache_seed_text(system, force_min_tokens=force_min_tokens)}]}
                ],
            }
            if system:
                payload["systemInstruction"] = {"parts": [{"text": system}]}
            ttl = self._cache_ttl()
            if ttl and re.match(r"^\d+s$", ttl):
                payload["ttl"] = ttl
            elif ttl:
                logger.warning(f"GEMINI_CACHE_TTL ignored (invalid format): {ttl}")
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=20)
                if resp.status_code >= 300:
                    return None, resp
                data = resp.json()
                name = data.get("name")
                return name, resp
            except Exception as exc:
                logger.warning(f"Gemini cachedContent creation failed: {exc}")
                return None, None

        base_seed = self._cache_seed_text(system, force_min_tokens=False)
        base_total = self.estimate_tokens((system or "") + "\n" + base_seed)
        force_min = base_total < self._min_cache_tokens()
        name, resp = _attempt_create(force_min_tokens=force_min)
        if not name and resp is not None and _is_model_not_found(resp) and self.model != self.default_model:
            logger.warning(
                "Gemini cachedContent model not found (%s); falling back to default model %s.",
                self.model,
                self.default_model,
            )
            self.model = self.default_model
            name, resp = _attempt_create(force_min_tokens=True)
        if not name and resp is not None and resp.status_code == 400:
            logger.debug(f"Gemini cachedContent create returned 400; retrying with padded seed. Body: {resp.text[:200]}")
            name, resp = _attempt_create(force_min_tokens=True)
        if not name:
            if resp is not None and resp.status_code >= 300:
                logger.warning(
                    "Gemini cachedContent creation failed (status=%s): %s",
                    resp.status_code,
                    (resp.text or "")[:200],
                )
            return None
        with self._cache_lock:
            self._cached_contents[cache_key] = name
            self._created_cached_contents.append(name)
        return name

    def cleanup(self) -> None:
        if not self._created_cached_contents:
            return
        headers = self._gemini_headers()
        for name in list(self._created_cached_contents):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/{name}"
                resp = requests.delete(url, headers=headers, timeout=10)
                if resp.status_code >= 300:
                    logger.debug(f"Gemini cachedContent delete failed ({resp.status_code}): {name}")
            except Exception:
                continue
        self._created_cached_contents = []

    def predict(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        system: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResponse:
        self._wait_for_gap()
        # Gemini generateContent expects a contents list of role/parts
        # We use v1beta as it supports newer models and reasoning features
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = self._gemini_headers()

        contents = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, str):
                contents.append({"role": role, "parts": [{"text": content}]})
            elif isinstance(content, list):
                # Handle multimodal content if provided
                parts = []
                for part in content:
                    if part.get("type") == "text":
                        parts.append({"text": part.get("text")})
                    elif part.get("type") == "image_url":
                        # Gemini expects base64 in a slightly different way
                        # For simplicity, we'll assume the URL is a data URL
                        url_val = part.get("image_url", {}).get("url", "")
                        if url_val.startswith("data:"):
                            mime, b64 = url_val.split(";base64,")
                            mime = mime.replace("data:", "")
                            parts.append({"inline_data": {"mime_type": mime, "data": b64}})
                contents.append({"role": role, "parts": parts})

        def _wants_json() -> bool:
            if os.getenv("GEMINI_JSON_MODE", "").strip().lower() in ("1", "true", "yes"):
                return True
            text_parts = [system or ""]
            for m in messages:
                content = m.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
            combined = " ".join(text_parts).lower()
            return "json" in combined and ("return json" in combined or "json only" in combined or "schema" in combined)

        def _build_payload(skip_cache: bool) -> Tuple[Dict[str, Any], Optional[str]]:
            generation_config: Dict[str, Any] = {"temperature": temperature}
            if _wants_json():
                generation_config["responseMimeType"] = "application/json"
            payload = {"contents": contents, "generationConfig": generation_config}
            cached_content = None
            if not skip_cache and self._explicit_cache_enabled():
                cached_content = self._create_cached_content(system)
            if cached_content and not skip_cache:
                payload["cachedContent"] = cached_content
            elif system:
                payload["systemInstruction"] = {"parts": [{"text": system}]}
            if max_tokens:
                payload["generationConfig"]["maxOutputTokens"] = max_tokens
            return payload, cached_content

        skip_cache = False
        for attempt in range(2):
            payload, cached_content = _build_payload(skip_cache=skip_cache)
            start = time.time()
            resp = _http_post(url, headers=headers, json_payload=payload, timeout=timeout)
            latency_ms = int((time.time() - start) * 1000)
            if resp.status_code >= 300:
                if attempt == 0 and cached_content and _is_cached_content_error(resp):
                    if cached_content == self._cached_override_name:
                        logger.warning(
                            "Gemini cachedContent override rejected (%s); disabling cachedContent for this run.",
                            cached_content,
                        )
                        self._disable_cached_content = True
                    else:
                        logger.warning(
                            "Gemini cachedContent invalid (%s); recreating cache and retrying without it.",
                            cached_content,
                        )
                        self._evict_cached_content(system, cached_content)
                    skip_cache = True
                    continue
                if attempt == 0 and _is_model_not_found(resp) and self.model != self.default_model:
                    logger.warning(
                        "Gemini model not found (%s); falling back to default model %s.",
                        self.model,
                        self.default_model,
                    )
                    self.model = self.default_model
                    continue
                raise LLMError(f"Gemini error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            _log_token_usage(self.name, self.model, _extract_gemini_usage(data))
            candidates = data.get("candidates", [])
            text = ""
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
            if not isinstance(text, str):
                text = str(text)
            
            logger.debug(f"Gemini Response: {text[:500]}...")
            return LLMResponse(text=text, raw=data, latency_ms=latency_ms, provider=self.name, model=self.model)

    def transcribe(self, file_path: str, timeout: Optional[int] = None) -> str:
        # Gemini also supports audio/video, but via standard generateContent with file upload/data.
        # For now, we'll implement it as a prediction with a specific prompt.
        import base64
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "audio/mpeg"
        
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Please transcribe this audio/video file exactly."},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{data}"}} # Re-using image_url structure for simplicity
            ]
        }]
        # We need to adjust predict to handle this generic structure
        resp = self.predict(messages, timeout=timeout)
        return resp.text

    def health_check(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        # call models:list style endpoint
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        headers = {}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        elif self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
            
        start = time.time()
        try:
            resp = requests.get(url, headers=headers, timeout=timeout or _env_int("LLM_TIMEOUT_SECONDS", 60))
            latency_ms = int((time.time() - start) * 1000)
            ok = resp.status_code < 300
            return {"ok": ok, "status_code": resp.status_code, "latency_ms": latency_ms, "message": "ok" if ok else resp.text[:200]}
        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            return {"ok": False, "status_code": None, "latency_ms": latency_ms, "message": str(e)}

    def get_context_window(self) -> int:
        model = self.model.lower()
        if "pro" in model:
            if "1.5" in model or "2.0" in model or "2.5" in model:
                return 2000000
            if "1.0" in model:
                return 32768
        if "flash" in model:
            return 1000000
        # Default fallbacks
        if "gemini-1.5" in model or "gemini-2.0" in model or "gemini-2.5" in model:
            return 1000000
        return super().get_context_window()


class OllamaProvider(LLMProvider):
    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None, inter_request_gap: float = 0.0, **kwargs):
        super().__init__(inter_request_gap=inter_request_gap)
        self.name = "ollama"
        self.default_model = os.getenv("OLLAMA_DEFAULT_MODEL", "llama3.2")
        self.model = model or os.getenv("OLLAMA_MODEL", self.default_model)
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")

    def predict(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        system: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResponse:
        self._wait_for_gap()
        # Collapse messages into a single prompt for /api/generate
        # Note: Ollama /api/chat is better for multimodal, but we are using /api/generate here
        # For simplicity, we'll keep generate but ideally it should use chat
        prompt = ""
        images = []
        for m in messages:
            content = m.get("content", "")
            role = m.get("role", "user")
            if isinstance(content, str):
                prompt += f"{role}: {content}\n"
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        prompt += f"{role}: {part.get('text')}\n"
                    elif part.get("type") == "image_url":
                        url_val = part.get("image_url", {}).get("url", "")
                        if url_val.startswith("data:"):
                            _, b64 = url_val.split(";base64,")
                            images.append(b64)
        
        if system:
            prompt = f"System: {system}\n" + prompt
        url = f"{self.base_url}/api/generate"
        def _build_payload() -> Dict[str, Any]:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "options": {"temperature": temperature},
                "stream": False,
            }
            if images:
                payload["images"] = images
            return payload
            
        # Ollama may not support explicit max_tokens uniformly across models; omit if None
        for attempt in range(2):
            payload = _build_payload()
            start = time.time()
            try:
                resp = _http_post(url, headers={"Content-Type": "application/json"}, json_payload=payload, timeout=timeout)
            except LLMError as e:
                raise LLMError(f"Ollama connection error: {e}")
            latency_ms = int((time.time() - start) * 1000)
            if resp.status_code >= 300:
                if attempt == 0 and _is_model_not_found(resp) and self.model != self.default_model:
                    logger.warning(
                        "Ollama model not found (%s); falling back to default model %s.",
                        self.model,
                        self.default_model,
                    )
                    self.model = self.default_model
                    continue
                raise LLMError(f"Ollama error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            _log_token_usage(self.name, self.model, _extract_ollama_usage(data))
            text = data.get("response", "")
            if not isinstance(text, str):
                text = str(text)
            
            if not text:
                logger.debug(f"Ollama returned an empty response. Full data: {data}")
            else:
                logger.debug(f"Ollama Response: {text[:500]}...")
                
            return LLMResponse(text=text, raw=data, latency_ms=latency_ms, provider=self.name, model=self.model)

    def transcribe(self, file_path: str, timeout: Optional[int] = None) -> str:
        raise NotImplementedError("Ollama transcription not yet implemented in this adapter.")

    def health_check(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/api/tags"
        start = time.time()
        try:
            resp = requests.get(url, timeout=timeout or _env_int("LLM_TIMEOUT_SECONDS", 60))
            latency_ms = int((time.time() - start) * 1000)
            ok = resp.status_code < 300
            return {"ok": ok, "status_code": resp.status_code, "latency_ms": latency_ms, "message": "ok" if ok else resp.text[:200]}
        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            return {"ok": False, "status_code": None, "latency_ms": latency_ms, "message": str(e)}

    def get_context_window(self) -> int:
        url = f"{self.base_url}/api/show"
        payload = {"name": self.model}
        try:
            # We use requests directly here to avoid retries/backoff for a metadata call
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                
                # 1. Check for num_ctx in parameters
                parameters = data.get("parameters", "")
                if parameters:
                    match = re.search(r"num_ctx\s+(\d+)", parameters)
                    if match:
                        return int(match.group(1))
                
                # 2. Check model info
                model_info = data.get("model_info", {})
                if model_info:
                    ctx = model_info.get("llama.context_length") or model_info.get("context_length")
                    if ctx:
                        return int(ctx)
        except Exception:
            pass
            
        # 3. Fallbacks based on known models
        m = self.model.lower()
        if "llama3" in m:
            return 8192
        if "phi3" in m:
            return 128000
        if "mistral" in m:
            return 32768
            
        return super().get_context_window()


def get_provider(name: Optional[str], model: Optional[str] = None, base_url: Optional[str] = None, inter_request_gap: float = 0.0, **kwargs) -> Optional[LLMProvider]:
    if not name:
        return None
    name = name.lower().strip()
    if name in ("openai", "chatgpt", "gpt"):
        return OpenAIProvider(model=model, inter_request_gap=inter_request_gap, **kwargs)
    if name in ("gemini", "google"):
        return GeminiProvider(model=model, inter_request_gap=inter_request_gap, **kwargs)
    if name in ("ollama",):
        return OllamaProvider(model=model, base_url=base_url, inter_request_gap=inter_request_gap, **kwargs)
    raise LLMError(f"Unknown provider: {name}")
