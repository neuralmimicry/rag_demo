"""Concurrent multi-engine orchestration for Refiner workflow LLM calls.

The repository historically selected one provider plus an optional fallback in
each workflow module. This module centralises that logic so workflows can:

- discover configured providers once,
- score providers against workflow/role specialisation,
- execute multiple candidates concurrently,
- persist health/quality telemetry, and
- augment neuromorphic tasks with concurrent SNN/AER specialist-engine context.

The orchestrator intentionally stays light-weight: it wraps existing
`LLMProvider` instances instead of replacing the provider abstraction or adding
heavy external dependencies.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from refiner.llm_providers import LLMError, LLMProvider, LLMQuotaError, LLMResponse, get_provider as default_get_provider
from refiner.refiner_ai_model_inventory import model_inventory_status
from refiner.refiner_ai_routing_profiles import (
    base_provider_specialties as routing_base_provider_specialties,
    load_routing_profiles,
    workflow_tags as routing_workflow_tags,
)
from refiner.refiner_ai_specialists import analyze_specialist_engines, build_specialist_engines, specialist_engine_summaries


logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    cleaned = str(raw).strip().lower()
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _compact_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _response_raw_payload(response: Any) -> Dict[str, Any]:
    """Normalise provider response metadata for backward-compatible orchestration.

    Refiner-native providers return `LLMResponse(raw=...)`, but a number of
    tests and monkeypatched call sites use lighter response objects exposing
    only `text`, `provider`, and `model`. The orchestrator should enrich those
    responses rather than failing the whole workflow.
    """

    raw = getattr(response, "raw", None)
    if isinstance(raw, dict):
        payload = dict(raw)
    elif raw is not None:
        payload = {"raw": raw}
    else:
        payload = {}
    provider = str(getattr(response, "provider", "") or "").strip()
    model = str(getattr(response, "model", "") or "").strip()
    if provider and "provider" not in payload:
        payload["provider"] = provider
    if model and "model" not in payload:
        payload["model"] = model
    return payload


def _response_text(response: Any) -> str:
    value = getattr(response, "text", "")
    return value if isinstance(value, str) else str(value)


def _response_provider(response: Any, fallback: str) -> str:
    provider = str(getattr(response, "provider", "") or "").strip()
    return provider or str(fallback or "").strip()


def _response_model(response: Any, fallback: str) -> str:
    model = str(getattr(response, "model", "") or "").strip()
    return model or str(fallback or "").strip()


def _preview_labels(values: Sequence[str], *, limit: int = 5) -> str:
    labels = [str(value).strip() for value in values if str(value).strip()]
    if not labels:
        return "none"
    preview = labels[: max(1, int(limit))]
    if len(labels) > len(preview):
        preview.append(f"+{len(labels) - len(preview)} more")
    return ", ".join(preview)


def _safe_write_json(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(temp_path, path)


def _load_json_file(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _default_config_path(config_path: Optional[str] = None) -> str:
    return config_path or os.getenv("REFINER_CONFIG_PATH") or "config.json"


def _orchestration_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    cfg = _load_json_file(_default_config_path(config_path))
    ai_cfg = cfg.get("ai_orchestration")
    return ai_cfg if isinstance(ai_cfg, dict) else {}


def _orchestration_enabled(config_path: Optional[str] = None) -> bool:
    ai_cfg = _orchestration_config(config_path)
    if "enabled" in ai_cfg:
        return bool(ai_cfg.get("enabled"))
    return _env_bool("REFINER_AI_ORCHESTRATION_ENABLED", True)


def _configured_provider_specs(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = _load_json_file(_default_config_path(config_path))
    ai_cfg = cfg.get("ai_orchestration") if isinstance(cfg.get("ai_orchestration"), dict) else {}
    specs: List[Dict[str, Any]] = []

    for raw in cfg.get("llm_providers") or []:
        if not isinstance(raw, dict):
            continue
        spec = dict(raw)
        spec.setdefault("_source", "config")
        specs.append(spec)

    for raw in ai_cfg.get("providers") or []:
        if not isinstance(raw, dict):
            continue
        spec = dict(raw)
        spec.setdefault("_source", "ai_orchestration")
        specs.append(spec)

    return specs


def _configured_engine_specs(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = _load_json_file(_default_config_path(config_path))
    ai_cfg = cfg.get("ai_orchestration") if isinstance(cfg.get("ai_orchestration"), dict) else {}
    specs: List[Dict[str, Any]] = []
    for raw in ai_cfg.get("engines") or []:
        if not isinstance(raw, dict):
            continue
        spec = dict(raw)
        spec.setdefault("_source", "ai_orchestration")
        specs.append(spec)
    return specs


def _metrics_path(config_path: Optional[str] = None) -> str:
    ai_cfg = _orchestration_config(config_path)
    configured = str(ai_cfg.get("registry_path") or ai_cfg.get("metrics_path") or "").strip()
    raw = os.getenv("REFINER_AI_REGISTRY_PATH") or configured
    if not raw:
        raw = os.path.join("job_data", "ai", "provider_metrics.json")
    return raw


def _selection_mode(config_path: Optional[str] = None) -> str:
    ai_cfg = _orchestration_config(config_path)
    value = (
        os.getenv("REFINER_AI_SELECTION_MODE")
        or ai_cfg.get("selection_mode")
        or "best"
    )
    cleaned = str(value).strip().lower()
    return cleaned if cleaned in {"best", "fastest"} else "best"


def _max_parallel_candidates(config_path: Optional[str] = None) -> int:
    ai_cfg = _orchestration_config(config_path)
    return max(
        1,
        _env_int(
            "REFINER_AI_MAX_CONCURRENT_CANDIDATES",
            int(ai_cfg.get("max_parallel_candidates") or 3),
        ),
    )


def _include_configured_candidates(config_path: Optional[str] = None) -> bool:
    ai_cfg = _orchestration_config(config_path)
    if "include_configured_candidates" in ai_cfg:
        return bool(ai_cfg.get("include_configured_candidates"))
    return _env_bool("REFINER_AI_INCLUDE_CONFIGURED_CANDIDATES", True)


def _health_ttl_seconds(config_path: Optional[str] = None) -> float:
    ai_cfg = _orchestration_config(config_path)
    return max(30.0, _env_float("REFINER_AI_HEALTH_TTL_SECONDS", float(ai_cfg.get("health_ttl_seconds") or 1800.0)))


def _is_interactive_workflow(workflow: str, role: str) -> bool:
    workflow_key = str(workflow or "").strip().lower()
    role_key = str(role or "").strip().lower()
    return workflow_key.startswith("assistant_") or role_key == "assistant"


def _early_success_enabled(
    config_path: Optional[str] = None,
    *,
    workflow: str,
    role: str,
    selection_mode: str,
) -> bool:
    if selection_mode == "fastest":
        return True
    ai_cfg = _orchestration_config(config_path)
    raw = os.getenv("REFINER_AI_EARLY_SUCCESS_ENABLED")
    if raw is not None:
        return _env_bool("REFINER_AI_EARLY_SUCCESS_ENABLED", False)
    if "early_success_enabled" in ai_cfg:
        return bool(ai_cfg.get("early_success_enabled"))
    return _is_interactive_workflow(workflow, role)


def _early_success_settle_seconds(
    config_path: Optional[str] = None,
    *,
    workflow: str,
    role: str,
    selection_mode: str,
) -> float:
    ai_cfg = _orchestration_config(config_path)
    default = 0.0 if selection_mode == "fastest" else (0.75 if _is_interactive_workflow(workflow, role) else 0.0)
    raw = os.getenv("REFINER_AI_EARLY_SUCCESS_SETTLE_SECONDS")
    if raw is not None:
        return max(0.0, _env_float("REFINER_AI_EARLY_SUCCESS_SETTLE_SECONDS", default))
    if "early_success_settle_seconds" in ai_cfg:
        return max(0.0, _safe_float(ai_cfg.get("early_success_settle_seconds"), default))
    return default


def _early_success_quality_floor(config_path: Optional[str] = None) -> float:
    ai_cfg = _orchestration_config(config_path)
    default = 0.5
    raw = os.getenv("REFINER_AI_EARLY_SUCCESS_MIN_QUALITY")
    if raw is not None:
        return _safe_float(raw, default)
    if "early_success_min_quality" in ai_cfg:
        return _safe_float(ai_cfg.get("early_success_min_quality"), default)
    return default


def _candidate_timeout_cap_seconds(
    config_path: Optional[str] = None,
    *,
    workflow: str,
    role: str,
) -> Optional[int]:
    ai_cfg = _orchestration_config(config_path)
    default = 45 if _is_interactive_workflow(workflow, role) else 0
    raw = os.getenv("REFINER_AI_CANDIDATE_TIMEOUT_CAP_SECONDS")
    if raw is not None:
        value = _env_int("REFINER_AI_CANDIDATE_TIMEOUT_CAP_SECONDS", default)
    elif "candidate_timeout_cap_seconds" in ai_cfg:
        value = int(_safe_float(ai_cfg.get("candidate_timeout_cap_seconds"), float(default)))
    else:
        value = default
    return None if value <= 0 else max(1, int(value))


def _provider_kwargs(provider_type: Optional[str], api_key: Optional[str]) -> Dict[str, Any]:
    provider = str(provider_type or "").strip().lower()
    secret = str(api_key or "").strip()
    if not secret:
        return {}
    if provider in {"gemini", "google"} and secret.startswith("ya29."):
        return {"access_token": secret}
    return {"api_key": secret}


def _flatten_prompt_text(messages: Sequence[Dict[str, Any]], system: Optional[str]) -> str:
    parts = [str(system or "").strip()]
    for message in messages or []:
        if not isinstance(message, dict):
            parts.append(str(message))
            continue
        parts.append(str(message.get("content") or ""))
    return "\n".join(part for part in parts if part).strip()


def _workflow_tags(workflow: str, role: str, text: str) -> Set[str]:
    return routing_workflow_tags(workflow, role, text)


def _expected_json(messages: Sequence[Dict[str, Any]], system: Optional[str]) -> bool:
    text = _flatten_prompt_text(messages, system).lower()
    hints = (
        "return only valid json",
        "respond with json only",
        "valid json",
        "json with keys",
        "output only json",
        "schema",
    )
    return any(hint in text for hint in hints)


def _try_parse_json(text: str) -> Optional[Any]:
    payload = str(text or "").strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except Exception:
        pass
    if payload.startswith("```") and payload.endswith("```"):
        inner = payload.strip("`").strip()
        if inner.startswith("json"):
            inner = inner[4:].strip()
        try:
            return json.loads(inner)
        except Exception:
            return None
    return None


def _infer_specialties(
    provider_type: str,
    model: str,
    *,
    source: str,
    configured: Optional[Sequence[str]] = None,
) -> Set[str]:
    specialties = routing_base_provider_specialties(provider_type)
    lowered_model = str(model or "").lower()
    lowered_source = str(source or "").lower()
    if "codex" in lowered_model:
        specialties.update({"code", "planning", "review"})
    if "flash" in lowered_model:
        specialties.add("fast")
    if "mini" in lowered_model or "small" in lowered_model:
        specialties.add("fast")
    if "pro" in lowered_model or "o3" in lowered_model or "o4" in lowered_model:
        specialties.add("reasoning")
    if "embed" in lowered_model:
        specialties.add("retrieval")
    if "local" in lowered_source:
        specialties.add("local")
    for item in configured or []:
        if item:
            specialties.add(str(item).strip().lower())
    return specialties


def _provider_type_from_instance(provider: LLMProvider) -> str:
    return str(getattr(provider, "name", "") or provider.__class__.__name__).strip().lower()


@dataclass
class ProviderCandidate:
    """One orchestrator candidate wrapping an existing `LLMProvider`."""

    provider: LLMProvider
    source: str
    provider_type: str
    model: str
    configured_model: str = ""
    weight: float = 0.0
    specialties: Set[str] = field(default_factory=set)
    roles: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.configured_model:
            self.configured_model = self.model

    def sync_runtime_model(self) -> str:
        runtime_model = str(getattr(self.provider, "model", "") or "").strip()
        if runtime_model and runtime_model != self.model:
            self.model = runtime_model
        return self.model

    @property
    def resolved_model(self) -> str:
        return self.sync_runtime_model()

    @property
    def candidate_id(self) -> str:
        return f"{self.provider_type}/{self.resolved_model or 'default'}"

    def label(self) -> str:
        resolved_model = self.resolved_model or "default"
        configured_model = str(self.configured_model or "").strip()
        if configured_model and configured_model != resolved_model:
            return f"{self.provider_type}/{resolved_model} (configured {configured_model})"
        return f"{self.provider_type}/{resolved_model}"

    def summary(self) -> Dict[str, Any]:
        resolved_model = self.resolved_model
        configured_model = str(self.configured_model or "").strip() or resolved_model
        return {
            "candidate_id": self.candidate_id,
            "provider": self.provider_type,
            "model": resolved_model,
            "configured_model": configured_model,
            "resolved_model": resolved_model,
            "source": self.source,
            "specialties": sorted(self.specialties),
            "roles": sorted(self.roles),
        }


@dataclass
class InvocationResult:
    """Outcome from one concurrent provider invocation."""

    candidate: ProviderCandidate
    response: Optional[LLMResponse] = None
    error: Optional[Exception] = None
    latency_ms: Optional[int] = None
    quality: float = 0.0
    score: float = float("-inf")


class ProviderMetricsStore:
    """Persistent rolling success/latency/quality history per provider."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        self._data = _load_json_file(path)
        if "candidates" not in self._data or not isinstance(self._data.get("candidates"), dict):
            self._data = {"candidates": {}, "updated_at": time.time()}

    def _candidate_bucket(self, candidate_id: str) -> Dict[str, Any]:
        with self.lock:
            candidates = self._data.setdefault("candidates", {})
            bucket = candidates.get(candidate_id)
            if not isinstance(bucket, dict):
                bucket = {"stats": {}, "roles": {}, "health": {}}
                candidates[candidate_id] = bucket
            bucket.setdefault("stats", {})
            bucket.setdefault("roles", {})
            bucket.setdefault("health", {})
            return bucket

    def _save(self) -> None:
        with self.lock:
            self._data["updated_at"] = time.time()
            _safe_write_json(self.path, self._data)

    def should_probe(self, candidate_id: str, ttl_sec: float) -> bool:
        bucket = self._candidate_bucket(candidate_id)
        checked_at = _safe_float(bucket.get("health", {}).get("checked_at"), 0.0)
        if checked_at <= 0:
            return True
        return (time.time() - checked_at) >= ttl_sec

    def health_snapshot(self, candidate_id: str) -> Dict[str, Any]:
        bucket = self._candidate_bucket(candidate_id)
        health = bucket.get("health")
        return dict(health) if isinstance(health, dict) else {}

    def record_health(self, candidate: ProviderCandidate, health: Dict[str, Any]) -> None:
        resolved_model = candidate.resolved_model
        bucket = self._candidate_bucket(candidate.candidate_id)
        bucket["provider"] = candidate.provider_type
        bucket["model"] = resolved_model
        bucket["configured_model"] = candidate.configured_model or resolved_model
        bucket["resolved_model"] = resolved_model
        bucket["specialties"] = sorted(candidate.specialties)
        bucket["health"] = dict(health or {})
        bucket["health"]["checked_at"] = time.time()
        self._save()

    def _record_stat_bucket(
        self,
        bucket: Dict[str, Any],
        *,
        success: bool,
        latency_ms: Optional[int],
        quality: float,
        error: Optional[str] = None,
    ) -> None:
        stats = bucket.setdefault("stats", {})
        stats["successes"] = int(stats.get("successes") or 0) + (1 if success else 0)
        stats["failures"] = int(stats.get("failures") or 0) + (0 if success else 1)
        total = stats["successes"] + stats["failures"]
        stats["total"] = total
        if latency_ms is not None:
            previous = stats.get("ewma_latency_ms")
            stats["ewma_latency_ms"] = latency_ms if previous is None else round((0.75 * float(previous)) + (0.25 * latency_ms), 3)
        previous_quality = _safe_float(stats.get("ewma_quality"), 0.0)
        stats["ewma_quality"] = round((0.75 * previous_quality) + (0.25 * quality), 6)
        stats["last_status"] = "success" if success else "failure"
        stats["last_error"] = error
        stats["updated_at"] = time.time()

    def record_result(
        self,
        candidate: ProviderCandidate,
        *,
        workflow: str,
        role: str,
        success: bool,
        latency_ms: Optional[int],
        quality: float,
        error: Optional[str] = None,
    ) -> None:
        resolved_model = candidate.resolved_model
        bucket = self._candidate_bucket(candidate.candidate_id)
        bucket["provider"] = candidate.provider_type
        bucket["model"] = resolved_model
        bucket["configured_model"] = candidate.configured_model or resolved_model
        bucket["resolved_model"] = resolved_model
        bucket["specialties"] = sorted(candidate.specialties)
        self._record_stat_bucket(bucket, success=success, latency_ms=latency_ms, quality=quality, error=error)
        role_key = f"{workflow}:{role}"
        role_bucket = bucket.setdefault("roles", {}).setdefault(role_key, {})
        self._record_stat_bucket({"stats": role_bucket}, success=success, latency_ms=latency_ms, quality=quality, error=error)
        self._save()

    def score_bonus(self, candidate: ProviderCandidate, *, workflow: str, role: str) -> float:
        bucket = self._candidate_bucket(candidate.candidate_id)
        role_key = f"{workflow}:{role}"
        role_stats = bucket.get("roles", {}).get(role_key)
        stats = role_stats if isinstance(role_stats, dict) and role_stats else bucket.get("stats", {})
        if not isinstance(stats, dict):
            return 0.0
        total = int(stats.get("total") or 0)
        if total <= 0:
            return 0.0
        successes = int(stats.get("successes") or 0)
        success_rate = successes / max(1, total)
        quality_bonus = _safe_float(stats.get("ewma_quality"), 0.0)
        latency = stats.get("ewma_latency_ms")
        latency_bonus = 0.0
        if latency is not None:
            latency_bonus = max(-0.35, min(0.35, (1500.0 - float(latency)) / 3000.0))
        return round((success_rate - 0.5) + quality_bonus + latency_bonus, 6)

    def export(self) -> Dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self._data))


