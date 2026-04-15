"""Specialist-engine registry for Refiner AI orchestration.

This module generalises the earlier single-AARNN path into a collection of
specialist engines that can contribute alongside the concurrent LLM router.

Today the concrete runtime implementation is based on the AER/SNN transport
support already implemented by ``AarnnEngine``. That means Refiner can now:

- attach multiple AARNN instances concurrently,
- attach additional non-AARNN SNN/AER specialist engines concurrently,
- collect their task analyses in parallel, and
- merge their prompt context into the LLM orchestration path.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from refiner_ai_aarnn import AarnnEngine


logger = logging.getLogger(__name__)

_GENERIC_ENGINE_TYPES = {"snn", "snn_aer", "aer_snn", "aer", "neuromorphic"}
_NEUROMORPHIC_SPECIALTIES = {"aarnn", "aer", "snn", "spiking", "neuromorphic"}


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


def _load_json_file(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        import json

        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _default_config_path(config_path: Optional[str] = None) -> str:
    return config_path or os.getenv("REFINER_CONFIG_PATH") or "config.json"


def _configured_engine_specs(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = _load_json_file(_default_config_path(config_path))
    orchestration_cfg = cfg.get("ai_orchestration") if isinstance(cfg.get("ai_orchestration"), dict) else {}
    specs: List[Dict[str, Any]] = []
    for raw in orchestration_cfg.get("engines") or []:
        if not isinstance(raw, dict):
            continue
        spec = dict(raw)
        spec.setdefault("_source", "ai_orchestration")
        specs.append(spec)
    return specs


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def _is_aarnn_spec(spec: Dict[str, Any]) -> bool:
    engine_type = str(spec.get("type") or spec.get("engine") or "").strip().lower()
    name = str(spec.get("name") or "").strip().lower()
    specialties = set(_as_list(spec.get("specialties") or spec.get("tags")))
    return engine_type == "aarnn" or "aarnn" in name or "aarnn" in specialties


def _is_supported_specialist_spec(spec: Dict[str, Any]) -> bool:
    if _is_aarnn_spec(spec):
        return True
    engine_type = str(spec.get("type") or spec.get("engine") or "").strip().lower()
    specialties = set(_as_list(spec.get("specialties") or spec.get("tags")))
    return (
        engine_type in _GENERIC_ENGINE_TYPES
        or bool(specialties & _NEUROMORPHIC_SPECIALTIES)
        or str(spec.get("protocol") or "").strip().lower() == "aer"
    )


def _configured_aarnn_spec_count(specs: Sequence[Dict[str, Any]]) -> int:
    return sum(1 for spec in specs if _is_aarnn_spec(spec))


def _contains_aarnn_engine(engines: Sequence[AarnnEngine]) -> bool:
    return any(str(getattr(engine, "engine_type", "")).strip().lower() == "aarnn" for engine in engines)


def _contains_aarnn_summary(summaries: Sequence[Dict[str, Any]]) -> bool:
    return any(str(summary.get("type") or "").strip().lower() == "aarnn" for summary in summaries)


def _build_engine_from_spec(spec: Dict[str, Any], *, apply_env_overrides: bool = False) -> Optional[AarnnEngine]:
    if not _is_supported_specialist_spec(spec):
        return None
    default_name = "AARNN" if _is_aarnn_spec(spec) else "SNN/AER Specialist"
    return AarnnEngine.from_spec(
        spec,
        apply_env_overrides=apply_env_overrides,
        default_name=default_name,
    )


def build_specialist_engines(config_path: Optional[str] = None) -> List[AarnnEngine]:
    """Return active specialist engines configured for orchestration."""

    config_file = _default_config_path(config_path)
    specs = _configured_engine_specs(config_file)
    aarnn_spec_count = _configured_aarnn_spec_count(specs)
    built: List[AarnnEngine] = []
    for spec in specs:
        apply_env = aarnn_spec_count == 1 and _is_aarnn_spec(spec)
        try:
            engine = _build_engine_from_spec(spec, apply_env_overrides=apply_env)
        except Exception as exc:
            logger.debug("Skipping specialist engine %s: %s", spec.get("name") or spec.get("type"), exc)
            continue
        if engine is not None:
            built.append(engine)

    # Backwards-compatible AARNN fallback keeps one AARNN/SNN/AER path available
    # even when the explicit engine registry only lists non-AARNN specialists.
    if aarnn_spec_count == 0 and not _contains_aarnn_engine(built):
        legacy = AarnnEngine.from_env_or_config(config_file)
        if legacy is not None:
            built.append(legacy)
    return built


def specialist_engine_summaries(config_path: Optional[str] = None, *, probe_health: bool = False) -> List[Dict[str, Any]]:
    """Return serialisable summaries for all configured specialist engines."""

    config_file = _default_config_path(config_path)
    specs = _configured_engine_specs(config_file)
    aarnn_spec_count = _configured_aarnn_spec_count(specs)
    summaries: List[Dict[str, Any]] = []
    for spec in specs:
        if not _is_supported_specialist_spec(spec):
            summaries.append(
                {
                    "type": str(spec.get("type") or spec.get("engine") or "").strip().lower() or None,
                    "name": str(spec.get("name") or spec.get("type") or "engine").strip(),
                    "enabled": bool(spec.get("enabled", True)),
                    "configured": True,
                    "roles": _as_list(spec.get("roles") or spec.get("role")),
                    "specialties": _as_list(spec.get("specialties") or spec.get("tags")),
                    "source": str(spec.get("_source") or spec.get("source") or "config").strip() or "config",
                    "available": False,
                    "health": {
                        "ok": False,
                        "mode": "unsupported",
                        "details": {"reason": "This engine type is not yet implemented by Refiner"},
                        "probed": False,
                    },
                }
            )
            continue
        apply_env = aarnn_spec_count == 1 and _is_aarnn_spec(spec)
        summaries.append(
            AarnnEngine.configuration_summary_from_spec(
                spec,
                probe_health=probe_health,
                apply_env_overrides=apply_env,
                default_name="AARNN" if _is_aarnn_spec(spec) else "SNN/AER Specialist",
            )
        )

    if aarnn_spec_count == 0 and not _contains_aarnn_summary(summaries):
        legacy = AarnnEngine.configuration_summary(config_file, probe_health=probe_health)
        if legacy.get("configured") or legacy.get("available"):
            legacy = dict(legacy)
            legacy.setdefault("source", "legacy")
            summaries.append(legacy)
    return summaries


def _engine_supports_role(engine: AarnnEngine, role: str) -> bool:
    roles = {str(item).strip().lower() for item in getattr(engine, "roles", []) if str(item).strip()}
    if not roles:
        return True
    return str(role or "").strip().lower() in roles


def _invoke_engine(engine: AarnnEngine, *, text: str, workflow: str, role: str) -> Dict[str, Any]:
    analysis = engine.analyze_task(text, workflow=workflow, role=role)
    if not isinstance(analysis, dict):
        analysis = {"relevant": False, "error": "invalid_analysis_payload"}
    analysis.setdefault("engine", getattr(engine, "engine_type", None))
    analysis.setdefault("engine_name", getattr(engine, "name", None))
    analysis.setdefault("roles", list(getattr(engine, "roles", [])))
    analysis.setdefault("specialties", list(getattr(engine, "specialties", [])))
    analysis.setdefault("weight", getattr(engine, "weight", 0.0))
    return analysis


def analyze_specialist_engines(
    engines: Sequence[AarnnEngine],
    *,
    text: str,
    workflow: str,
    role: str,
) -> Dict[str, Any]:
    """Run specialist-engine task analysis concurrently and merge the results."""

    active = [engine for engine in engines if engine is not None and _engine_supports_role(engine, role)]
    if not active:
        return {
            "relevant": False,
            "engine_count": 0,
            "engines": [],
            "selected": None,
            "combined_specialties": [],
            "context_blocks": [],
            "context": "",
        }

    paired: List[Tuple[AarnnEngine, Dict[str, Any]]] = []
    if len(active) == 1:
        try:
            paired.append((active[0], _invoke_engine(active[0], text=text, workflow=workflow, role=role)))
        except Exception as exc:
            logger.debug("Specialist engine %s analysis failed: %s", getattr(active[0], "name", "engine"), exc)
            paired.append(
                (
                    active[0],
                    {
                    "engine": getattr(active[0], "engine_type", None),
                    "engine_name": getattr(active[0], "name", None),
                    "relevant": False,
                    "error": str(exc),
                    "specialties": list(getattr(active[0], "specialties", [])),
                    "roles": list(getattr(active[0], "roles", [])),
                    "weight": getattr(active[0], "weight", 0.0),
                    },
                )
            )
    else:
        with ThreadPoolExecutor(max_workers=len(active)) as executor:
            future_map = {
                executor.submit(_invoke_engine, engine, text=text, workflow=workflow, role=role): engine
                for engine in active
            }
            for future in as_completed(future_map):
                engine = future_map[future]
                try:
                    paired.append((engine, future.result()))
                except Exception as exc:
                    logger.debug("Specialist engine %s analysis failed: %s", getattr(engine, "name", "engine"), exc)
                    paired.append(
                        (
                            engine,
                            {
                            "engine": getattr(engine, "engine_type", None),
                            "engine_name": getattr(engine, "name", None),
                            "relevant": False,
                            "error": str(exc),
                            "specialties": list(getattr(engine, "specialties", [])),
                            "roles": list(getattr(engine, "roles", [])),
                            "weight": getattr(engine, "weight", 0.0),
                            },
                        )
                    )

    paired.sort(
        key=lambda item: (
            1 if item[1].get("relevant") else 0,
            float(item[1].get("score") or 0.0) + float(item[1].get("weight") or 0.0),
            str(item[1].get("engine_name") or item[1].get("engine") or ""),
        ),
        reverse=True,
    )
    analyses = [analysis for _, analysis in paired]
    relevant = [analysis for analysis in analyses if analysis.get("relevant")]
    contexts: List[str] = []
    seen_contexts = set()
    for engine, analysis in paired:
        if not analysis.get("relevant"):
            continue
        try:
            context = engine.format_prompt_context(analysis)
        except Exception as exc:
            logger.debug("Specialist engine %s context formatting failed: %s", getattr(engine, "name", "engine"), exc)
            context = ""
        cleaned = str(context or "").strip()
        if cleaned and cleaned not in seen_contexts:
            seen_contexts.add(cleaned)
            contexts.append(cleaned)

    combined_specialties = sorted(
        {
            str(tag).strip().lower()
            for analysis in relevant
            for tag in (analysis.get("specialties") or [])
            if str(tag).strip()
        }
    )
    return {
        "relevant": bool(relevant),
        "engine_count": len(analyses),
        "engines": analyses,
        "selected": relevant[0] if relevant else (analyses[0] if analyses else None),
        "combined_specialties": combined_specialties,
        "context_blocks": contexts,
        "context": "\n\n".join(contexts).strip(),
    }
