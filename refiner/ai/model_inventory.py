"""Resource-aware local model inventory for Refiner AI orchestration.

This module keeps local-model selection deterministic and safe:

- probe locally installed Ollama models without triggering pulls,
- estimate whether candidate models fit current disk/RAM budgets,
- rank candidates by capability relevance to current workflow needs,
- persist a cached inventory for admin/orchestration drill-down views, and
- resolve a safe local fallback at request time when auto-pull is disabled.

The inventory intentionally does not download models. It only reports which
models are already installed and which additional models would be reasonable
download candidates for the current needs and host resources.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import requests


logger = logging.getLogger(__name__)

_SNAPSHOT_CACHE_LOCK = threading.Lock()
_SNAPSHOT_CACHE_PATH: Optional[str] = None
_SNAPSHOT_CACHE_MTIME: Optional[float] = None
_SNAPSHOT_CACHE_VALUE: Optional[Dict[str, Any]] = None

_MODEL_BYTES_PER_BILLION_PARAMS = 0.68 * (1024 ** 3)
_DEFAULT_MODEL_HINTS: Dict[str, List[str]] = {
    "chat": ["llama3.2"],
    "reasoning": ["qwen2.5:7b", "llama3.2"],
    "code": ["qwen2.5-coder:7b"],
    "vision": ["llava:7b"],
}
_ROLE_CAPABILITY_HINTS: Dict[str, Set[str]] = {
    "general": {"chat", "reasoning"},
    "assistant": {"chat"},
    "planner": {"reasoning"},
    "reviewer": {"reasoning"},
    "researcher": {"chat", "reasoning"},
}
_SPECIALTY_CAPABILITY_HINTS: Dict[str, Set[str]] = {
    "planning": {"reasoning"},
    "review": {"reasoning"},
    "audit": {"reasoning"},
    "analysis": {"reasoning"},
    "json": {"reasoning"},
    "code": {"code", "reasoning"},
    "research": {"chat", "reasoning"},
    "long_context": {"chat", "reasoning"},
    "requirements": {"chat", "reasoning"},
    "vision": {"vision"},
    "image": {"vision"},
    "multimodal": {"vision"},
}
_TEXT_CAPABILITY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "vision": ("image", "images", "picture", "pictures", "photo", "photos", "diagram", "screenshot", "ocr", "visual", "multimodal"),
    "code": ("code", "coder", "coding", "program", "function", "api", "refactor", "bug", "test", "pytest", "json schema"),
    "reasoning": ("reason", "reasoning", "analyse", "analyze", "plan", "review", "audit", "solve", "math", "proof"),
    "chat": ("chat", "chatbot", "assistant", "conversation", "reply", "answer", "support"),
}
_MODEL_CAPABILITY_HINTS: Dict[str, Set[str]] = {
    "coder": {"code", "reasoning"},
    "code": {"code", "reasoning"},
    "deepseek-r1": {"reasoning"},
    "reason": {"reasoning"},
    "think": {"reasoning"},
    "qwq": {"reasoning"},
    "vision": {"vision"},
    "llava": {"vision"},
    "bakllava": {"vision"},
    "moondream": {"vision"},
    "multimodal": {"vision"},
    "vl": {"vision"},
    "embed": {"embedding"},
    "embedding": {"embedding"},
    "chat": {"chat"},
    "assistant": {"chat"},
    "instruct": {"chat"},
    "llama": {"chat", "reasoning"},
    "qwen": {"chat", "reasoning"},
    "mistral": {"chat", "reasoning"},
    "gemma": {"chat", "reasoning"},
    "phi": {"chat", "reasoning"},
    "deepseek": {"chat", "reasoning"},
}
_MODEL_CATALOG_KEYS = (
    "model_catalog",
    "models",
    "ollama_model_catalog",
    "ollama_models",
)


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_write_json(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
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


def inventory_enabled(config_path: Optional[str] = None) -> bool:
    ai_cfg = _orchestration_config(config_path)
    if "model_inventory_enabled" in ai_cfg:
        return bool(ai_cfg.get("model_inventory_enabled"))
    return _env_bool("REFINER_AI_MODEL_INVENTORY_ENABLED", True)


def inventory_path(config_path: Optional[str] = None) -> str:
    ai_cfg = _orchestration_config(config_path)
    configured = str(
        ai_cfg.get("model_inventory_path")
        or ai_cfg.get("inventory_path")
        or ""
    ).strip()
    raw = os.getenv("REFINER_AI_MODEL_INVENTORY_PATH") or configured
    if not raw:
        raw = os.path.join("job_data", "ai", "model_inventory.json")
    return raw


def inventory_poll_seconds(config_path: Optional[str] = None) -> float:
    ai_cfg = _orchestration_config(config_path)
    configured = int(_safe_float(ai_cfg.get("model_inventory_poll_sec"), 1800.0))
    return max(60.0, float(_env_int("REFINER_AI_MODEL_INVENTORY_POLL_SEC", configured or 1800)))


def _ollama_model_store_path(config_path: Optional[str] = None) -> str:
    ai_cfg = _orchestration_config(config_path)
    configured = str(ai_cfg.get("ollama_models_path") or ai_cfg.get("model_store_path") or "").strip()
    raw = str(os.getenv("OLLAMA_MODELS") or configured).strip()
    if raw:
        return os.path.expanduser(raw)
    return os.path.expanduser("~/.ollama/models")


def _ollama_base_url(config_path: Optional[str] = None, base_url: Optional[str] = None) -> str:
    ai_cfg = _orchestration_config(config_path)
    raw = (
        str(base_url or "").strip()
        or str(os.getenv("OLLAMA_BASE_URL") or "").strip()
        or str(ai_cfg.get("ollama_base_url") or "").strip()
        or "http://localhost:11434"
    )
    return raw.rstrip("/")


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_capability(value: Any) -> Optional[str]:
    cleaned = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned or None


def _normalize_model_name(name: Any) -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        return ""
    return cleaned


def _model_aliases(name: Any) -> Set[str]:
    cleaned = _normalize_model_name(name).lower()
    if not cleaned:
        return set()
    aliases = {cleaned}
    if ":" in cleaned:
        base, _, tag = cleaned.rpartition(":")
        if tag == "latest" and base:
            aliases.add(base)
    else:
        aliases.add(f"{cleaned}:latest")
    return aliases


def _primary_model_key(name: Any) -> str:
    cleaned = _normalize_model_name(name).lower()
    if not cleaned:
        return ""
    if ":" not in cleaned:
        return f"{cleaned}:latest"
    return cleaned


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_parameter_billions(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*b", text)
    if match:
        return _safe_float(match.group(1), 0.0) or None
    numeric = _safe_float(text, -1.0)
    if numeric > 0:
        return numeric
    return None


def _parameter_billions_from_model_name(model: str) -> Optional[float]:
    lowered = _normalize_model_name(model).lower()
    if not lowered:
        return None
    match = re.search(r"(?::|[-_])(\d+(?:\.\d+)?)b\b", lowered)
    if match:
        return _safe_float(match.group(1), 0.0) or None
    match = re.search(r"\b(\d+(?:\.\d+)?)b\b", lowered)
    if match:
        return _safe_float(match.group(1), 0.0) or None
    return None


def _estimate_size_bytes(model: str, *, explicit_size: Optional[int] = None, parameter_billions: Optional[float] = None) -> Optional[int]:
    if explicit_size is not None and explicit_size > 0:
        return int(explicit_size)
    params = parameter_billions if parameter_billions and parameter_billions > 0 else _parameter_billions_from_model_name(model)
    if params is None:
        return None
    return int(max(1.0, params) * _MODEL_BYTES_PER_BILLION_PARAMS)


def _estimate_required_ram_bytes(size_bytes: Optional[int]) -> Optional[int]:
    if size_bytes is None or size_bytes <= 0:
        return None
    overhead_bytes = int(_env_float("REFINER_AI_MODEL_RAM_OVERHEAD_GB", 1.5) * (1024 ** 3))
    factor = max(1.0, _env_float("REFINER_AI_MODEL_RAM_FACTOR", 1.35))
    return int(max(size_bytes * factor, size_bytes + overhead_bytes))


def _infer_capabilities_from_model_name(model: str, configured: Optional[Sequence[str]] = None) -> Set[str]:
    lowered = _normalize_model_name(model).lower()
    capabilities = {
        value
        for value in (_normalize_capability(item) for item in (configured or []))
        if value
    }
    for token, inferred in _MODEL_CAPABILITY_HINTS.items():
        if token in lowered:
            capabilities.update(inferred)
    if not capabilities:
        capabilities.update({"chat"})
    if "vision" in capabilities:
        capabilities.add("chat")
    if "code" in capabilities:
        capabilities.add("reasoning")
    return capabilities


def infer_required_capabilities_from_text(text: str) -> Set[str]:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return set()
    capabilities: Set[str] = set()
    for capability, keywords in _TEXT_CAPABILITY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            capabilities.add(capability)
    return capabilities


def _configured_provider_specs(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = _load_json_file(_default_config_path(config_path))
    ai_cfg = cfg.get("ai_orchestration") if isinstance(cfg.get("ai_orchestration"), dict) else {}
    specs: List[Dict[str, Any]] = []
    for raw in cfg.get("llm_providers") or []:
        if isinstance(raw, dict):
            spec = dict(raw)
            spec.setdefault("_source", "config")
            specs.append(spec)
    for raw in ai_cfg.get("providers") or []:
        if isinstance(raw, dict):
            spec = dict(raw)
            spec.setdefault("_source", "ai_orchestration")
            specs.append(spec)
    return specs


def _configured_model_catalog(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    ai_cfg = _orchestration_config(config_path)
    raw_entries: List[Any] = []
    for key in _MODEL_CATALOG_KEYS:
        value = ai_cfg.get(key)
        if isinstance(value, list):
            raw_entries.extend(value)
    entries: List[Dict[str, Any]] = []
    for raw in raw_entries:
        if isinstance(raw, str):
            entries.append({"model": raw, "_source": "model_catalog"})
        elif isinstance(raw, dict):
            entry = dict(raw)
            entry.setdefault("_source", "model_catalog")
            entries.append(entry)
    return entries


def configured_required_capabilities(config_path: Optional[str] = None) -> List[str]:
    ai_cfg = _orchestration_config(config_path)
    configured = set(
        value
        for value in (
            _normalize_capability(item)
            for item in (
                _as_list(ai_cfg.get("required_capabilities"))
                + _as_list(ai_cfg.get("model_requirements"))
                + _as_list(ai_cfg.get("needs"))
            )
        )
        if value
    )

    for spec in _configured_provider_specs(config_path):
        provider_type = str(spec.get("provider") or spec.get("type") or spec.get("llm_provider") or "").strip().lower()
        if provider_type != "ollama":
            continue
        configured.update(
            value
            for value in (
                _normalize_capability(item)
                for item in (
                    _as_list(spec.get("capabilities"))
                    + _as_list(spec.get("specialties"))
                    + _as_list(spec.get("tags"))
                )
            )
            if value
        )
        roles = {_normalize_capability(item) for item in _as_list(spec.get("roles") or spec.get("role"))}
        for role in roles:
            configured.update(_ROLE_CAPABILITY_HINTS.get(role or "", set()))
        for specialty in list(configured):
            configured.update(_SPECIALTY_CAPABILITY_HINTS.get(specialty, set()))

    if not configured:
        configured.update({"chat", "reasoning"})
    return sorted(configured)


def fetch_ollama_tags(base_url: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    url = f"{str(base_url or '').rstrip('/')}/api/tags"
    start = time.time()
    try:
        resp = requests.get(url, timeout=timeout or _env_int("LLM_TIMEOUT_SECONDS", 10))
        latency_ms = int((time.time() - start) * 1000)
        if resp.status_code >= 300:
            return {
                "ok": False,
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
                "message": resp.text[:200],
                "models": [],
            }
        data = resp.json() if resp.content else {}
        raw_models = data.get("models") if isinstance(data, dict) else []
        models: List[Dict[str, Any]] = []
        for raw in raw_models or []:
            if not isinstance(raw, dict):
                continue
            model_name = _normalize_model_name(raw.get("name") or raw.get("model"))
            if not model_name:
                continue
            details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
            parameter_billions = (
                _parse_parameter_billions(details.get("parameter_size"))
                or _parameter_billions_from_model_name(model_name)
            )
            size_bytes = _safe_int(raw.get("size"), 0) or _estimate_size_bytes(
                model_name,
                parameter_billions=parameter_billions,
            )
            capabilities = _infer_capabilities_from_model_name(model_name)
            models.append(
                {
                    "model": model_name,
                    "installed": True,
                    "aliases": sorted(_model_aliases(model_name)),
                    "size_bytes": int(size_bytes) if size_bytes else None,
                    "parameter_billions": parameter_billions,
                    "capabilities": sorted(capabilities),
                    "family": details.get("family"),
                    "families": _as_list(details.get("families")),
                    "quantization": str(details.get("quantization_level") or "").strip() or None,
                    "modified_at": raw.get("modified_at"),
                    "digest": raw.get("digest"),
                }
            )
        return {
            "ok": True,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "message": "ok",
            "models": models,
        }
    except Exception as exc:
        latency_ms = int((time.time() - start) * 1000)
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": latency_ms,
            "message": str(exc),
            "models": [],
        }


def host_resource_snapshot(model_store_path: Optional[str] = None) -> Dict[str, Any]:
    path = os.path.expanduser(model_store_path or _ollama_model_store_path())

    total_bytes = None
    available_bytes = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            meminfo = {}
            for line in handle:
                parts = line.split(":", 1)
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                match = re.search(r"(\d+)", parts[1])
                if match:
                    meminfo[key] = int(match.group(1)) * 1024
        total_bytes = meminfo.get("MemTotal")
        available_bytes = meminfo.get("MemAvailable") or meminfo.get("MemFree")
    except Exception:
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            avail_pages = os.sysconf("SC_AVPHYS_PAGES")
            total_bytes = int(page_size * phys_pages)
            available_bytes = int(page_size * avail_pages)
        except Exception:
            total_bytes = None
            available_bytes = None

    disk_target = path if os.path.exists(path) else os.path.dirname(path) or "."
    try:
        disk_usage = shutil.disk_usage(disk_target)
        disk_total = int(disk_usage.total)
        disk_free = int(disk_usage.free)
    except Exception:
        disk_total = None
        disk_free = None

    gpu: Dict[str, Any] = {"available": False, "total_bytes": None, "free_bytes": None, "device_count": 0, "probed": False}
    if _env_bool("REFINER_AI_MODEL_GPU_PROBE", True):
        binary = shutil.which("nvidia-smi")
        if binary:
            gpu["probed"] = True
            try:
                result = subprocess.run(
                    [
                        binary,
                        "--query-gpu=memory.total,memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                )
                if result.returncode == 0:
                    total_mb = 0
                    free_mb = 0
                    count = 0
                    for line in (result.stdout or "").splitlines():
                        bits = [part.strip() for part in line.split(",")]
                        if len(bits) < 2:
                            continue
                        total_mb += _safe_int(bits[0], 0)
                        free_mb += _safe_int(bits[1], 0)
                        count += 1
                    if count > 0:
                        gpu.update(
                            {
                                "available": True,
                                "device_count": count,
                                "total_bytes": total_mb * 1024 * 1024,
                                "free_bytes": free_mb * 1024 * 1024,
                            }
                        )
            except Exception:
                pass

    memory_budget_fraction = min(0.98, max(0.1, _env_float("REFINER_AI_MODEL_MEMORY_BUDGET_FRACTION", 0.82)))
    disk_budget_fraction = min(0.98, max(0.1, _env_float("REFINER_AI_MODEL_DISK_BUDGET_FRACTION", 0.80)))
    return {
        "memory": {
            "total_bytes": total_bytes,
            "available_bytes": available_bytes,
            "budget_bytes": int(available_bytes * memory_budget_fraction) if available_bytes is not None else None,
        },
        "disk": {
            "path": disk_target,
            "total_bytes": disk_total,
            "free_bytes": disk_free,
            "budget_bytes": int(disk_free * disk_budget_fraction) if disk_free is not None else None,
        },
        "gpu": gpu,
    }


def _candidate_from_spec(spec: Dict[str, Any], source_default: str) -> Optional[Dict[str, Any]]:
    model_name = _normalize_model_name(spec.get("model") or spec.get("name"))
    if not model_name:
        return None
    capabilities = _as_list(spec.get("capabilities"))
    if not capabilities:
        capabilities = (
            _as_list(spec.get("specialties"))
            + _as_list(spec.get("tags"))
        )
    parameter_billions = (
        _parse_parameter_billions(spec.get("parameter_size"))
        or _parameter_billions_from_model_name(model_name)
    )
    size_bytes = _safe_int(spec.get("size_bytes"), 0) or _estimate_size_bytes(
        model_name,
        parameter_billions=parameter_billions,
    )
    required_ram_bytes = _safe_int(spec.get("required_ram_bytes"), 0) or _estimate_required_ram_bytes(size_bytes)
    modality = _normalize_capability(spec.get("modality")) or ("vision" if "vision" in _infer_capabilities_from_model_name(model_name, capabilities) else "text")
    return {
        "model": model_name,
        "source": str(spec.get("_source") or spec.get("source") or source_default).strip() or source_default,
        "capabilities": sorted(_infer_capabilities_from_model_name(model_name, capabilities)),
        "parameter_billions": parameter_billions,
        "size_bytes": size_bytes,
        "required_ram_bytes": required_ram_bytes,
        "modality": modality,
        "weight": _safe_float(spec.get("weight"), 0.0),
    }


def configured_candidate_models(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for spec in _configured_provider_specs(config_path):
        provider_type = str(spec.get("provider") or spec.get("type") or spec.get("llm_provider") or "").strip().lower()
        if provider_type != "ollama":
            continue
        candidate = _candidate_from_spec(spec, "configured_provider")
        if candidate is not None:
            candidates.append(candidate)
    for spec in _configured_model_catalog(config_path):
        candidate = _candidate_from_spec(spec, "model_catalog")
        if candidate is not None:
            candidates.append(candidate)
    for key in ("OLLAMA_MODEL", "OLLAMA_DEFAULT_MODEL", "SOLVER_OLLAMA_MODEL"):
        model_name = _normalize_model_name(os.getenv(key))
        if model_name:
            candidates.append(
                {
                    "model": model_name,
                    "source": f"env:{key.lower()}",
                    "capabilities": sorted(_infer_capabilities_from_model_name(model_name)),
                    "parameter_billions": _parameter_billions_from_model_name(model_name),
                    "size_bytes": _estimate_size_bytes(model_name),
                    "required_ram_bytes": _estimate_required_ram_bytes(_estimate_size_bytes(model_name)),
                    "modality": "vision" if "vision" in _infer_capabilities_from_model_name(model_name) else "text",
                    "weight": 0.1,
                }
            )
    return candidates


def _merge_candidate_sources(candidates: Sequence[Dict[str, Any]], installed: Sequence[Dict[str, Any]], required_capabilities: Sequence[str]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    installed_lookup: Dict[str, Dict[str, Any]] = {}
    for item in installed:
        if not isinstance(item, dict):
            continue
        for alias in _model_aliases(item.get("model")):
            installed_lookup[alias] = dict(item)
    for candidate in list(candidates) + list(installed):
        if not isinstance(candidate, dict):
            continue
        model_name = _normalize_model_name(candidate.get("model"))
        if not model_name:
            continue
        primary_key = _primary_model_key(model_name)
        if not primary_key:
            continue
        entry = merged.setdefault(
            primary_key,
            {
                "model": model_name,
                "sources": [],
                "capabilities": [],
                "size_bytes": None,
                "required_ram_bytes": None,
                "parameter_billions": None,
                "modality": None,
                "weight": 0.0,
                "installed": False,
            },
        )
        source = str(candidate.get("source") or ("installed" if candidate.get("installed") else "candidate")).strip()
        if source and source not in entry["sources"]:
            entry["sources"].append(source)
        capabilities = set(entry.get("capabilities") or [])
        capabilities.update(_as_list(candidate.get("capabilities")))
        entry["capabilities"] = sorted(
            _infer_capabilities_from_model_name(
                model_name,
                configured=list(capabilities),
            )
        )
        entry["weight"] = max(_safe_float(entry.get("weight"), 0.0), _safe_float(candidate.get("weight"), 0.0))
        if candidate.get("size_bytes"):
            entry["size_bytes"] = candidate.get("size_bytes")
        if candidate.get("required_ram_bytes"):
            entry["required_ram_bytes"] = candidate.get("required_ram_bytes")
        if candidate.get("parameter_billions"):
            entry["parameter_billions"] = candidate.get("parameter_billions")
        if candidate.get("modality"):
            entry["modality"] = candidate.get("modality")
        if candidate.get("installed"):
            entry["installed"] = True

        installed_match = None
        for alias in _model_aliases(model_name):
            if alias in installed_lookup:
                installed_match = installed_lookup[alias]
                break
        if installed_match:
            entry["installed"] = True
            if "installed" not in entry["sources"]:
                entry["sources"].append("installed")
            if installed_match.get("size_bytes"):
                entry["size_bytes"] = installed_match.get("size_bytes")
            if installed_match.get("parameter_billions"):
                entry["parameter_billions"] = installed_match.get("parameter_billions")
            if installed_match.get("family"):
                entry["family"] = installed_match.get("family")
            if installed_match.get("families"):
                entry["families"] = installed_match.get("families")
            if installed_match.get("quantization"):
                entry["quantization"] = installed_match.get("quantization")
            if installed_match.get("modified_at"):
                entry["modified_at"] = installed_match.get("modified_at")
            if installed_match.get("digest"):
                entry["digest"] = installed_match.get("digest")

        size_bytes = _safe_int(entry.get("size_bytes"), 0) or _estimate_size_bytes(
            model_name,
            explicit_size=entry.get("size_bytes"),
            parameter_billions=_safe_float(entry.get("parameter_billions"), 0.0) or None,
        )
        entry["size_bytes"] = size_bytes
        entry["required_ram_bytes"] = _safe_int(entry.get("required_ram_bytes"), 0) or _estimate_required_ram_bytes(size_bytes)
        if not entry.get("modality"):
            entry["modality"] = "vision" if "vision" in set(entry["capabilities"]) else "text"
        needed = {_normalize_capability(item) for item in required_capabilities if _normalize_capability(item)}
        overlap = sorted(value for value in needed if value in set(entry["capabilities"]))
        entry["matched_capabilities"] = overlap

    return list(merged.values())


def _score_candidate(entry: Dict[str, Any], required_capabilities: Sequence[str], resources: Dict[str, Any]) -> Dict[str, Any]:
    needed = {
        value
        for value in (_normalize_capability(item) for item in required_capabilities)
        if value
    }
    capabilities = {
        value
        for value in (_normalize_capability(item) for item in entry.get("capabilities") or [])
        if value
    }
    memory_budget = _safe_int(resources.get("memory", {}).get("budget_bytes"), 0) or None
    disk_budget = _safe_int(resources.get("disk", {}).get("budget_bytes"), 0) or None
    required_ram_bytes = _safe_int(entry.get("required_ram_bytes"), 0) or None
    size_bytes = _safe_int(entry.get("size_bytes"), 0) or None
    fits_memory = None if memory_budget is None or required_ram_bytes is None else required_ram_bytes <= memory_budget
    fits_disk = True if entry.get("installed") else None if disk_budget is None or size_bytes is None else size_bytes <= disk_budget
    overlap = sorted(value for value in needed if value in capabilities)
    relevance_score = float(len(overlap) * 4)
    if "chat" in capabilities and "chat" in needed:
        relevance_score += 1.0
    if "vision" in capabilities and "vision" in needed:
        relevance_score += 1.5
    if entry.get("installed"):
        relevance_score += 2.5
    if fits_memory is True:
        relevance_score += 1.0
    if fits_disk is True:
        relevance_score += 0.5
    if fits_memory is False:
        relevance_score -= 9.0
    if fits_disk is False:
        relevance_score -= 7.0
    relevance_score += _safe_float(entry.get("weight"), 0.0)

    if entry.get("installed") and fits_memory is not False:
        fit_status = "ready"
    elif not entry.get("installed") and fits_memory is False:
        fit_status = "blocked_memory"
    elif not entry.get("installed") and fits_disk is False:
        fit_status = "blocked_disk"
    elif not entry.get("installed") and overlap and fits_memory is not False and fits_disk is not False:
        fit_status = "download_candidate"
    elif entry.get("installed") and fits_memory is False:
        fit_status = "installed_but_too_large"
    else:
        fit_status = "not_relevant"

    recommended_download = bool(
        not entry.get("installed")
        and fit_status == "download_candidate"
    )
    runtime_ready = bool(entry.get("installed") and fits_memory is not False)

    scored = dict(entry)
    scored["fits_memory"] = fits_memory
    scored["fits_disk"] = fits_disk
    scored["relevance_score"] = round(relevance_score, 3)
    scored["matched_capabilities"] = overlap
    scored["fit_status"] = fit_status
    scored["download_recommended"] = recommended_download
    scored["runtime_ready"] = runtime_ready
    return scored


def build_model_inventory_snapshot(
    config_path: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    config_file = _default_config_path(config_path)
    base_url_value = _ollama_base_url(config_file, base_url)
    resources = host_resource_snapshot(_ollama_model_store_path(config_file))
    required_capabilities = configured_required_capabilities(config_file)
    configured_candidates = configured_candidate_models(config_file)
    for capability in required_capabilities:
        for hint in _DEFAULT_MODEL_HINTS.get(capability, []):
            configured_candidates.append(
                {
                    "model": hint,
                    "source": f"default_hint:{capability}",
                    "capabilities": sorted(_infer_capabilities_from_model_name(hint, [capability])),
                    "parameter_billions": _parameter_billions_from_model_name(hint),
                    "size_bytes": _estimate_size_bytes(hint),
                    "required_ram_bytes": _estimate_required_ram_bytes(_estimate_size_bytes(hint)),
                    "modality": "vision" if capability == "vision" else "text",
                    "weight": 0.0,
                }
            )

    ollama_status = fetch_ollama_tags(base_url_value, timeout=_env_int("REFINER_AI_MODEL_INVENTORY_TIMEOUT_SECONDS", 10))
    installed_models = ollama_status.get("models") if isinstance(ollama_status.get("models"), list) else []
    merged = _merge_candidate_sources(configured_candidates, installed_models, required_capabilities)
    scored = [_score_candidate(item, required_capabilities, resources) for item in merged]
    scored.sort(
        key=lambda item: (
            1 if item.get("runtime_ready") else 0,
            1 if item.get("download_recommended") else 0,
            _safe_float(item.get("relevance_score"), 0.0),
            -(item.get("size_bytes") if item.get("size_bytes") is not None else 10 ** 18),
            item.get("model") or "",
        ),
        reverse=True,
    )
    counts = {
        "total_models": len(scored),
        "installed_models": sum(1 for item in scored if item.get("installed")),
        "ready_models": sum(1 for item in scored if item.get("runtime_ready")),
        "download_candidates": sum(1 for item in scored if item.get("download_recommended")),
        "blocked_memory": sum(1 for item in scored if item.get("fit_status") in {"blocked_memory", "installed_but_too_large"}),
        "blocked_disk": sum(1 for item in scored if item.get("fit_status") == "blocked_disk"),
    }
    download_shortlist = [item for item in scored if item.get("download_recommended")][:8]
    snapshot = {
        "enabled": inventory_enabled(config_file),
        "config_path": config_file,
        "path": inventory_path(config_file),
        "generated_at": _now_iso(),
        "generated_ts": time.time(),
        "poll_sec": inventory_poll_seconds(config_file),
        "provider": {
            "name": "ollama",
            "base_url": base_url_value,
            "reachable": bool(ollama_status.get("ok")),
            "status_code": ollama_status.get("status_code"),
            "latency_ms": ollama_status.get("latency_ms"),
            "message": ollama_status.get("message"),
            "auto_pull_guard": not _env_bool("OLLAMA_ALLOW_AUTO_PULL", False),
        },
        "resources": resources,
        "required_capabilities": required_capabilities,
        "counts": counts,
        "models": scored,
        "download_shortlist": download_shortlist,
    }
    return snapshot


def refresh_model_inventory_cache(
    config_path: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot = build_model_inventory_snapshot(config_path, base_url=base_url)
    path = inventory_path(config_path)
    snapshot["path"] = path
    _safe_write_json(path, snapshot)
    with _SNAPSHOT_CACHE_LOCK:
        global _SNAPSHOT_CACHE_PATH, _SNAPSHOT_CACHE_MTIME, _SNAPSHOT_CACHE_VALUE
        _SNAPSHOT_CACHE_PATH = path
        try:
            _SNAPSHOT_CACHE_MTIME = os.path.getmtime(path)
        except Exception:
            _SNAPSHOT_CACHE_MTIME = None
        _SNAPSHOT_CACHE_VALUE = json.loads(json.dumps(snapshot))
    return snapshot


def load_model_inventory_snapshot(config_path: Optional[str] = None) -> Dict[str, Any]:
    path = inventory_path(config_path)
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return {}
    with _SNAPSHOT_CACHE_LOCK:
        global _SNAPSHOT_CACHE_PATH, _SNAPSHOT_CACHE_MTIME, _SNAPSHOT_CACHE_VALUE
        if _SNAPSHOT_CACHE_PATH == path and _SNAPSHOT_CACHE_MTIME == mtime and isinstance(_SNAPSHOT_CACHE_VALUE, dict):
            return json.loads(json.dumps(_SNAPSHOT_CACHE_VALUE))
    data = _load_json_file(path)
    if not data:
        return {}
    with _SNAPSHOT_CACHE_LOCK:
        _SNAPSHOT_CACHE_PATH = path
        _SNAPSHOT_CACHE_MTIME = mtime
        _SNAPSHOT_CACHE_VALUE = json.loads(json.dumps(data))
    return data


def _snapshot_stale(snapshot: Dict[str, Any], poll_sec: float) -> bool:
    generated_ts = _safe_float(snapshot.get("generated_ts"), 0.0)
    if generated_ts <= 0:
        generated_at = str(snapshot.get("generated_at") or "").strip()
        if not generated_at:
            return True
        return True
    return (time.time() - generated_ts) > max(60.0, poll_sec * 2.0)


def model_inventory_status(
    config_path: Optional[str] = None,
    *,
    limit: int = 20,
    refresh_if_missing: bool = False,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    config_file = _default_config_path(config_path)
    snapshot = load_model_inventory_snapshot(config_file)
    if (not snapshot) and refresh_if_missing and inventory_enabled(config_file):
        try:
            snapshot = refresh_model_inventory_cache(config_file, base_url=base_url)
        except Exception as exc:
            logger.debug("Model inventory refresh failed during status read: %s", exc)
            snapshot = {}
    if not snapshot:
        return {
            "enabled": inventory_enabled(config_file),
            "path": inventory_path(config_file),
            "config_path": config_file,
            "generated_at": None,
            "stale": True,
            "provider": {
                "name": "ollama",
                "base_url": _ollama_base_url(config_file, base_url),
                "reachable": False,
                "auto_pull_guard": not _env_bool("OLLAMA_ALLOW_AUTO_PULL", False),
            },
            "resources": host_resource_snapshot(_ollama_model_store_path(config_file)),
            "required_capabilities": configured_required_capabilities(config_file),
            "counts": {
                "total_models": 0,
                "installed_models": 0,
                "ready_models": 0,
                "download_candidates": 0,
                "blocked_memory": 0,
                "blocked_disk": 0,
            },
            "models": [],
            "download_shortlist": [],
        }
    models = snapshot.get("models") if isinstance(snapshot.get("models"), list) else []
    shortlist = snapshot.get("download_shortlist") if isinstance(snapshot.get("download_shortlist"), list) else []
    poll_sec = _safe_float(snapshot.get("poll_sec"), inventory_poll_seconds(config_file))
    payload = json.loads(json.dumps(snapshot))
    payload["stale"] = _snapshot_stale(snapshot, poll_sec)
    payload["models"] = models[: max(1, int(limit))]
    payload["download_shortlist"] = shortlist[: min(max(1, int(limit)), 12)]
    return payload


def resolve_ollama_model_for_request(
    requested_model: str,
    *,
    prompt_text: str,
    config_path: Optional[str] = None,
    base_url: Optional[str] = None,
    allow_auto_pull: Optional[bool] = None,
) -> Dict[str, Any]:
    config_file = _default_config_path(config_path)
    requested = _normalize_model_name(requested_model)
    needs = infer_required_capabilities_from_text(prompt_text)
    needs.update(_infer_capabilities_from_model_name(requested))
    snapshot = load_model_inventory_snapshot(config_file)
    if not snapshot or _snapshot_stale(snapshot, inventory_poll_seconds(config_file)) or str(snapshot.get("provider", {}).get("base_url") or "").rstrip("/") != _ollama_base_url(config_file, base_url):
        snapshot = refresh_model_inventory_cache(config_file, base_url=base_url)
    models = snapshot.get("models") if isinstance(snapshot.get("models"), list) else []
    allow_pull = _env_bool("OLLAMA_ALLOW_AUTO_PULL", False) if allow_auto_pull is None else bool(allow_auto_pull)

    def _matches_requested(item: Dict[str, Any]) -> bool:
        aliases = set(_as_list(item.get("aliases")))
        if not aliases:
            aliases = set(_model_aliases(item.get("model")))
        return bool(_model_aliases(requested) & aliases)

    requested_entry = next((item for item in models if _matches_requested(item)), None)
    if requested_entry and requested_entry.get("runtime_ready"):
        return {
            "selected_model": requested_entry.get("model") or requested,
            "requested_model": requested,
            "reason": "requested_model_ready",
            "required_capabilities": sorted(needs),
            "recommended_downloads": [],
        }

    installed_ready = [item for item in models if item.get("runtime_ready")]
    installed_ready.sort(
        key=lambda item: (
            len(set(item.get("matched_capabilities") or []) & needs),
            _safe_float(item.get("relevance_score"), 0.0),
            -(item.get("size_bytes") if item.get("size_bytes") is not None else 10 ** 18),
            item.get("model") or "",
        ),
        reverse=True,
    )
    if installed_ready:
        best = installed_ready[0]
        if best.get("model"):
            reason = "requested_model_missing"
            if requested_entry and requested_entry.get("fit_status") == "installed_but_too_large":
                reason = "requested_model_too_large"
            elif requested_entry and requested_entry.get("installed"):
                reason = "requested_model_not_runtime_ready"
            return {
                "selected_model": best.get("model"),
                "requested_model": requested,
                "reason": reason,
                "required_capabilities": sorted(needs),
                "recommended_downloads": [],
            }

    recommended_downloads = [
        item.get("model")
        for item in models
        if item.get("download_recommended")
        and (set(item.get("matched_capabilities") or []) & needs)
    ][:3]
    if allow_pull:
        return {
            "selected_model": requested,
            "requested_model": requested,
            "reason": "auto_pull_allowed",
            "required_capabilities": sorted(needs),
            "recommended_downloads": recommended_downloads,
        }
    return {
        "selected_model": None,
        "requested_model": requested,
        "reason": "no_safe_local_model",
        "required_capabilities": sorted(needs),
        "recommended_downloads": recommended_downloads,
    }


class AIModelInventoryMonitor:
    """Background monitor that refreshes the local-model inventory snapshot."""

    def __init__(
        self,
        *,
        config_path: Optional[str] = None,
        base_url: Optional[str] = None,
        poll_sec: Optional[float] = None,
    ) -> None:
        self.config_path = _default_config_path(config_path)
        self.base_url = base_url
        self.poll_sec = max(60.0, float(poll_sec or inventory_poll_seconds(self.config_path)))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_run_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_summary: Dict[str, Any] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not inventory_enabled(self.config_path):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="ai-model-inventory-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, float(timeout)))

    def status(self) -> Dict[str, Any]:
        thread = self._thread
        with self._lock:
            return {
                "enabled": inventory_enabled(self.config_path),
                "running": bool(thread and thread.is_alive()),
                "poll_sec": self.poll_sec,
                "path": inventory_path(self.config_path),
                "last_run_at": self._last_run_at,
                "last_error": self._last_error,
                "last_summary": json.loads(json.dumps(self._last_summary)),
            }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("AI model inventory monitor cycle failed")
            self._stop_event.wait(self.poll_sec)

    def run_once(self) -> Dict[str, Any]:
        error: Optional[str] = None
        summary: Dict[str, Any] = {}
        try:
            snapshot = refresh_model_inventory_cache(self.config_path, base_url=self.base_url)
            provider = snapshot.get("provider") if isinstance(snapshot.get("provider"), dict) else {}
            counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
            summary = {
                "reachable": bool(provider.get("reachable")),
                "ready_models": counts.get("ready_models"),
                "download_candidates": counts.get("download_candidates"),
                "blocked_memory": counts.get("blocked_memory"),
                "blocked_disk": counts.get("blocked_disk"),
            }
        except Exception as exc:
            error = str(exc)
            with self._lock:
                self._last_run_at = _now_iso()
                self._last_error = error
                self._last_summary = {}
            raise
        with self._lock:
            self._last_run_at = _now_iso()
            self._last_error = error
            self._last_summary = summary
        return summary