class ConcurrentLLMProvider(LLMProvider):
    """Wrap multiple providers and select the best concurrent response."""

    refiner_orchestrated = True

    def __init__(
        self,
        *,
        candidates: Sequence[ProviderCandidate],
        workflow: str,
        role: str,
        metrics_store: ProviderMetricsStore,
        selection_mode: str,
        max_candidates: int,
        health_ttl_sec: float,
        early_success_enabled: bool,
        early_success_settle_sec: float,
        early_success_min_quality: float,
        candidate_timeout_cap_sec: Optional[int],
        specialist_engines: Optional[Sequence[Any]] = None,
        actions_log: Optional[List[str]] = None,
    ) -> None:
        super().__init__(inter_request_gap=0.0)
        self.candidates = list(candidates)
        self.workflow = str(workflow or "general").strip().lower() or "general"
        self.role = str(role or "general").strip().lower() or "general"
        self.metrics_store = metrics_store
        self.selection_mode = selection_mode
        self.max_candidates = max(1, int(max_candidates))
        self.health_ttl_sec = max(30.0, float(health_ttl_sec))
        self.early_success_enabled = bool(early_success_enabled)
        self.early_success_settle_sec = max(0.0, float(early_success_settle_sec))
        self.early_success_min_quality = float(early_success_min_quality)
        self.candidate_timeout_cap_sec = None if candidate_timeout_cap_sec is None else max(1, int(candidate_timeout_cap_sec))
        self.specialist_engines = list(specialist_engines or [])
        self.actions_log = actions_log
        self.name = f"refiner_ai:{self.workflow}:{self.role}"
        self.model = ",".join(sorted({candidate.model for candidate in self.candidates if candidate.model})) or "multi"

    def candidate_summaries(self) -> List[Dict[str, Any]]:
        return [candidate.summary() for candidate in self.candidates]

    def _record_action(self, message: str) -> None:
        cleaned = str(message or "").strip()
        if not cleaned:
            return
        if self.actions_log is not None:
            self.actions_log.append(cleaned)
        logger.info(cleaned)

    def _candidate_labels(self, candidates: Sequence[ProviderCandidate]) -> str:
        return _preview_labels([candidate.label() for candidate in candidates], limit=6)

    def _specialist_labels(self, specialist_meta: Optional[Dict[str, Any]]) -> str:
        if not isinstance(specialist_meta, dict):
            return "none"
        labels: List[str] = []
        for entry in specialist_meta.get("engines") or []:
            if not isinstance(entry, dict):
                continue
            engine_type = str(entry.get("engine") or entry.get("engine_type") or "").strip().lower()
            engine_name = str(entry.get("engine_name") or engine_type or "engine").strip()
            if engine_type and engine_name and engine_name.lower() != engine_type:
                labels.append(f"{engine_name}<{engine_type}>")
            else:
                labels.append(engine_name or engine_type or "engine")
        return _preview_labels(labels, limit=6)

    def _candidate_result_message(self, result: InvocationResult) -> str:
        candidate = result.candidate
        status = "ok" if result.response is not None else "error"
        latency = "n/a" if result.latency_ms is None else f"{result.latency_ms}ms"
        message = (
            f"AI orchestration result {self.workflow}:{self.role} <- {candidate.label()} "
            f"status={status} latency={latency}"
        )
        if result.response is not None:
            message += f" quality={result.quality:.2f}"
        if result.error is not None:
            message += f" error={_compact_text(result.error, limit=160)}"
        return message

    def _probe_health(self, candidate: ProviderCandidate) -> Dict[str, Any]:
        cached = self.metrics_store.health_snapshot(candidate.candidate_id)
        if not self.metrics_store.should_probe(candidate.candidate_id, self.health_ttl_sec):
            return cached
        try:
            health = candidate.provider.health_check(timeout=4) if hasattr(candidate.provider, "health_check") else {"ok": True}
        except Exception as exc:
            health = {"ok": False, "message": str(exc)}
        self.metrics_store.record_health(candidate, health if isinstance(health, dict) else {"ok": False})
        return health if isinstance(health, dict) else {"ok": False}

    def _rank_candidates(self, task_tags: Set[str]) -> List[Tuple[float, ProviderCandidate]]:
        ranked: List[Tuple[float, ProviderCandidate]] = []
        for candidate in self.candidates:
            overlap = len(task_tags & candidate.specialties)
            role_score = 0.0
            if candidate.roles:
                role_score = 0.6 if self.role in candidate.roles else -0.9
            health = self._probe_health(candidate)
            health_score = 0.4 if health.get("ok", True) else -1.4
            preferred = 0.7 if candidate.metadata.get("preferred") else 0.0
            score = (
                float(candidate.weight or 0.0)
                + (overlap * 0.85)
                + role_score
                + health_score
                + preferred
                + self.metrics_store.score_bonus(candidate, workflow=self.workflow, role=self.role)
            )
            ranked.append((score, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked

    def _quality_score(self, text: str, *, expected_json: bool) -> float:
        cleaned = str(text or "").strip()
        if not cleaned:
            return -3.0
        score = 0.6
        if len(cleaned) >= 40:
            score += 0.35
        if expected_json:
            parsed = _try_parse_json(cleaned)
            if parsed is None:
                score -= 2.0
            else:
                score += 2.2
                if isinstance(parsed, dict):
                    score += 0.25
                if isinstance(parsed, list):
                    score += 0.15
        if "```" in cleaned and expected_json:
            score -= 0.4
        return score

    def _invoke_candidate(
        self,
        candidate: ProviderCandidate,
        *,
        messages: Sequence[Dict[str, Any]],
        max_tokens: Optional[int],
        temperature: float,
        system: Optional[str],
        timeout: Optional[int],
        reasoning_effort: Optional[str],
        expected_json: bool,
    ) -> InvocationResult:
        quota_retries = max(0, _env_int("LLM_RATE_LIMIT_RETRIES", 2))
        timeout_retries = max(0, _env_int("LLM_TIMEOUT_RETRIES", 1))
        quota_backoff_base = max(0.1, _env_float("LLM_RATE_LIMIT_BACKOFF_BASE", 1.0))
        timeout_backoff_base = max(0.1, _env_float("LLM_TIMEOUT_BACKOFF_BASE", 1.0))
        empty_retry = _env_bool("REFINER_AI_RETRY_EMPTY_OUTPUT", True)
        effective_timeout = timeout
        if effective_timeout is None and self.candidate_timeout_cap_sec is not None:
            effective_timeout = self.candidate_timeout_cap_sec
        attempts = 0
        quota_attempts = 0
        timeout_attempts = 0
        while True:
            attempts += 1
            started = time.time()
            try:
                response = candidate.provider.predict(
                    messages=list(messages),
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    timeout=effective_timeout,
                    reasoning_effort=reasoning_effort,
                )
                text = _response_text(response)
                if not text.strip() and empty_retry and attempts <= 2:
                    raise LLMError("provider returned empty text")
                latency_ms = max(0, int((time.time() - started) * 1000))
                quality = self._quality_score(text, expected_json=expected_json)
                return InvocationResult(
                    candidate=candidate,
                    response=response,
                    latency_ms=latency_ms,
                    quality=quality,
                )
            except LLMQuotaError as exc:
                if quota_attempts >= quota_retries:
                    latency_ms = max(0, int((time.time() - started) * 1000))
                    return InvocationResult(candidate=candidate, error=exc, latency_ms=latency_ms)
                delay = quota_backoff_base * (2 ** quota_attempts)
                quota_attempts += 1
                time.sleep(delay)
            except LLMError as exc:
                lowered = str(exc).lower()
                is_timeout = "timed out" in lowered or "timeout" in lowered
                if is_timeout and timeout_attempts < timeout_retries:
                    delay = timeout_backoff_base * (2 ** timeout_attempts)
                    timeout_attempts += 1
                    time.sleep(delay)
                    continue
                latency_ms = max(0, int((time.time() - started) * 1000))
                return InvocationResult(candidate=candidate, error=exc, latency_ms=latency_ms)
            except Exception as exc:
                latency_ms = max(0, int((time.time() - started) * 1000))
                return InvocationResult(candidate=candidate, error=exc, latency_ms=latency_ms)

    def _accepts_early_success(self, result: InvocationResult) -> bool:
        return bool(result.response is not None and result.quality >= self.early_success_min_quality)

    def _collect_future_result(
        self,
        future,
        future_map: Dict[Any, ProviderCandidate],
    ) -> InvocationResult:
        try:
            return future.result()
        except Exception as exc:
            candidate = future_map[future]
            return InvocationResult(candidate=candidate, error=exc)

    def _predict_impl(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        system: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResponse:
        prompt_text = _flatten_prompt_text(messages, system)
        task_tags = _workflow_tags(self.workflow, self.role, prompt_text)
        specialist_meta = None
        system_to_send = system
        always_route_specialists = (
            _env_bool("REFINER_NEUROMORPHIC_ALWAYS_ROUTE", False)
            or _env_bool("REFINER_SPECIALIST_ENGINES_ALWAYS_ROUTE", False)
            or _env_bool("REFINER_AARNN_ALWAYS_ROUTE", False)
        )
        if self.specialist_engines and ("neuromorphic" in task_tags or always_route_specialists):
            try:
                specialist_meta = analyze_specialist_engines(
                    self.specialist_engines,
                    text=prompt_text,
                    workflow=self.workflow,
                    role=self.role,
                )
                if specialist_meta.get("relevant"):
                    task_tags.update({"neuromorphic", "aer"})
                    task_tags.update(
                        {
                            str(tag).strip().lower()
                            for tag in (specialist_meta.get("combined_specialties") or [])
                            if str(tag).strip()
                        }
                    )
                    context = str(specialist_meta.get("context") or "").strip()
                    if context:
                        system_to_send = (f"{system}\n\n{context}" if system else context)
                    self._record_action(
                        f"AI orchestration attached {int(specialist_meta.get('engine_count') or 0)} "
                        f"specialist engine(s) for {self.workflow}:{self.role}: "
                        f"{self._specialist_labels(specialist_meta)}."
                    )
            except Exception as exc:
                logger.debug("Specialist engine analysis skipped: %s", exc)

        ranked_candidates = self._rank_candidates(task_tags)
        selected = [candidate for _, candidate in ranked_candidates[: self.max_candidates]]
        if not selected:
            raise LLMError("No LLM providers are available for orchestration")
        self._record_action(
            f"AI orchestration dispatch {self.workflow}:{self.role} to "
            f"{len(selected)}/{len(ranked_candidates)} candidate(s): {self._candidate_labels(selected)}. "
            f"tags={_preview_labels(sorted(task_tags), limit=8)}"
        )

        expected_json = _expected_json(messages, system_to_send)
        results: List[InvocationResult] = []
        returned_early = False
        if len(selected) == 1:
            result = self._invoke_candidate(
                selected[0],
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_to_send,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
                expected_json=expected_json,
            )
            results.append(result)
            self._record_action(self._candidate_result_message(result))
        else:
            executor = ThreadPoolExecutor(max_workers=len(selected))
            future_map = {
                executor.submit(
                    self._invoke_candidate,
                    candidate,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_to_send,
                    timeout=timeout,
                    reasoning_effort=reasoning_effort,
                    expected_json=expected_json,
                ): candidate
                for candidate in selected
            }
            pending = set(future_map)
            early_deadline: Optional[float] = None
            returned_early = False
            try:
                while pending:
                    wait_timeout = None
                    if early_deadline is not None:
                        wait_timeout = max(0.0, early_deadline - time.time())
                    done, pending = wait(
                        pending,
                        timeout=wait_timeout,
                        return_when=FIRST_COMPLETED,
                    )
                    if not done:
                        if early_deadline is not None:
                            returned_early = True
                            break
                        continue
                    for future in done:
                        result = self._collect_future_result(future, future_map)
                        results.append(result)
                        self._record_action(self._candidate_result_message(result))
                        if self.early_success_enabled and self._accepts_early_success(result):
                            if self.selection_mode == "fastest":
                                returned_early = True
                                pending.clear()
                                break
                            if early_deadline is None:
                                early_deadline = time.time() + self.early_success_settle_sec
                    if returned_early:
                        break
                    if early_deadline is not None and time.time() >= early_deadline:
                        returned_early = True
                        break
            finally:
                executor.shutdown(wait=not returned_early, cancel_futures=returned_early)

        successful: List[InvocationResult] = []
        failures: List[InvocationResult] = []
        for result in results:
            if result.response is not None:
                latency_penalty = 0.0
                if result.latency_ms is not None:
                    latency_penalty = min(1.25, result.latency_ms / 5000.0)
                result.score = (
                    result.quality
                    - latency_penalty
                    + self.metrics_store.score_bonus(result.candidate, workflow=self.workflow, role=self.role)
                )
                successful.append(result)
                self.metrics_store.record_result(
                    result.candidate,
                    workflow=self.workflow,
                    role=self.role,
                    success=True,
                    latency_ms=result.latency_ms,
                    quality=result.quality,
                )
            else:
                failures.append(result)
                self.metrics_store.record_result(
                    result.candidate,
                    workflow=self.workflow,
                    role=self.role,
                    success=False,
                    latency_ms=result.latency_ms,
                    quality=-1.0,
                    error=str(result.error),
                )

        if not successful:
            if failures:
                self._record_action(
                    f"AI orchestration exhausted {len(selected)} candidate(s) for {self.workflow}:{self.role}; "
                    f"last_error={_compact_text(failures[-1].error, limit=160)}"
                )
                last_error = failures[-1].error
                if isinstance(last_error, Exception):
                    raise last_error
                raise LLMError(str(last_error))
            raise LLMError("LLM orchestration returned no responses")

        chosen = max(successful, key=lambda item: item.score)
        chosen_provider = _response_provider(chosen.response, chosen.candidate.provider_type)
        chosen_model = _response_model(chosen.response, chosen.candidate.model)
        self._record_action(
            f"AI orchestration selected {chosen.candidate.label()} for {self.workflow}:{self.role} "
            f"from {len(selected)} candidate(s); mode={self.selection_mode} "
            f"returned_early={returned_early if len(selected) > 1 else False}."
        )
        raw_payload = _response_raw_payload(chosen.response)
        raw_payload["refiner_ai"] = {
            "workflow": self.workflow,
            "role": self.role,
            "task_tags": sorted(task_tags),
            "selection_mode": self.selection_mode,
            "returned_early": returned_early if len(selected) > 1 else False,
            "early_success_enabled": self.early_success_enabled,
            "early_success_settle_sec": self.early_success_settle_sec,
            "selected": chosen.candidate.summary(),
            "candidates": [
                {
                    **result.candidate.summary(),
                    "latency_ms": result.latency_ms,
                    "quality": result.quality,
                    "score": result.score,
                    "status": "ok" if result.response else "error",
                    "error_class": type(result.error).__name__ if result.error else None,
                    "error": str(result.error) if result.error else None,
                }
                for result in (successful + failures)
            ],
            "metrics_store": self.metrics_store.path,
            "specialist_engines": specialist_meta,
            "neuromorphic": specialist_meta,
        }
        return LLMResponse(
            text=_response_text(chosen.response),
            raw=raw_payload,
            latency_ms=chosen.latency_ms,
            provider=chosen_provider,
            model=chosen_model,
        )

    def transcribe(self, file_path: str, timeout: Optional[int] = None) -> str:
        for candidate in self.candidates:
            try:
                return candidate.provider.transcribe(file_path, timeout=timeout)
            except Exception:
                continue
        raise LLMError("No orchestrated provider can transcribe this file")

    def health_check(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        ranked = self._rank_candidates(set())
        if not ranked:
            return {"ok": False, "message": "no candidates"}
        score, candidate = ranked[0]
        health = self._probe_health(candidate)
        return {
            "ok": bool(health.get("ok", True)),
            "provider": candidate.provider_type,
            "model": candidate.model,
            "selection_score": score,
            "candidate_count": len(self.candidates),
            "specialist_engine_count": len(self.specialist_engines),
            "details": health,
        }

    def estimate_tokens(self, text: str) -> int:
        estimates = []
        for candidate in self.candidates:
            try:
                estimates.append(candidate.provider.estimate_tokens(text))
            except Exception:
                continue
        return max(estimates) if estimates else super().estimate_tokens(text)

    def get_context_window(self) -> int:
        windows = []
        for candidate in self.candidates:
            try:
                windows.append(candidate.provider.get_context_window())
            except Exception:
                continue
        return max(windows) if windows else super().get_context_window()


def _candidate_from_instance(
    provider: Optional[LLMProvider],
    *,
    source: str,
    preferred: bool = False,
    configured_specialties: Optional[Sequence[str]] = None,
    configured_roles: Optional[Sequence[str]] = None,
    weight: float = 0.0,
) -> Optional[ProviderCandidate]:
    if provider is None:
        return None
    provider_type = _provider_type_from_instance(provider)
    model = str(getattr(provider, "model", "") or "").strip()
    specialties = _infer_specialties(
        provider_type,
        model,
        source=source,
        configured=configured_specialties,
    )
    roles = {str(role).strip().lower() for role in (configured_roles or []) if str(role).strip()}
    return ProviderCandidate(
        provider=provider,
        source=source,
        provider_type=provider_type,
        model=model,
        weight=float(weight or 0.0),
        specialties=specialties,
        roles=roles,
        metadata={"preferred": preferred},
    )


def _candidate_from_spec(
    spec: Dict[str, Any],
    *,
    provider_factory: Callable[..., Optional[LLMProvider]],
    base_url: Optional[str],
    inter_request_gap: float,
) -> Optional[ProviderCandidate]:
    provider_type = str(spec.get("provider") or spec.get("type") or spec.get("llm_provider") or "").strip()
    if not provider_type:
        return None
    model = str(spec.get("model") or "").strip() or None
    provider_base_url = str(spec.get("base_url") or base_url or "").strip() or None
    api_key = spec.get("api_key")
    kwargs = _provider_kwargs(provider_type, str(api_key or "").strip() or None)
    provider = provider_factory(
        provider_type,
        model=model,
        base_url=provider_base_url,
        inter_request_gap=inter_request_gap,
        **kwargs,
    )
    if not provider:
        return None
    roles = spec.get("roles") or spec.get("role")
    if isinstance(roles, str):
        roles = [roles]
    specialties = spec.get("specialties") or spec.get("tags")
    if isinstance(specialties, str):
        specialties = [specialties]
    return _candidate_from_instance(
        provider,
        source=str(spec.get("_source") or spec.get("name") or "config"),
        preferred=bool(spec.get("preferred")),
        configured_specialties=specialties if isinstance(specialties, list) else None,
        configured_roles=roles if isinstance(roles, list) else None,
        weight=_safe_float(spec.get("weight"), _safe_float(spec.get("priority"), 0.0)),
    )


def _gail_bridge() -> Optional[Dict[str, Callable[..., Any]]]:
    """Load the optional Gail bridge lazily to avoid import cycles."""

    try:
        from refiner.refiner_ai_gail import (
            build_workflow_provider as gail_build_workflow_provider,
            build_workflow_provider_from_candidates as gail_build_workflow_provider_from_candidates,
            gail_enabled as is_gail_enabled,
            gail_status as fetch_gail_status,
        )
    except Exception as exc:
        logger.debug("Gail workflow bridge unavailable, using local orchestration: %s", exc)
        return None

    try:
        if not is_gail_enabled():
            return None
    except Exception as exc:
        logger.debug("Gail enablement check failed, using local orchestration: %s", exc)
        return None

    return {
        "build_workflow_provider": gail_build_workflow_provider,
        "build_workflow_provider_from_candidates": gail_build_workflow_provider_from_candidates,
        "gail_status": fetch_gail_status,
    }


def orchestrate_provider_candidates(
    candidates: Sequence[Optional[LLMProvider]],
    *,
    workflow: str,
    role: str,
    provider_factory: Optional[Callable[..., Optional[LLMProvider]]] = None,
    config_path: Optional[str] = None,
    include_configured: Optional[bool] = None,
    base_url: Optional[str] = None,
    inter_request_gap: float = 0.0,
    selection_mode: Optional[str] = None,
    max_candidates: Optional[int] = None,
    actions_log: Optional[List[str]] = None,
    specialist_engines: Optional[Sequence[Any]] = None,
) -> Optional[LLMProvider]:
    """Wrap provider instances in the concurrent orchestrator."""

    existing = [provider for provider in candidates if provider is not None]
    if not existing:
        return None
    if len(existing) == 1 and getattr(existing[0], "refiner_orchestrated", False):
        return existing[0]

    resolved_include_configured = (
        _include_configured_candidates(config_path)
        if include_configured is None
        else bool(include_configured)
    )
    gail_bridge = _gail_bridge()
    if gail_bridge is not None:
        return gail_bridge["build_workflow_provider_from_candidates"](
            existing,
            workflow=workflow,
            role=role,
            include_configured=resolved_include_configured,
            base_url=base_url,
            inter_request_gap=inter_request_gap,
            selection_mode=selection_mode,
            max_candidates=max_candidates,
        )

    if not _orchestration_enabled(config_path):
        return existing[0]

    candidate_entries: List[ProviderCandidate] = []
    for index, provider in enumerate(existing):
        candidate = _candidate_from_instance(
            provider,
            source="existing_primary" if index == 0 else f"existing_{index + 1}",
            preferred=index == 0,
            weight=0.4 if index == 0 else 0.0,
        )
        if candidate is not None:
            candidate_entries.append(candidate)

    include_extra = resolved_include_configured
    factory = provider_factory or default_get_provider
    if include_extra:
        for spec in _configured_provider_specs(config_path):
            try:
                candidate = _candidate_from_spec(
                    spec,
                    provider_factory=factory,
                    base_url=base_url,
                    inter_request_gap=inter_request_gap,
                )
            except Exception as exc:
                logger.debug("Skipping configured provider candidate %s: %s", spec.get("name") or spec.get("type"), exc)
                continue
            if candidate is not None:
                candidate_entries.append(candidate)

    deduped: List[ProviderCandidate] = []
    seen: Set[str] = set()
    for candidate in candidate_entries:
        key = candidate.candidate_id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    metrics_store = ProviderMetricsStore(_metrics_path(config_path))
    resolved_specialist_engines = list(specialist_engines) if specialist_engines is not None else build_specialist_engines(_default_config_path(config_path))
    if len(deduped) == 1 and not resolved_specialist_engines:
        return deduped[0].provider
    return ConcurrentLLMProvider(
        candidates=deduped,
        workflow=workflow,
        role=role,
        metrics_store=metrics_store,
        selection_mode=selection_mode or _selection_mode(config_path),
        max_candidates=max_candidates or _max_parallel_candidates(config_path),
        health_ttl_sec=_health_ttl_seconds(config_path),
        early_success_enabled=_early_success_enabled(
            config_path,
            workflow=workflow,
            role=role,
            selection_mode=selection_mode or _selection_mode(config_path),
        ),
        early_success_settle_sec=_early_success_settle_seconds(
            config_path,
            workflow=workflow,
            role=role,
            selection_mode=selection_mode or _selection_mode(config_path),
        ),
        early_success_min_quality=_early_success_quality_floor(config_path),
        candidate_timeout_cap_sec=_candidate_timeout_cap_seconds(
            config_path,
            workflow=workflow,
            role=role,
        ),
        specialist_engines=resolved_specialist_engines,
        actions_log=actions_log,
    )


def build_workflow_provider(
    *,
    workflow: str,
    role: str,
    preferred_provider: Optional[str],
    preferred_model: Optional[str],
    preferred_api_key: Optional[str] = None,
    fallback_provider: Optional[str] = None,
    fallback_model: Optional[str] = None,
    fallback_api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    provider_factory: Optional[Callable[..., Optional[LLMProvider]]] = None,
    config_path: Optional[str] = None,
    include_configured: Optional[bool] = None,
    inter_request_gap: float = 0.0,
    selection_mode: Optional[str] = None,
    max_candidates: Optional[int] = None,
    actions_log: Optional[List[str]] = None,
    specialist_engines: Optional[Sequence[Any]] = None,
) -> Optional[LLMProvider]:
    """Build and orchestrate providers from provider/model configuration."""

    resolved_include_configured = (
        _include_configured_candidates(config_path)
        if include_configured is None
        else bool(include_configured)
    )
    gail_bridge = _gail_bridge()
    if gail_bridge is not None:
        preferred_kwargs = _provider_kwargs(preferred_provider, preferred_api_key)
        fallback_kwargs = _provider_kwargs(fallback_provider, fallback_api_key)
        return gail_bridge["build_workflow_provider"](
            workflow=workflow,
            role=role,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            preferred_api_key=preferred_kwargs.get("api_key"),
            preferred_access_token=preferred_kwargs.get("access_token"),
            fallback_provider=fallback_provider,
            fallback_model=fallback_model,
            fallback_api_key=fallback_kwargs.get("api_key"),
            fallback_access_token=fallback_kwargs.get("access_token"),
            base_url=base_url,
            include_configured=resolved_include_configured,
            inter_request_gap=inter_request_gap,
            selection_mode=selection_mode,
            max_candidates=max_candidates,
        )

    factory = provider_factory or default_get_provider
    built: List[Optional[LLMProvider]] = []
    if preferred_provider:
        kwargs = _provider_kwargs(preferred_provider, preferred_api_key)
        built.append(
            factory(
                preferred_provider,
                model=preferred_model,
                base_url=base_url,
                inter_request_gap=inter_request_gap,
                **kwargs,
            )
        )
    if fallback_provider:
        kwargs = _provider_kwargs(fallback_provider, fallback_api_key)
        built.append(
            factory(
                fallback_provider,
                model=fallback_model,
                base_url=base_url,
                inter_request_gap=inter_request_gap,
                **kwargs,
            )
        )
    return orchestrate_provider_candidates(
        built,
        workflow=workflow,
        role=role,
        provider_factory=factory,
        config_path=config_path,
        include_configured=include_configured,
        base_url=base_url,
        inter_request_gap=inter_request_gap,
        selection_mode=selection_mode,
        max_candidates=max_candidates,
        actions_log=actions_log,
        specialist_engines=specialist_engines,
    )


def describe_provider(provider: Optional[LLMProvider]) -> Dict[str, Any]:
    """Return a serialisable summary for plain and orchestrated providers."""

    if provider is None:
        return {"available": False}
    if getattr(provider, "refiner_orchestrated", False):
        health = {}
        try:
            health = provider.health_check()  # type: ignore[assignment]
        except Exception:
            health = {"ok": False}
        return {
            "available": True,
            "mode": "orchestrated",
            "workflow": getattr(provider, "workflow", None),
            "role": getattr(provider, "role", None),
            "candidates": provider.candidate_summaries(),  # type: ignore[attr-defined]
            "specialist_engines": [
                engine.summary(probe_health=False)
                for engine in getattr(provider, "specialist_engines", [])
                if hasattr(engine, "summary")
            ],
            "health": health,
        }
    return {
        "available": True,
        "mode": "single",
        "provider": getattr(provider, "name", None),
        "model": getattr(provider, "model", None),
    }


def provider_log_summary(provider: Optional[LLMProvider]) -> str:
    """Return a concise provider summary suitable for live logs."""

    if provider is None:
        return "unavailable"
    if getattr(provider, "refiner_orchestrated", False):
        candidates = provider.candidate_summaries()  # type: ignore[attr-defined]
        candidate_labels: List[str] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            provider_type = str(candidate.get("provider") or "provider").strip()
            resolved_model = str(candidate.get("resolved_model") or candidate.get("model") or "default").strip() or "default"
            configured_model = str(candidate.get("configured_model") or "").strip()
            label = f"{provider_type}/{resolved_model}"
            if configured_model and configured_model != resolved_model:
                label += f" (configured {configured_model})"
            candidate_labels.append(label)
        engine_labels: List[str] = []
        for engine in getattr(provider, "specialist_engines", []):
            engine_type = str(getattr(engine, "engine_type", "") or "").strip().lower()
            engine_name = str(getattr(engine, "name", "") or engine_type or "engine").strip()
            if engine_type and engine_name and engine_name.lower() != engine_type:
                engine_labels.append(f"{engine_name}<{engine_type}>")
            else:
                engine_labels.append(engine_name or engine_type or "engine")
        summary = (
            f"orchestrated mode={getattr(provider, 'selection_mode', 'best')} "
            f"candidates={_preview_labels(candidate_labels, limit=6)}"
        )
        if engine_labels:
            summary += f"; specialist_engines={_preview_labels(engine_labels, limit=6)}"
        return summary
    provider_name = str(getattr(provider, "name", "") or provider.__class__.__name__).strip().lower() or "provider"
    model = str(getattr(provider, "model", "") or "").strip() or "default"
    return f"single {provider_name}/{model}"


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def _provider_spec_summary(spec: Dict[str, Any]) -> Dict[str, Any]:
    provider_type = str(spec.get("provider") or spec.get("type") or spec.get("llm_provider") or "").strip().lower()
    model = str(spec.get("model") or "").strip() or None
    return {
        "name": str(spec.get("name") or provider_type or "provider").strip(),
        "provider": provider_type or None,
        "model": model,
        "source": str(spec.get("_source") or spec.get("source") or "config").strip(),
        "roles": _as_list(spec.get("roles") or spec.get("role")),
        "specialties": _as_list(spec.get("specialties") or spec.get("tags")),
        "weight": _safe_float(spec.get("weight"), _safe_float(spec.get("priority"), 0.0)),
        "preferred": bool(spec.get("preferred")),
        "base_url": str(spec.get("base_url") or "").strip() or None,
    }


def _metrics_summary(path: str, *, limit: int = 20) -> Dict[str, Any]:
    data = _load_json_file(path)
    raw_candidates = data.get("candidates") if isinstance(data.get("candidates"), dict) else {}
    candidates: List[Dict[str, Any]] = []
    for candidate_id, bucket in raw_candidates.items():
        if not isinstance(bucket, dict):
            continue
        stats = bucket.get("stats") if isinstance(bucket.get("stats"), dict) else {}
        health = bucket.get("health") if isinstance(bucket.get("health"), dict) else {}
        successes = int(stats.get("successes") or 0)
        failures = int(stats.get("failures") or 0)
        total = int(stats.get("total") or (successes + failures))
        success_rate = round(successes / total, 6) if total > 0 else None
        candidates.append(
            {
                "candidate_id": str(candidate_id),
                "provider": bucket.get("provider"),
                "model": bucket.get("model"),
                "configured_model": bucket.get("configured_model"),
                "resolved_model": bucket.get("resolved_model") or bucket.get("model"),
                "specialties": bucket.get("specialties") if isinstance(bucket.get("specialties"), list) else [],
                "successes": successes,
                "failures": failures,
                "total": total,
                "success_rate": success_rate,
                "ewma_latency_ms": stats.get("ewma_latency_ms"),
                "ewma_quality": stats.get("ewma_quality"),
                "last_status": stats.get("last_status"),
                "last_error": stats.get("last_error"),
                "updated_at": stats.get("updated_at"),
                "health_ok": health.get("ok"),
                "health_mode": health.get("mode"),
                "health_checked_at": health.get("checked_at"),
            }
        )
    candidates.sort(
        key=lambda item: (
            1 if item.get("health_ok") is True else 0,
            item.get("success_rate") if item.get("success_rate") is not None else -1.0,
            item.get("ewma_quality") if item.get("ewma_quality") is not None else -999.0,
            -(item.get("ewma_latency_ms") if item.get("ewma_latency_ms") is not None else 10**9),
        ),
        reverse=True,
    )
    limited = candidates[: max(1, int(limit))]
    return {
        "path": path,
        "exists": os.path.exists(path),
        "updated_at": data.get("updated_at"),
        "candidate_count": len(candidates),
        "healthy_candidates": sum(1 for item in candidates if item.get("health_ok") is True),
        "degraded_candidates": sum(1 for item in candidates if item.get("health_ok") is False),
        "candidates": limited,
    }


def orchestration_status(
    config_path: Optional[str] = None,
    *,
    include_metrics: bool = True,
    probe_engines: bool = False,
    candidate_limit: int = 20,
) -> Dict[str, Any]:
    """Return a serialisable view of configured providers, engines, and telemetry."""

    gail_bridge = _gail_bridge()
    if gail_bridge is not None:
        return gail_bridge["gail_status"](
            candidate_limit=candidate_limit,
            probe_engines=probe_engines,
            probe_providers=False,
        )

    config_file = _default_config_path(config_path)
    provider_specs = [_provider_spec_summary(spec) for spec in _configured_provider_specs(config_file)]
    engine_summaries = specialist_engine_summaries(config_file, probe_health=probe_engines)
    try:
        routing_profiles = load_routing_profiles()
    except Exception:
        routing_profiles = {}

    metrics_path = _metrics_path(config_file)
    return {
        "enabled": _orchestration_enabled(config_file),
        "config_path": config_file,
        "routing_profiles_path": routing_profiles.get("path"),
        "routing_profiles_version": routing_profiles.get("version"),
        "selection_mode": _selection_mode(config_file),
        "max_parallel_candidates": _max_parallel_candidates(config_file),
        "health_ttl_seconds": _health_ttl_seconds(config_file),
        "provider_count": len(provider_specs),
        "providers": provider_specs,
        "engine_count": len(engine_summaries),
        "engines": engine_summaries,
        "metrics": _metrics_summary(metrics_path, limit=candidate_limit)
        if include_metrics
        else {
            "path": metrics_path,
            "exists": os.path.exists(metrics_path),
        },
        "model_inventory": model_inventory_status(
            config_path=config_file,
            limit=candidate_limit,
            refresh_if_missing=False,
        ),
    }
