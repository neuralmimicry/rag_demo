"""AARNN neuromorphic engine adapter for Refiner orchestration.

The adapter serves two purposes:

1. expose a lightweight, always-available specialist engine profile for
   neuromorphic/AER-aware workflow routing, and
2. talk to a live `aarnn_rust` runtime when either its HTTP inference API or
   Unix datagram AER socket is available.

When no live runtime is reachable the adapter still provides deterministic
offline heuristics plus AER payload generation, so Refiner can continue to
plan or document SNN/AARNN tasks without degrading into placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import socket
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

import requests

from refiner.refiner_ai_aer import decode_spikes, decode_spikes_auto, encode_spikes, payload_hex, spikes_from_floats


logger = logging.getLogger(__name__)

DEFAULT_AARNN_REPO_ROOT = "/home/pbisaacs/Developer/neuralmimicry/aarnn_rust"
DEFAULT_AER_SENSORY_BASE = 4096
DEFAULT_AER_OUTPUT_BASE = 16384
DEFAULT_SOCKET_PATH = "/tmp/aarnn_rust.nn"
NEUROMORPHIC_KEYWORDS = (
    "aarnn",
    "aer",
    "spiking neural",
    "spiking-neural",
    "spike train",
    "snn",
    "neuromorphic",
    "celegans",
    "drosophila",
)

DEFAULT_SPECIALTIES = ("aarnn", "snn", "neuromorphic", "aer")
AARNN_GUIDANCE_LINES = (
    "Prefer AARNN-grown SNN designs when the task explicitly calls for spiking or neuromorphic networks.",
    "Use `AER1` payloads for spike exchange with the AARNN UDS/runtime path.",
)
GENERIC_AER_GUIDANCE_LINES = (
    "Prefer spike-native or neuromorphic designs when the task explicitly calls for SNN/AER systems.",
    "Use `AER1` payloads when the attached runtime or translation layer expects AER-based communication.",
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


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalized_tags(value: Any) -> List[str]:
    return [item.lower() for item in _as_list(value)]


def _load_json_file(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalized_url(raw: Optional[str]) -> Optional[str]:
    value = str(raw or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = f"http://{value}"
    return value.rstrip("/")


def _keyword_hits(text: str, keywords: Optional[Sequence[str]] = None) -> Dict[str, int]:
    lowered = str(text or "").lower()
    hits: Dict[str, int] = {}
    configured_keywords = tuple(str(keyword).strip().lower() for keyword in (keywords or NEUROMORPHIC_KEYWORDS) if str(keyword).strip())
    for keyword in configured_keywords:
        count = lowered.count(keyword)
        if count:
            hits[keyword] = count
    return hits


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


def _is_aarnn_kind(kind: Optional[str], name: Optional[str] = None, specialties: Optional[Sequence[str]] = None) -> bool:
    kind_value = str(kind or "").strip().lower()
    name_value = str(name or "").strip().lower()
    specialty_values = {str(item).strip().lower() for item in (specialties or []) if str(item).strip()}
    return kind_value == "aarnn" or "aarnn" in name_value or "aarnn" in specialty_values


def _engine_options_from_spec(
    spec: Dict[str, Any],
    *,
    apply_env_overrides: bool = False,
    default_name: str = "AARNN",
) -> Dict[str, Any]:
    engine_cfg = dict(spec or {})
    roles = _normalized_tags(engine_cfg.get("roles") or engine_cfg.get("role"))
    specialties = _normalized_tags(engine_cfg.get("specialties") or engine_cfg.get("tags"))
    engine_name = str(engine_cfg.get("name") or engine_cfg.get("display_name") or default_name).strip() or default_name
    raw_engine_type = str(engine_cfg.get("type") or engine_cfg.get("engine") or "").strip().lower()
    keyword_hints = _normalized_tags(engine_cfg.get("keywords") or engine_cfg.get("keyword_hints"))
    guidance_lines = _as_list(engine_cfg.get("guidance_lines") or engine_cfg.get("prompt_guidance"))
    use_env_overrides = bool(apply_env_overrides or _is_truthy(engine_cfg.get("use_env_overrides")))
    is_aarnn = _is_aarnn_kind(raw_engine_type or None, engine_name, specialties)
    engine_type = raw_engine_type or ("aarnn" if is_aarnn else "snn_aer")
    resolved_specialties = specialties or ([*DEFAULT_SPECIALTIES] if is_aarnn else ["snn", "neuromorphic", "aer"])
    resolved_keywords = keyword_hints or list(dict.fromkeys([*resolved_specialties, engine_name, engine_type]))
    resolved_guidance = guidance_lines or [*(AARNN_GUIDANCE_LINES if is_aarnn else GENERIC_AER_GUIDANCE_LINES)]

    def _spec_value(env_name: str, *keys: str, default: Any = None) -> Any:
        if use_env_overrides:
            env_value = os.getenv(env_name)
            if env_value not in {None, ""}:
                return env_value
        for key in keys:
            value = engine_cfg.get(key)
            if value not in {None, ""}:
                return value
        return default

    enabled = bool(engine_cfg.get("enabled", True))
    if is_aarnn and use_env_overrides and str(os.getenv("REFINER_AARNN_ENABLED", "1")).strip().lower() in {"0", "false", "no", "off"}:
        enabled = False

    return {
        "enabled": enabled,
        "engine_cfg": engine_cfg,
        "name": engine_name,
        "engine_type": engine_type,
        "repo_root": _spec_value("REFINER_AARNN_REPO_ROOT", "repo_root", "path", default=DEFAULT_AARNN_REPO_ROOT if is_aarnn else None),
        "endpoint": _spec_value("REFINER_AARNN_ENDPOINT", "endpoint", "url"),
        "socket_path": _spec_value("REFINER_AARNN_SOCKET", "socket_path", "socket", default=DEFAULT_SOCKET_PATH if is_aarnn else None),
        "sensory_size": _spec_value("REFINER_AARNN_SENSORY_SIZE", "sensory_size", "expected_s", default=32),
        "output_size": _spec_value("REFINER_AARNN_OUTPUT_SIZE", "output_size", "expected_o", default=16),
        "aer_sensory_base": _spec_value("REFINER_AARNN_AER_SENSORY_BASE", "aer_sensory_base", default=DEFAULT_AER_SENSORY_BASE),
        "aer_output_base": _spec_value("REFINER_AARNN_AER_OUTPUT_BASE", "aer_output_base", default=DEFAULT_AER_OUTPUT_BASE),
        "timeout": _spec_value("REFINER_AARNN_TIMEOUT", "timeout", default=2.0),
        "spike_threshold": _spec_value("REFINER_AARNN_SPIKE_THRESHOLD", "spike_threshold", default=0.5),
        "health_ttl_sec": float(engine_cfg.get("health_ttl_sec") or 300.0),
        "roles": roles,
        "specialties": resolved_specialties,
        "keyword_hints": resolved_keywords,
        "guidance_lines": resolved_guidance,
        "description": str(engine_cfg.get("description") or "").strip() or None,
        "weight": _safe_float(engine_cfg.get("weight"), 0.0),
        "prefer_aarnn_designs": bool(engine_cfg.get("prefer_aarnn_designs", is_aarnn)),
        "source": str(engine_cfg.get("_source") or engine_cfg.get("source") or "config").strip() or "config",
    }


def _configured_engine_options(config_path: Optional[str] = None) -> Dict[str, Any]:
    cfg = _load_json_file(config_path or os.getenv("REFINER_CONFIG_PATH") or "config.json")
    orchestration_cfg = cfg.get("ai_orchestration") if isinstance(cfg.get("ai_orchestration"), dict) else {}
    engines = orchestration_cfg.get("engines") if isinstance(orchestration_cfg.get("engines"), list) else []
    engine_cfg: Dict[str, Any] = {}
    for item in engines:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("engine") or "").strip().lower()
        name = str(item.get("name") or "").strip().lower()
        if kind == "aarnn" or "aarnn" in name:
            engine_cfg = dict(item)
            break
    return _engine_options_from_spec(engine_cfg, apply_env_overrides=True, default_name="AARNN")


@dataclass
class AarnnPrediction:
    """Normalized prediction result from any AARNN transport mode."""

    score: float
    fired: bool
    mode: str
    threshold: float
    input_spikes: List[int]
    output_spikes: List[int]
    aer_payload_hex: str
    raw: Dict[str, Any]


class AarnnEngine:
    """Adapter that can talk to a live AARNN runtime or fall back locally."""

    def __init__(
        self,
        *,
        repo_root: Optional[str] = None,
        endpoint: Optional[str] = None,
        socket_path: Optional[str] = None,
        sensory_size: int = 32,
        output_size: int = 16,
        aer_sensory_base: int = DEFAULT_AER_SENSORY_BASE,
        aer_output_base: int = DEFAULT_AER_OUTPUT_BASE,
        spike_threshold: float = 0.5,
        timeout: float = 2.0,
        health_ttl_sec: float = 300.0,
        name: str = "AARNN",
        engine_type: str = "aarnn",
        roles: Optional[Sequence[str]] = None,
        specialties: Optional[Sequence[str]] = None,
        keyword_hints: Optional[Sequence[str]] = None,
        guidance_lines: Optional[Sequence[str]] = None,
        description: Optional[str] = None,
        weight: float = 0.0,
        prefer_aarnn_designs: bool = True,
    ) -> None:
        self.repo_root = str(repo_root or "").strip() or None
        self.endpoint = _normalized_url(endpoint)
        self.socket_path = str(socket_path or "").strip() or None
        self.sensory_size = max(1, int(sensory_size))
        self.output_size = max(1, int(output_size))
        self.aer_sensory_base = int(aer_sensory_base)
        self.aer_output_base = int(aer_output_base)
        self.spike_threshold = float(spike_threshold)
        self.timeout = max(0.2, float(timeout))
        self.health_ttl_sec = max(5.0, float(health_ttl_sec))
        self.name = str(name or "AARNN").strip() or "AARNN"
        self.engine_type = str(engine_type or "aarnn").strip().lower() or "aarnn"
        self.roles = _normalized_tags(roles)
        self.specialties = _normalized_tags(specialties) or [*DEFAULT_SPECIALTIES]
        self.keyword_hints = _normalized_tags(keyword_hints) or list(dict.fromkeys([*self.specialties, self.name, self.engine_type]))
        self.guidance_lines = _as_list(guidance_lines) or [*AARNN_GUIDANCE_LINES]
        self.description = str(description or "").strip() or None
        self.weight = float(weight or 0.0)
        self.prefer_aarnn_designs = bool(prefer_aarnn_designs)
        self._health_cache: Optional[Dict[str, Any]] = None
        self._health_cache_at = 0.0

    @classmethod
    def from_env_or_config(cls, config_path: Optional[str] = None) -> Optional["AarnnEngine"]:
        """Build an engine from config/env, returning `None` when disabled."""

        options = _configured_engine_options(config_path)
        if not options.get("enabled", True):
            return None

        return cls.from_options(options)

    @classmethod
    def from_options(cls, options: Dict[str, Any]) -> Optional["AarnnEngine"]:
        if not options.get("enabled", True):
            return None
        engine = cls(
            repo_root=str(options.get("repo_root") or "").strip() or None,
            endpoint=options.get("endpoint"),
            socket_path=str(options.get("socket_path") or "").strip() or None,
            sensory_size=int(options.get("sensory_size") or 32),
            output_size=int(options.get("output_size") or 16),
            aer_sensory_base=int(options.get("aer_sensory_base") or DEFAULT_AER_SENSORY_BASE),
            aer_output_base=int(options.get("aer_output_base") or DEFAULT_AER_OUTPUT_BASE),
            timeout=float(options.get("timeout") or 2.0),
            spike_threshold=float(options.get("spike_threshold") or 0.5),
            health_ttl_sec=float(options.get("health_ttl_sec") or 300.0),
            name=str(options.get("name") or "AARNN").strip() or "AARNN",
            engine_type=str(options.get("engine_type") or "aarnn").strip().lower() or "aarnn",
            roles=options.get("roles"),
            specialties=options.get("specialties"),
            keyword_hints=options.get("keyword_hints"),
            guidance_lines=options.get("guidance_lines"),
            description=options.get("description"),
            weight=_safe_float(options.get("weight"), 0.0),
            prefer_aarnn_designs=bool(options.get("prefer_aarnn_designs", True)),
        )
        if not engine.is_available():
            return None
        return engine

    @classmethod
    def from_spec(
        cls,
        spec: Dict[str, Any],
        *,
        apply_env_overrides: bool = False,
        default_name: str = "AARNN",
    ) -> Optional["AarnnEngine"]:
        options = _engine_options_from_spec(
            spec,
            apply_env_overrides=apply_env_overrides,
            default_name=default_name,
        )
        return cls.from_options(options)

    def is_available(self) -> bool:
        """Return `True` when some AARNN mode is locally available."""

        if self.endpoint:
            return True
        if self.socket_path:
            return True
        return bool(self.repo_root and os.path.exists(self.repo_root))

    def _socket_handshake(self) -> Dict[str, Any]:
        if not self.socket_path:
            raise FileNotFoundError("AARNN socket path is not configured")
        if not os.path.exists(self.socket_path):
            raise FileNotFoundError(f"AARNN socket does not exist: {self.socket_path}")
        with tempfile.NamedTemporaryFile(prefix="refiner_aarnn_", suffix=".sock", dir="/tmp", delete=True) as handle:
            client_path = handle.name
        try:
            if os.path.exists(client_path):
                os.unlink(client_path)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                client.settimeout(self.timeout)
                client.bind(client_path)
                handshake = json.dumps(
                    {
                        "expected_s": self.sensory_size,
                        "expected_o": self.output_size,
                    }
                ).encode("utf-8")
                client.sendto(handshake, self.socket_path)
                response = client.recv(1024)
            finally:
                client.close()
        finally:
            if os.path.exists(client_path):
                try:
                    os.unlink(client_path)
                except OSError:
                    pass
        try:
            payload = json.loads(response.decode("utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            expected_s = payload.get("expected_s")
            expected_o = payload.get("expected_o")
            if isinstance(expected_s, int) and expected_s > 0:
                self.sensory_size = expected_s
            if isinstance(expected_o, int) and expected_o > 0:
                self.output_size = expected_o
        return payload if isinstance(payload, dict) else {}

    def health_check(self, force: bool = False) -> Dict[str, Any]:
        """Return the best-effort health state for the configured engine."""

        now = time.time()
        if not force and self._health_cache and (now - self._health_cache_at) < self.health_ttl_sec:
            return dict(self._health_cache)

        started = time.time()
        status: Dict[str, Any]
        try:
            if self.endpoint:
                response = requests.get(
                    f"{self.endpoint}/healthz",
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json() if response.content else {}
                status = {
                    "ok": True,
                    "mode": "http",
                    "endpoint": self.endpoint,
                    "details": payload if isinstance(payload, dict) else {},
                }
            elif self.socket_path and os.path.exists(self.socket_path):
                payload = self._socket_handshake()
                status = {
                    "ok": True,
                    "mode": "uds",
                    "socket_path": self.socket_path,
                    "details": payload,
                }
            elif self.repo_root and os.path.exists(self.repo_root):
                status = {
                    "ok": True,
                    "mode": "offline_heuristic",
                    "repo_root": self.repo_root,
                    "details": {
                        "reason": "AARNN repository present; using offline heuristic routing and AER translation",
                    },
                }
            else:
                status = {
                    "ok": False,
                    "mode": "unavailable",
                    "details": {"reason": "No AARNN endpoint, socket, or repository detected"},
                }
        except Exception as exc:
            status = {
                "ok": bool(self.repo_root and os.path.exists(self.repo_root)),
                "mode": "offline_heuristic" if self.repo_root and os.path.exists(self.repo_root) else "error",
                "details": {"reason": str(exc)},
            }
        status["latency_ms"] = max(0, int((time.time() - started) * 1000))
        self._health_cache = dict(status)
        self._health_cache_at = now
        return dict(status)

    def _basic_health_snapshot(self) -> Dict[str, Any]:
        repo_present = bool(self.repo_root and os.path.exists(self.repo_root))
        socket_present = bool(self.socket_path and os.path.exists(self.socket_path))
        if self.endpoint:
            return {
                "ok": True,
                "mode": "http_configured",
                "details": {"endpoint": self.endpoint},
            }
        if socket_present:
            return {
                "ok": True,
                "mode": "uds_ready",
                "details": {"socket_path": self.socket_path},
            }
        if repo_present:
            return {
                "ok": True,
                "mode": "offline_heuristic",
                "details": {"repo_root": self.repo_root},
            }
        if self.socket_path:
            return {
                "ok": False,
                "mode": "uds_missing",
                "details": {"socket_path": self.socket_path},
            }
        return {
            "ok": False,
            "mode": "unavailable",
            "details": {"reason": "No AARNN endpoint, socket, or repository detected"},
        }

    def summary(self, *, probe_health: bool = False) -> Dict[str, Any]:
        """Return a serialisable engine summary for health/admin surfaces."""

        if probe_health:
            health = self.health_check(force=False)
        else:
            health = self._basic_health_snapshot()
        summary = {
            "name": self.name,
            "type": self.engine_type,
            "enabled": True,
            "available": self.is_available(),
            "roles": list(self.roles),
            "specialties": list(self.specialties),
            "repo_root": self.repo_root,
            "endpoint": self.endpoint,
            "socket_path": self.socket_path,
            "sensory_size": self.sensory_size,
            "output_size": self.output_size,
            "aer_sensory_base": self.aer_sensory_base,
            "aer_output_base": self.aer_output_base,
            "description": self.description,
            "weight": self.weight,
            "health": dict(health or {}),
        }
        summary["health"]["probed"] = bool(probe_health)
        return summary

    @classmethod
    def configuration_summary(cls, config_path: Optional[str] = None, *, probe_health: bool = False) -> Dict[str, Any]:
        """Return configured AARNN status even when the engine is unavailable."""

        options = _configured_engine_options(config_path)
        return cls.configuration_summary_from_options(options, probe_health=probe_health)

    @classmethod
    def configuration_summary_from_spec(
        cls,
        spec: Dict[str, Any],
        *,
        probe_health: bool = False,
        apply_env_overrides: bool = False,
        default_name: str = "AARNN",
    ) -> Dict[str, Any]:
        options = _engine_options_from_spec(
            spec,
            apply_env_overrides=apply_env_overrides,
            default_name=default_name,
        )
        return cls.configuration_summary_from_options(options, probe_health=probe_health)

    @classmethod
    def configuration_summary_from_options(cls, options: Dict[str, Any], *, probe_health: bool = False) -> Dict[str, Any]:
        summary = {
            "type": str(options.get("engine_type") or "aarnn").strip().lower() or "aarnn",
            "name": str(options.get("name") or options.get("engine_cfg", {}).get("name") or "AARNN").strip() or "AARNN",
            "enabled": bool(options.get("enabled", True)),
            "configured": any(
                [
                    options.get("repo_root"),
                    options.get("endpoint"),
                    options.get("socket_path"),
                    options.get("engine_cfg"),
                ]
            ),
            "roles": list(options.get("roles") or []),
            "specialties": list(options.get("specialties") or []),
            "repo_root": str(options.get("repo_root") or "").strip() or None,
            "endpoint": _normalized_url(options.get("endpoint")),
            "socket_path": str(options.get("socket_path") or "").strip() or None,
            "sensory_size": int(options.get("sensory_size") or 32),
            "output_size": int(options.get("output_size") or 16),
            "aer_sensory_base": int(options.get("aer_sensory_base") or DEFAULT_AER_SENSORY_BASE),
            "aer_output_base": int(options.get("aer_output_base") or DEFAULT_AER_OUTPUT_BASE),
            "description": options.get("description"),
            "weight": _safe_float(options.get("weight"), 0.0),
        }
        if not summary["enabled"]:
            summary["available"] = False
            summary["health"] = {
                "ok": False,
                "mode": "disabled",
                "details": {"reason": "REFINER_AARNN_ENABLED disabled the neuromorphic engine"},
                "probed": False,
            }
            return summary

        engine = cls.from_options(options)
        if engine is None:
            summary["available"] = False
            summary["health"] = {
                "ok": False,
                "mode": "unavailable",
                "details": {"reason": "No endpoint, socket, or repository was detected for this engine"},
                "probed": bool(probe_health),
            }
            return summary
        engine_summary = engine.summary(probe_health=probe_health)
        summary["available"] = engine_summary.get("available", False)
        summary["health"] = engine_summary.get("health") or {}
        return summary

    def _predict_http(self, inputs: Sequence[float]) -> AarnnPrediction:
        response = requests.post(
            f"{self.endpoint}/predict",
            json={"inputs": [float(value) for value in inputs]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        input_spikes = spikes_from_floats(inputs, threshold=self.spike_threshold)
        aer_payload = encode_spikes(int(time.time() * 1_000_000), self.aer_sensory_base, input_spikes)
        score = _safe_float(payload.get("score"), default=sum(inputs) / max(1, len(inputs)))
        threshold = _safe_float(payload.get("threshold"), default=self.spike_threshold)
        fired = bool(payload.get("fired", score >= threshold))
        output_spikes = [1] if fired else []
        return AarnnPrediction(
            score=score,
            fired=fired,
            mode="http",
            threshold=threshold,
            input_spikes=input_spikes,
            output_spikes=output_spikes,
            aer_payload_hex=payload_hex(aer_payload),
            raw=payload if isinstance(payload, dict) else {"raw": payload},
        )

    def _predict_socket(self, inputs: Sequence[float]) -> AarnnPrediction:
        if not self.socket_path:
            raise FileNotFoundError("AARNN socket path is not configured")
        if not os.path.exists(self.socket_path):
            raise FileNotFoundError(f"AARNN socket does not exist: {self.socket_path}")
        input_spikes = spikes_from_floats(inputs, threshold=self.spike_threshold)
        payload = encode_spikes(int(time.time() * 1_000_000), self.aer_sensory_base, input_spikes)
        with tempfile.NamedTemporaryFile(prefix="refiner_aarnn_predict_", suffix=".sock", dir="/tmp", delete=True) as handle:
            client_path = handle.name
        try:
            if os.path.exists(client_path):
                os.unlink(client_path)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                client.settimeout(self.timeout)
                client.bind(client_path)
                client.sendto(payload, self.socket_path)
                response = client.recv(65536)
            finally:
                client.close()
        finally:
            if os.path.exists(client_path):
                try:
                    os.unlink(client_path)
                except OSError:
                    pass
        output_spikes = decode_spikes(response, self.aer_output_base, self.output_size)
        if not any(output_spikes):
            output_spikes = decode_spikes_auto(response, self.aer_output_base) or output_spikes
        spike_total = sum(1 for spike in output_spikes if int(spike) > 0)
        score = spike_total / max(1, len(output_spikes) or self.output_size)
        return AarnnPrediction(
            score=score,
            fired=bool(spike_total),
            mode="uds",
            threshold=self.spike_threshold,
            input_spikes=input_spikes,
            output_spikes=list(output_spikes),
            aer_payload_hex=payload_hex(payload),
            raw={
                "socket_path": self.socket_path,
                "response_bytes": len(response),
            },
        )

    def _predict_heuristic(self, inputs: Sequence[float]) -> AarnnPrediction:
        input_spikes = spikes_from_floats(inputs, threshold=self.spike_threshold)
        aer_payload = encode_spikes(int(time.time() * 1_000_000), self.aer_sensory_base, input_spikes)
        if inputs:
            score = sum(float(value) for value in inputs) / float(len(inputs))
        else:
            score = 0.0
        fired = score >= self.spike_threshold
        output_spikes = [1] if fired else []
        return AarnnPrediction(
            score=score,
            fired=fired,
            mode="offline_heuristic",
            threshold=self.spike_threshold,
            input_spikes=input_spikes,
            output_spikes=output_spikes,
            aer_payload_hex=payload_hex(aer_payload),
            raw={"repo_root": self.repo_root},
        )

    def predict_inputs(self, inputs: Sequence[float]) -> AarnnPrediction:
        """Predict against the best available AARNN transport."""

        if self.endpoint:
            try:
                return self._predict_http(inputs)
            except Exception as exc:
                logger.debug("AARNN HTTP predict failed, falling back: %s", exc)
        if self.socket_path:
            try:
                return self._predict_socket(inputs)
            except Exception as exc:
                logger.debug("AARNN UDS predict failed, falling back: %s", exc)
        return self._predict_heuristic(inputs)

    def analyze_task(self, text: str, *, workflow: str, role: str) -> Dict[str, Any]:
        """Score a text task for neuromorphic relevance and emit AER metadata."""

        cleaned = " ".join(str(text or "").split())
        lowered = cleaned.lower()
        hits = _keyword_hits(cleaned, self.keyword_hints)
        aer_hits = sum(count for keyword, count in hits.items() if "aer" in keyword or "spike" in keyword)
        feature_inputs = [
            min(len(cleaned) / 1200.0, 1.0),
            min(sum(hits.values()) / 6.0, 1.0),
            1.0 if workflow in {"project_solver", "playground_plan", "assistant_requirements"} else 0.4,
            1.0 if role in {"planner", "reviewer", "researcher", "assistant"} else 0.3,
            min(aer_hits / 3.0, 1.0),
        ]
        prediction = self.predict_inputs(feature_inputs)
        relevant = bool(hits) or prediction.fired or "neuromorphic" in lowered or "spiking" in lowered
        return {
            "engine": self.engine_type,
            "engine_name": self.name,
            "relevant": relevant,
            "score": round(prediction.score, 6),
            "fired": prediction.fired,
            "mode": prediction.mode,
            "threshold": prediction.threshold,
            "keyword_hits": hits,
            "inputs": [round(float(value), 6) for value in feature_inputs],
            "input_spikes": prediction.input_spikes,
            "output_spikes": prediction.output_spikes,
            "aer_payload_hex": prediction.aer_payload_hex,
            "workflow": workflow,
            "role": role,
            "repo_root": self.repo_root,
            "endpoint": self.endpoint,
            "socket_path": self.socket_path,
            "aer_sensory_base": self.aer_sensory_base,
            "aer_output_base": self.aer_output_base,
            "roles": list(self.roles),
            "specialties": list(self.specialties),
            "description": self.description,
            "weight": self.weight,
            "raw": prediction.raw,
        }

    def prompt_context(self, text: str, *, workflow: str, role: str) -> str:
        """Render a concise prompt block for LLM tasks touching AARNN/SNN work."""

        analysis = self.analyze_task(text, workflow=workflow, role=role)
        return self.format_prompt_context(analysis)

    def format_prompt_context(self, analysis: Dict[str, Any]) -> str:
        """Render prompt context from a precomputed analysis payload."""

        if not analysis.get("relevant"):
            return ""
        mode = analysis.get("mode") or "offline_heuristic"
        location = self.endpoint or self.socket_path or self.repo_root or "not configured"
        lines = [
            "Neuromorphic engine support is available for this task.",
            f"- Engine: {self.name} ({mode})",
            f"- Engine type: {self.engine_type}",
            f"- Location: {location}",
            f"- AER sensory/output bases: {self.aer_sensory_base}/{self.aer_output_base}",
        ]
        if self.description:
            lines.append(f"- Description: {self.description}")
        for guidance in self.guidance_lines:
            cleaned = str(guidance).strip()
            if not cleaned:
                continue
            lines.append(cleaned if cleaned.startswith("-") else f"- {cleaned}")
        if self.prefer_aarnn_designs and "aarnn" in self.specialties and not any("AARNN" in line.upper() for line in lines):
            lines.append("- Prefer AARNN-grown SNN designs when the task explicitly calls for spiking or neuromorphic networks.")
        lines.extend(
            [
                f"- Routing score: {analysis.get('score')}",
                f"- Input AER payload sample (hex): {analysis.get('aer_payload_hex')}",
            ]
        )
        return "\n".join(lines).strip()
