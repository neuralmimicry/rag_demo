"""
CLI entry point to run Refiner workflows:
- Jira reporting and quality analysis (default statistics workflow)
- Jira issue quality analysis (interactive HTML + optional LLM)
- Confluence space quality analysis (interactive HTML + optional LLM/Rovo)
- Topic research (LLM-backed RAG, optional web search)
- Project solver (scan a local project for requirements and apply an LLM plan)
- Delivery pipeline (sandbox/dev/integration/staging/uat/deploy)

Workflow selection order:
1) --topic-research
2) --delivery
3) --project
4) --analyze-confluence
5) --analyze-jira
6) Default Jira statistics workflow (unless --disable-jira is set)

Usage examples:
  - Classic Jira stats (legacy behaviour):
      python run_refiner.py

  - Analyse a Confluence space (e.g., CTO Agile Transformation Team space `CAT`):
      python run_refiner.py --analyze-confluence --space CAT \
          --output confluence_report.html --use-rovo

  - Project solver (scan local folder for requirements, propose/apply plan):
      python run_refiner.py --project /path/to/project --llm-provider openai

  - Delivery pipeline (sandbox/dev/staging/uat/deploy):
      python run_refiner.py --delivery --project /path/to/project

Notes:
  - Configure credentials via environment (JIRA_USERNAME/JIRA_PASSWORD or token)
    and config.json. Confluence in Atlassian Cloud is assumed under the same base
    URL with a `/wiki` path.
  - When `--use-rovo` is set, the CLI attempts to call a configured Rovo endpoint
    (see README for environment variables). If unavailable, it falls back
    gracefully to heuristic analysis via the Confluence REST APIs.
"""

from typing import Optional, List, Dict, Any, Tuple
import json
import os
import argparse
import logging
import sys
import time
from logging_utils import setup_logging

logger = logging.getLogger(__name__)


class EventEmitter:
    """Emit structured workflow events to stdout and/or JSONL file."""

    def __init__(self, enabled: bool = False, stream=None, file_path: Optional[str] = None):
        """Initialize optional stream/file event sinks."""
        self.enabled = bool(enabled)
        self.stream = stream or sys.stdout
        self.file_handle = None
        if file_path:
            try:
                self.file_handle = open(file_path, "a", encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to open events file %s: %s", file_path, exc)

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Write one event record to enabled sinks."""
        if not (self.enabled or self.file_handle):
            return
        record = {"type": event_type, "ts": time.time()}
        if payload:
            record.update(payload)
        if self.enabled:
            try:
                self.stream.write("__RAG_EVENT__ " + json.dumps(record) + "\n")
                self.stream.flush()
            except Exception:
                pass
        if self.file_handle:
            try:
                self.file_handle.write(json.dumps(record) + "\n")
                self.file_handle.flush()
            except Exception:
                pass

    def close(self) -> None:
        """Close file sink when configured."""
        if self.file_handle:
            try:
                self.file_handle.close()
            except Exception:
                pass
            self.file_handle = None


def _emit_stage(
    emitter: Optional[EventEmitter],
    workflow: str,
    stage: str,
    status: str,
    progress: Optional[int] = None,
    message: Optional[str] = None,
) -> None:
    """Emit a normalized stage-progress event."""
    if not emitter:
        return
    payload: Dict[str, Any] = {"workflow": workflow, "stage": stage, "status": status}
    if progress is not None:
        payload["progress"] = progress
    if message:
        payload["message"] = message
    emitter.emit("stage", payload)


def _parse_agent_role_overrides(raw_items: Optional[List[str]]) -> Dict[str, Dict[str, str]]:
    """Parse ``--agent-role`` overrides into ``role -> provider/model`` mapping."""
    roles: Dict[str, Dict[str, str]] = {}
    for item in raw_items or []:
        if not item or "=" not in item:
            continue
        role, value = item.split("=", 1)
        role = role.strip().lower()
        provider = value.strip()
        model = None
        if ":" in provider:
            provider, model = provider.split(":", 1)
            provider = provider.strip()
            model = model.strip() or None
        if not role or not provider:
            continue
        entry: Dict[str, str] = {"provider": provider}
        if model:
            entry["model"] = model
        roles[role] = entry
    return roles


def _normalize_agentic_roles(raw_roles: object) -> Dict[str, Dict[str, object]]:
    """Normalize role config values from dict/list formats to one mapping."""
    roles: Dict[str, Dict[str, object]] = {}
    if isinstance(raw_roles, dict):
        items = raw_roles.items()
    elif isinstance(raw_roles, list):
        items = []
        for item in raw_roles:
            if not isinstance(item, dict):
                continue
            role = item.get("role") or item.get("name")
            if role:
                items.append((role, item))
    else:
        return roles
    for role, cfg in items:
        if not isinstance(cfg, dict):
            continue
        role_name = str(role).strip().lower()
        if not role_name:
            continue
        roles[role_name] = dict(cfg)
    return roles


def _resolve_agentic_roles(
    cfg: dict,
    overrides: Optional[Dict[str, Dict[str, object]]],
    get_llm_credentials,
) -> Dict[str, Dict[str, object]]:
    """Resolve role configs to concrete provider/model/api-key settings."""
    llm_configs = cfg.get("llm_providers", []) or []
    roles = _normalize_agentic_roles(cfg.get("agentic_roles"))
    for role, override in (overrides or {}).items():
        base = roles.get(role, {})
        merged = dict(base)
        for key, value in (override or {}).items():
            if value is not None and value != "":
                merged[key] = value
        roles[role] = merged

    resolved: Dict[str, Dict[str, object]] = {}
    for role, cfg_role in roles.items():
        if not isinstance(cfg_role, dict):
            continue
        provider_key = cfg_role.get("provider") or cfg_role.get("type") or cfg_role.get("llm_provider")
        if not provider_key:
            continue
        provider_type = provider_key
        model = cfg_role.get("model")
        base_url = cfg_role.get("base_url")
        api_key = cfg_role.get("api_key")
        matched = next((p for p in llm_configs if p.get("name") == provider_key), None)
        if matched:
            provider_type = matched.get("type", provider_type)
            model = model or matched.get("model")
            base_url = base_url or matched.get("base_url")
            if not api_key:
                api_key = get_llm_credentials(matched.get("name"), provider_type)
        resolved[role] = {
            "provider": provider_type,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": cfg_role.get("temperature"),
            "max_tokens": cfg_role.get("max_tokens"),
            "timeout": cfg_role.get("timeout"),
            "reasoning_effort": cfg_role.get("reasoning_effort"),
        }
    return resolved


def _match_llm_config(llm_configs: List[dict], provider_key: Optional[str]) -> Optional[dict]:
    """Match a provider selector against config entries by name first, then type."""
    if not provider_key:
        return None
    normalized = str(provider_key).strip().lower()
    if not normalized:
        return None
    for item in llm_configs or []:
        name = str(item.get("name") or "").strip().lower()
        if name and name == normalized:
            return item
    for item in llm_configs or []:
        provider_type = str(item.get("type") or "").strip().lower()
        if provider_type and provider_type == normalized:
            return item
    return None


def _resolve_llm_selection(
    llm_configs: List[dict],
    requested_provider: Optional[str],
    requested_model: Optional[str],
    requested_base_url: Optional[str],
    get_llm_credentials,
    default_cfg: Optional[dict] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[dict]]:
    """Resolve one LLM slot from explicit CLI input or config defaults."""
    matched_cfg = _match_llm_config(llm_configs, requested_provider)
    provider = requested_provider
    model = requested_model
    base_url = requested_base_url
    api_key = None

    if matched_cfg is None and not provider and default_cfg:
        matched_cfg = default_cfg

    if matched_cfg:
        provider_name = matched_cfg.get("name")
        provider_type = matched_cfg.get("type", provider or "openai")
        provider = provider_type
        model = model or matched_cfg.get("model")
        base_url = base_url or matched_cfg.get("base_url")
        api_key = get_llm_credentials(provider_name, provider_type)
    elif provider:
        api_key = get_llm_credentials(None, provider)

    return provider, model, base_url, api_key, matched_cfg


def _run_jira_workflow() -> int:
    """Run the default Jira statistics workflow."""
    # Defer import to keep import side-effects minimal for unit tests
    from main import main as jira_main
    jira_main()
    return 0


def _run_confluence_analysis(
    space_key: str,
    output: Optional[str],
    use_rovo: bool,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    fallback_llm_provider: Optional[str] = None,
    fallback_llm_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    selection_path: Optional[str] = None,
    tree_depth: Optional[int] = None,
    starting_depth: Optional[int] = None,
    emit_templates: bool = False,
    templates_dir: Optional[str] = None,
    llm_max_tokens: Optional[int] = None,
    llm_chunk_size: Optional[int] = None,
    llm_temperature: float = 0.2,
    llm_timeout: Optional[int] = None,
    llm_reasoning_effort: Optional[str] = None,
    action_plan: bool = False,
    dry_run: bool = False,
    post_comments: bool = False,
    post_target: str = "both",
    post_exec_summary: bool = True,
    post_page_insights: bool = True,
    dry_run_post: bool = False,
    llm_inter_request_gap: float = 3.0,
) -> int:
    """Execute Confluence analysis mode from parsed CLI options."""
    from config_loader import load_config
    from credentials import get_credentials, get_llm_credentials
    from confluence_analysis import analyze_space_and_write_report

    cfg = load_config()
    instance_cfg = cfg.get("instances", [{}])[0]
    instance_name = instance_cfg.get("name")
    llm_configs = cfg.get("llm_providers", [])
    primary_default = llm_configs[0] if llm_configs else None
    llm_provider, llm_model, ollama_base_url, llm_api_key, primary_cfg = _resolve_llm_selection(
        llm_configs,
        llm_provider,
        llm_model,
        ollama_base_url,
        get_llm_credentials,
        default_cfg=primary_default,
    )
    if llm_api_key:
        if llm_provider in ("openai", "gpt", "chatgpt"):
            os.environ["OPENAI_API_KEY"] = llm_api_key
        elif llm_provider in ("gemini", "google"):
            os.environ["GEMINI_API_KEY"] = llm_api_key

    fallback_default = next((cfg for cfg in llm_configs if cfg is not primary_cfg), None)
    fallback_llm_provider, fallback_llm_model, fallback_base_url, fallback_llm_api_key, _ = _resolve_llm_selection(
        llm_configs,
        fallback_llm_provider,
        fallback_llm_model,
        ollama_base_url,
        get_llm_credentials,
        default_cfg=fallback_default,
    )
    if fallback_llm_provider == "ollama" and fallback_base_url:
        ollama_base_url = fallback_base_url

    base_url = instance_cfg.get("jira_url") or "https://your-domain.atlassian.net"
    auth = get_credentials(instance_name)
    out_path = output or "confluence_report.html"
    analyze_space_and_write_report(
        base_url,
        auth,
        space_key,
        out_path,
        use_rovo=use_rovo,
        llm_provider=llm_provider,
        llm_model=llm_model,
        fallback_llm_provider=fallback_llm_provider,
        fallback_llm_model=fallback_llm_model,
        fallback_llm_api_key=fallback_llm_api_key,
        ollama_base_url=ollama_base_url,
        selection_path=selection_path,
        tree_max_depth=tree_depth,
        starting_depth=starting_depth,
        emit_templates=emit_templates,
        templates_dir=templates_dir,
        llm_max_tokens=llm_max_tokens,
        llm_chunk_size=llm_chunk_size,
        llm_temperature=llm_temperature,
        llm_timeout=llm_timeout,
        llm_reasoning_effort=llm_reasoning_effort,
        action_plan=action_plan,
        dry_run=dry_run,
        post_comments=post_comments,
        post_target=post_target,
        post_exec_summary=post_exec_summary,
        post_page_insights=post_page_insights,
        dry_run_post=dry_run_post,
        llm_inter_request_gap=llm_inter_request_gap,
    )
    return 0


def _run_jira_analysis(
    projects: Optional[str],
    jql: Optional[str],
    output: Optional[str],
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    fallback_llm_provider: Optional[str] = None,
    fallback_llm_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    selection_path: Optional[str] = None,
    llm_max_tokens: Optional[int] = None,
    llm_chunk_size: Optional[int] = None,
    llm_temperature: float = 0.2,
    llm_timeout: Optional[int] = None,
    llm_reasoning_effort: Optional[str] = None,
    action_plan: bool = False,
    dry_run: bool = False,
    # Optional posting of AI-generated comments
    post_comments: bool = False,
    post_target: str = "both",
    dry_run_post: bool = False,
    llm_inter_request_gap: float = 3.0,
) -> int:
    """Execute Jira quality analysis mode from parsed CLI options."""
    from config_loader import load_config
    from credentials import get_credentials, get_llm_credentials
    from jira_analysis import analyze_jira_and_write_report

    cfg = load_config()
    instance_cfg = cfg.get("instances", [{}])[0]
    instance_name = instance_cfg.get("name")
    llm_configs = cfg.get("llm_providers", [])
    primary_default = llm_configs[0] if llm_configs else None
    llm_provider, llm_model, ollama_base_url, llm_api_key, primary_cfg = _resolve_llm_selection(
        llm_configs,
        llm_provider,
        llm_model,
        ollama_base_url,
        get_llm_credentials,
        default_cfg=primary_default,
    )
    if llm_api_key:
        if llm_provider in ("openai", "gpt", "chatgpt"):
            os.environ["OPENAI_API_KEY"] = llm_api_key
        elif llm_provider in ("gemini", "google"):
            os.environ["GEMINI_API_KEY"] = llm_api_key

    fallback_default = next((cfg for cfg in llm_configs if cfg is not primary_cfg), None)
    fallback_llm_provider, fallback_llm_model, fallback_base_url, fallback_llm_api_key, _ = _resolve_llm_selection(
        llm_configs,
        fallback_llm_provider,
        fallback_llm_model,
        ollama_base_url,
        get_llm_credentials,
        default_cfg=fallback_default,
    )
    if fallback_llm_provider == "ollama" and fallback_base_url:
        ollama_base_url = fallback_base_url

    base_url = instance_cfg.get("jira_url") or "https://your-domain.atlassian.net"
    auth = get_credentials(instance_name)
    out_path = output or "jira_report.html"
    analyze_jira_and_write_report(
        base_url=base_url,
        auth=auth,
        projects=projects,
        jql=jql,
        output_html=out_path,
        llm_provider=llm_provider,
        llm_model=llm_model,
        fallback_llm_provider=fallback_llm_provider,
        fallback_llm_model=fallback_llm_model,
        fallback_llm_api_key=fallback_llm_api_key,
        ollama_base_url=ollama_base_url,
        selection_path=selection_path,
        llm_max_tokens=llm_max_tokens,
        llm_chunk_size=llm_chunk_size,
        llm_temperature=llm_temperature,
        llm_timeout=llm_timeout,
        llm_reasoning_effort=llm_reasoning_effort,
        action_plan=action_plan,
        dry_run=dry_run,
        post_comments=post_comments,
        post_target=post_target,
        dry_run_post=dry_run_post,
        llm_inter_request_gap=llm_inter_request_gap,
    )
    return 0


def _run_topic_research(
    source: str,
    output: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    llm_max_tokens: Optional[int] = None,
    llm_temperature: float = 0.2,
    llm_timeout: Optional[int] = None,
    llm_reasoning_effort: Optional[str] = None,
    max_iterations: int = 10,
    context_sources: Optional[List[str]] = None,
    google_api_key: Optional[str] = None,
    google_cse_id: Optional[str] = None,
    fallback_llm_provider: Optional[str] = None,
    fallback_llm_model: Optional[str] = None,
    references_output: Optional[str] = None,
    llm_inter_request_gap: float = 3.0,
    cache_ttl_hours: int = 24,
    disable_jira: bool = False,
    disable_confluence: bool = False,
    agentic_role_overrides: Optional[Dict[str, Dict[str, object]]] = None,
) -> int:
    """Execute topic research mode and optionally emit reference bibliography."""
    from config_loader import load_config
    from credentials import get_credentials, get_llm_credentials
    from topic_researcher import TopicResearcher

    cfg = load_config()
    instance_cfg = cfg.get("instances", [{}])[0]
    base_url = instance_cfg.get("jira_url") or "https://your-domain.atlassian.net"
    company_name = instance_cfg.get("name")
    auth = get_credentials(company_name)

    llm_configs = cfg.get("llm_providers", [])
    search_configs = cfg.get("search_engines", [])
    primary_default = llm_configs[0] if llm_configs else None
    llm_provider, llm_model, ollama_base_url, llm_api_key, primary_cfg = _resolve_llm_selection(
        llm_configs,
        llm_provider,
        llm_model,
        ollama_base_url,
        get_llm_credentials,
        default_cfg=primary_default,
    )
    fallback_default = next((cfg for cfg in llm_configs if cfg is not primary_cfg), None)
    fallback_llm_provider, fallback_llm_model, fallback_base_url, fallback_llm_api_key, _ = _resolve_llm_selection(
        llm_configs,
        fallback_llm_provider,
        fallback_llm_model,
        ollama_base_url,
        get_llm_credentials,
        default_cfg=fallback_default,
    )
    if fallback_llm_provider == "ollama" and fallback_base_url:
        ollama_base_url = fallback_base_url

    agentic_roles = _resolve_agentic_roles(cfg, agentic_role_overrides, get_llm_credentials)
    if not agentic_roles:
        agentic_roles = None

    researcher = TopicResearcher(
        jira_base_url=base_url,
        jira_auth=auth,
        llm_provider=llm_provider,
        llm_model=llm_model,
        ollama_base_url=ollama_base_url,
        llm_temperature=llm_temperature,
        llm_max_tokens=llm_max_tokens,
        llm_timeout=llm_timeout,
        llm_reasoning_effort=llm_reasoning_effort,
        company_name=company_name,
        google_api_key=google_api_key,
        google_cse_id=google_cse_id,
        search_configs=search_configs,
        llm_api_key=llm_api_key,
        fallback_llm_provider=fallback_llm_provider,
        fallback_llm_model=fallback_llm_model,
        fallback_llm_api_key=fallback_llm_api_key,
        llm_inter_request_gap=llm_inter_request_gap,
        cache_ttl_hours=cache_ttl_hours,
        disable_jira=disable_jira,
        disable_confluence=disable_confluence,
        agentic_roles=agentic_roles,
    )
    researcher.run(source, output, max_iterations=max_iterations, context_sources=context_sources, references_path=references_output)
    return 0


def _run_project_solver(
    project_root: str,
    requirements_path: Optional[str],
    output: Optional[str],
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    fallback_llm_provider: Optional[str] = None,
    fallback_llm_model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    llm_max_tokens: Optional[int] = None,
    llm_temperature: float = 0.2,
    llm_timeout: Optional[int] = None,
    llm_reasoning_effort: Optional[str] = None,
    llm_inter_request_gap: float = 0.0,
    allow_run: bool = False,
    max_steps: int = 25,
    max_iterations: int = 3,
    project_output_dir: Optional[str] = None,
    codingagent: Optional[str] = None,
    codingagent_fallback: Optional[str] = None,
    codingagent_model: Optional[str] = None,
    codingagent_reasoning_effort: Optional[str] = None,
    agentic_role_overrides: Optional[Dict[str, Dict[str, object]]] = None,
) -> int:
    """Execute project solver mode and print completion diagnostics."""
    from credentials import get_llm_credentials
    from project_solver import run_project_solver

    def _load_config_for_project(path: str = "config.json") -> dict:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {"llm_providers": []}
        except Exception as exc:
            logger.info(f"Failed to read config at {path}: {exc}")
            return {"llm_providers": []}

    def _format_list(items: List[str], limit: int = 5) -> str:
        if not items:
            return "none"
        if len(items) <= limit:
            return ", ".join(items)
        remaining = len(items) - limit
        return f"{', '.join(items[:limit])}, ... (+{remaining} more)"

    cfg = _load_config_for_project()
    llm_configs = cfg.get("llm_providers", [])
    llm_api_key = None
    fallback_llm_api_key = None

    if llm_provider:
        provider_cfg = next((p for p in llm_configs if p.get("name") == llm_provider), None)
        if provider_cfg:
            llm_provider_name = provider_cfg.get("name")
            llm_provider_type = provider_cfg.get("type", "openai")
            llm_model = llm_model or provider_cfg.get("model")
            ollama_base_url = ollama_base_url or provider_cfg.get("base_url")
            llm_provider = llm_provider_type
            llm_api_key = get_llm_credentials(llm_provider_name, llm_provider_type)

    if fallback_llm_provider:
        f_provider_cfg = next((p for p in llm_configs if p.get("name") == fallback_llm_provider), None)
        if f_provider_cfg:
            f_name = f_provider_cfg.get("name")
            f_type = f_provider_cfg.get("type", "openai")
            fallback_llm_model = fallback_llm_model or f_provider_cfg.get("model")
            fallback_llm_provider = f_type
            fallback_llm_api_key = get_llm_credentials(f_name, f_type)

    agentic_roles = _resolve_agentic_roles(cfg, agentic_role_overrides, get_llm_credentials)
    if not agentic_roles:
        agentic_roles = None

    if not llm_provider:
        raise ValueError("LLM provider is required for project solving.")

    out_path = output or os.path.join(project_root, "project_solution.json")
    exit_code = run_project_solver(
        project_root,
        requirements_path=requirements_path,
        output_path=out_path,
        llm_provider=llm_provider,
        llm_model=llm_model,
        ollama_base_url=ollama_base_url,
        llm_max_tokens=llm_max_tokens,
        llm_temperature=llm_temperature,
        llm_timeout=llm_timeout,
        llm_reasoning_effort=llm_reasoning_effort,
        llm_api_key=llm_api_key,
        fallback_llm_provider=fallback_llm_provider,
        fallback_llm_model=fallback_llm_model,
        fallback_llm_api_key=fallback_llm_api_key,
        llm_inter_request_gap=llm_inter_request_gap,
        allow_run=allow_run,
        max_steps=max_steps,
        max_iterations=max_iterations,
        project_output_dir=project_output_dir,
        codingagent=codingagent,
        codingagent_fallback=codingagent_fallback,
        codingagent_model=codingagent_model,
        codingagent_reasoning_effort=codingagent_reasoning_effort,
        agentic_roles=agentic_roles,
    )
    try:
        with open(out_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        summary = report.get("completion_summary") if isinstance(report, dict) else None
        if isinstance(summary, dict):
            total_sources = summary.get("total_sources", 0)
            completed = summary.get("completed_sources") or []
            incomplete = summary.get("incomplete_sources") or []
            unstarted = summary.get("unstarted_sources") or []
            exhausted = summary.get("iterations_exhausted_sources") or []
            coverage_missing = summary.get("coverage_missing_sources") or []
            missing_req_ids = summary.get("requirements_missing_ids") or []
            verification_failures = summary.get("unresolved_verification_failures") or []
            steps_applied = summary.get("steps_applied", 0)
            max_steps_summary = summary.get("max_steps", 0)
            max_iterations_summary = summary.get("max_iterations", 0)
            needs_more = summary.get("needs_more_iterations", False)
            print("Project solver completion summary:")
            print(f"  Sources: {len(completed)}/{total_sources} completed; {len(incomplete)} incomplete")
            if incomplete:
                print(f"  Incomplete sources: {_format_list(incomplete)}")
            if unstarted:
                print(f"  Unstarted sources: {_format_list(unstarted)}")
            if exhausted:
                print(f"  Iterations exhausted: {_format_list(exhausted)}")
            if coverage_missing:
                print(f"  Requirement coverage missing: {_format_list(coverage_missing)}")
            if missing_req_ids:
                print(f"  Requirements missing IDs: {_format_list(missing_req_ids)}")
            if verification_failures:
                failed_commands = [
                    f.get("command")
                    for f in verification_failures
                    if isinstance(f, dict) and isinstance(f.get("command"), str)
                ]
                if failed_commands:
                    print(f"  Verification failures: {_format_list(failed_commands)}")
                else:
                    print("  Verification failures: present (commands unavailable)")
            print(
                f"  Steps: {steps_applied}/{max_steps_summary}; "
                f"max_iterations={max_iterations_summary}; "
                f"needs_more_iterations={needs_more}"
            )
            requirements_table = report.get("requirements_register_markdown")
            if isinstance(requirements_table, str) and requirements_table.strip():
                print("\nRequirements register:")
                print(requirements_table)
            todo_table = report.get("todo_table_markdown")
            if isinstance(todo_table, str) and todo_table.strip():
                print("\nTODO summary:")
                print(todo_table)
            req_sanity = report.get("requirements_sanity")
            if isinstance(req_sanity, dict):
                status = req_sanity.get("status")
                total = req_sanity.get("total")
                referenced = req_sanity.get("referenced")
                missing = req_sanity.get("missing_ids") or []
                print(
                    f"\nRequirements sanity check: status={status}; "
                    f"referenced={referenced}/{total}; "
                    f"missing={_format_list(missing) if missing else 'none'}"
                )
    except Exception as exc:
        logger.info(f"Failed to read completion summary from {out_path}: {exc}")
    return exit_code


def _run_delivery_pipeline(
    project_root: str,
    config_path: Optional[str],
    output: Optional[str],
    allow_run: bool,
    project_solution_path: Optional[str],
    allow_unfinished: bool,
    enable_interim: bool,
    llm_provider: Optional[str],
    llm_model: Optional[str],
    fallback_llm_provider: Optional[str],
    fallback_llm_model: Optional[str],
    ollama_base_url: Optional[str],
    llm_max_tokens: Optional[int],
    llm_temperature: float,
    llm_timeout: Optional[int],
    llm_reasoning_effort: Optional[str],
    codingagent: Optional[str],
    codingagent_fallback: Optional[str],
    codingagent_model: Optional[str],
    codingagent_reasoning_effort: Optional[str],
    project_output_dir: Optional[str],
    solver_fallback_override: Optional[bool],
) -> int:
    """Execute delivery pipeline mode with optional solver fallback controls."""
    from delivery_pipeline import run_delivery_pipeline
    from credentials import get_llm_credentials

    llm_api_key = get_llm_credentials(None, llm_provider) if llm_provider else None
    fallback_llm_api_key = get_llm_credentials(None, fallback_llm_provider) if fallback_llm_provider else None

    if config_path and str(config_path).strip().lower() == "default":
        resolved_config = os.path.join(os.path.dirname(__file__), "delivery_pipeline.json")
    else:
        resolved_config = config_path or os.path.join(project_root, "delivery_pipeline.json")
    if not os.path.exists(resolved_config):
        fallback = os.path.join(os.path.dirname(__file__), "delivery_pipeline.json")
        if os.path.exists(fallback):
            resolved_config = fallback
        else:
            raise FileNotFoundError(
                "Delivery pipeline config not found. Looked in: "
                f"{resolved_config} and {fallback}. "
                "Pass --delivery-config to specify a config."
            )
    logger.info("Delivery pipeline config: %s", resolved_config)
    return run_delivery_pipeline(
        project_root,
        config_path=resolved_config,
        output_path=output,
        allow_run=allow_run,
        project_solution_path=project_solution_path,
        allow_unfinished=allow_unfinished,
        enable_interim=enable_interim,
        llm_provider=llm_provider,
        llm_model=llm_model,
        fallback_llm_provider=fallback_llm_provider,
        fallback_llm_model=fallback_llm_model,
        ollama_base_url=ollama_base_url,
        llm_max_tokens=llm_max_tokens,
        llm_temperature=llm_temperature,
        llm_timeout=llm_timeout,
        llm_reasoning_effort=llm_reasoning_effort,
        llm_api_key=llm_api_key,
        fallback_llm_api_key=fallback_llm_api_key,
        codingagent=codingagent,
        codingagent_fallback=codingagent_fallback,
        codingagent_model=codingagent_model,
        codingagent_reasoning_effort=codingagent_reasoning_effort,
        project_output_dir=project_output_dir,
        solver_fallback_override=solver_fallback_override,
    )


def run(argv: Optional[List[str]] = None) -> int:
    """Parse CLI args, select a workflow, and execute with event telemetry."""
    parser = argparse.ArgumentParser(description="Refiner reporter, Jira quality analyser, and Confluence space analyser")
    parser.add_argument("--analyze-confluence", action="store_true", help="Run Confluence space quality analysis instead of Jira statistics")
    parser.add_argument("--analyze-jira", action="store_true", help="Run Jira project/issue quality analysis (interactive HTML + optional LLM)")
    parser.add_argument("--topic-research", dest="topic_research", help="Path or URL to a file containing a topic and requirements for research")
    parser.add_argument("--context", dest="context_sources", action="append", help="Additional URLs or file paths to provide context, relevance, and focus")
    parser.add_argument("--max-iterations", dest="max_iterations", type=int, default=10, help="Maximum iterations for document refinement (default: 10)")
    parser.add_argument("--references-output", dest="references_output", help="Path to save the bibliography/references file")
    parser.add_argument("--research-cache-ttl", dest="research_cache_ttl", type=int, default=24, help="TTL in hours for the research cache (default: 24)")
    parser.add_argument("--clear-research-cache", dest="clear_research_cache", action="store_true", help="Clear the research cache before starting")
    parser.add_argument("--project", dest="project_root", help="Project folder to scan for requirements and solve (enables project solver mode)")
    parser.add_argument("--requirements", dest="requirements_path", help="Optional requirements document; if provided, project scanning is skipped")
    parser.add_argument("--project-run", dest="project_run", action="store_true", help="Allow project solver to execute run_command steps (default: disabled)")
    parser.add_argument("--project-max-steps", dest="project_max_steps", type=int, default=25, help="Max steps to apply for project solving (default: 25)")
    parser.add_argument("--project-iterations", dest="project_iterations", type=int, default=3, help="Max planning iterations for project solving (default: 3)")
    parser.add_argument("--project-output-dir", dest="project_output_dir", help="Output directory for generated code/virtual environments (absolute path allowed)")
    parser.add_argument("--delivery", dest="delivery", action="store_true", help="Run delivery pipeline for a project (requires --project)")
    parser.add_argument("--delivery-config", dest="delivery_config", help="Path to delivery pipeline config (default: <project>/delivery_pipeline.json, or 'default' to force bundled config)")
    parser.add_argument("--delivery-run", dest="delivery_run", action="store_true", help="Execute delivery pipeline commands (default: dry-run)")
    parser.add_argument("--delivery-allow-unfinished", dest="delivery_allow_unfinished", action="store_true", help="Allow deploy stages even if the project solver reports incomplete work")
    parser.add_argument("--delivery-enable-interim", dest="delivery_enable_interim", action="store_true", help="Enable interim deploy/teardown stages in the delivery pipeline")
    parser.add_argument("--delivery-solver-fallback", dest="delivery_solver_fallback", action="store_true", help="Enable project_solver fallback on delivery failures (overrides config)")
    parser.add_argument("--delivery-no-solver-fallback", dest="delivery_no_solver_fallback", action="store_true", help="Disable project_solver fallback on delivery failures (overrides config)")
    parser.add_argument("--delivery-project-solution", dest="delivery_project_solution", help="Path to project solver output JSON for overlay/gating")
    parser.add_argument(
        "--codingagent",
        dest="codingagent",
        choices=["opencode", "codex", "llm"],
        help="Preferred coding agent for code-heavy tasks (opencode, codex, or llm to use the main provider)",
    )
    parser.add_argument(
        "--codingagent-fallback",
        dest="codingagent_fallback",
        choices=["opencode", "codex", "llm"],
        default="llm",
        help="Fallback coding agent if the primary is unavailable (default: llm)",
    )
    parser.add_argument("--codingagent-model", dest="codingagent_model", help="Override model name for the coding agent (e.g., gpt-5.2-codex)")
    parser.add_argument("--codingagent-reasoning-effort", dest="codingagent_reasoning_effort", help="Reasoning effort for the coding agent (e.g., none, low, medium, high, xhigh)")
    parser.add_argument("--space", dest="space", help="Confluence space key (e.g., CAT)")
    parser.add_argument(
        "--output",
        dest="output",
        help=(
            "Output path for the selected workflow. Defaults: "
            "confluence_report.html (Confluence), jira_report.html (Jira analysis), "
            "researched_document.md (topic research), project_solution.json (project solver), "
            "pipeline report JSON (delivery pipeline)."
        ),
    )
    parser.add_argument("--use-rovo", dest="use_rovo", action="store_true", help="Prefer Atlassian Rovo/AI endpoints when available")
    # Optional LLM integration flags
    parser.add_argument("--llm-provider", dest="llm_provider", choices=["openai", "gemini", "ollama", "gpt", "chatgpt", "google"], help="Select LLM provider for analysis")
    parser.add_argument("--llm-model", dest="llm_model", help="Override model name for the chosen LLM provider")
    parser.add_argument("--llm-reasoning-effort", dest="llm_reasoning_effort", help="Reasoning effort for the LLM provider (e.g., none, low, medium, high, xhigh)")
    parser.add_argument("--fallback-llm-provider", dest="fallback_llm_provider", choices=["openai", "gemini", "ollama", "gpt", "chatgpt", "google"], help="Select fallback LLM provider if the primary fails")
    parser.add_argument("--fallback-llm-model", dest="fallback_llm_model", help="Override model name for the fallback LLM provider")
    parser.add_argument("--ollama-base-url", dest="ollama_base_url", help="Override Ollama base URL (default: http://localhost:11434)")
    parser.add_argument("--selection", dest="selection", help="Path to selection manifest JSON produced by the report UI")
    parser.add_argument("--tree-depth", dest="tree_depth", type=int, help="Limit page hierarchy depth included in the Confluence report (0=root only; 1=include direct children; etc.)")
    parser.add_argument("--starting-depth", dest="starting_depth", type=int, help="Depth at which analysis starts; analysis and comments will be attached to pages at this depth (default: 1)")
    parser.add_argument("--emit-templates", dest="emit_templates", action="store_true", help="Generate local template files from LLM analyses")
    parser.add_argument("--templates-dir", dest="templates_dir", help="Directory for generated templates (default: templates/<space>)")
    parser.add_argument("--llm-max-tokens", dest="llm_max_tokens", type=int, help="Max output tokens per request (budget)")
    parser.add_argument("--llm-chunk-size", dest="llm_chunk_size", type=int, help="Approximate chunk size in tokens for map-reduce")
    parser.add_argument("--llm-temperature", dest="llm_temperature", type=float, default=0.2, help="Sampling temperature for the LLM (default 0.2)")
    parser.add_argument("--llm-timeout", dest="llm_timeout", type=int, help="Timeout in seconds for LLM requests")
    parser.add_argument("--llm-inter-request-gap", dest="llm_inter_request_gap", type=float, default=3.0, help="Minimum seconds between consecutive LLM requests (default: 3.0)")
    parser.add_argument(
        "--agent-role",
        dest="agent_role",
        action="append",
        help=(
            "Override agentic role provider mapping (repeatable). "
            "Format: role=provider or role=provider:model. "
            "Roles include planner, researcher, reviewer, critic, editor."
        ),
    )
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Do not call LLMs, only estimate tokens and render UI")
    parser.add_argument("--action-plan", dest="action_plan", action="store_true", help="Include an action plan section in Jira/Confluence analysis reports")
    # Optional posting of AI-generated comments
    parser.add_argument("--post-comments", dest="post_comments", action="store_true", help="Post AI-generated analysis as comments to Jira/Confluence targets")
    parser.add_argument("--post-target", dest="post_target", choices=["jira", "confluence", "both"], default="both", help="Where to post comments (default: both)")
    parser.add_argument("--post-exec-summary", dest="post_exec_summary", action="store_true", help="Post Executive Summary as a comment on the space homepage (Confluence)")
    parser.add_argument("--post-page-insights", dest="post_page_insights", action="store_true", help="Post per-page AI assessments as comments on each page (Confluence)")
    parser.add_argument("--dry-run-post", dest="dry_run_post", action="store_true", help="Simulate comment posting without making API changes")
    # Jira analyser inputs
    parser.add_argument("--projects", dest="projects", help="CSV of Jira project keys to analyse (e.g., CAT,ENG,OPS)")
    parser.add_argument("--jql", dest="jql", help="Custom JQL to select issues for analysis (overrides --projects if provided)")

    parser.add_argument("--google-api-key", dest="google_api_key", default=os.getenv("GOOGLE_API_KEY"), help="Google Search API Key")
    parser.add_argument("--google-cse-id", dest="google_cse_id", default=os.getenv("GOOGLE_CSE_ID"), help="Google Search Engine ID (CX)")
    parser.add_argument("--gemini-api-key", dest="gemini_api_key", default=os.getenv("GEMINI_API_KEY"), help="Google Gemini API Key")
    parser.add_argument("--gemini-access-token", dest="gemini_access_token", default=os.getenv("GEMINI_ACCESS_TOKEN") or os.getenv("GOOGLE_ACCESS_TOKEN"), help="Google Gemini OAuth 2.0 Access Token")

    # Debug and logging flags
    parser.add_argument("--disable-jira", action="store_true", help="Disable all Jira-related operations")
    parser.add_argument("--disable-confluence", action="store_true", help="Disable all Confluence-related operations")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose status updates (INFO level)")
    parser.add_argument("--debug", "-d", action="store_true", help="Enable detailed debug logging (DEBUG level)")
    parser.add_argument("--log-file", dest="log_file", default="refiner.log", help="Path to the log file (default: refiner.log)")
    parser.add_argument("--emit-events", dest="emit_events", action="store_true", help="Emit machine-readable workflow events to stdout")
    parser.add_argument("--events-file", dest="events_file", help="Optional JSONL file to write workflow events")

    args = parser.parse_args(argv)

    # Initialize logging
    setup_logging(verbose=args.verbose, debug=args.debug, log_file=args.log_file)
    agent_role_overrides = _parse_agent_role_overrides(args.agent_role)
    emitter = EventEmitter(enabled=args.emit_events, file_path=args.events_file)

    def _run_with_events(workflow_name: str, func, *f_args, **f_kwargs) -> int:
        emitter.emit("workflow_selected", {"workflow": workflow_name})
        _emit_stage(emitter, workflow_name, "prepare", "running", progress=5)
        _emit_stage(emitter, workflow_name, "prepare", "completed", progress=10)
        _emit_stage(emitter, workflow_name, "execute", "running", progress=15)
        try:
            result = func(*f_args, **f_kwargs)
        except Exception as exc:
            _emit_stage(emitter, workflow_name, "execute", "failed", progress=100, message=str(exc))
            _emit_stage(emitter, workflow_name, "finalize", "failed", progress=100)
            emitter.emit("workflow_complete", {"workflow": workflow_name, "status": "failed"})
            raise
        status = "completed" if result == 0 else "failed"
        _emit_stage(emitter, workflow_name, "execute", status, progress=90)
        _emit_stage(emitter, workflow_name, "finalize", status, progress=100)
        emitter.emit("workflow_complete", {"workflow": workflow_name, "status": status, "exit_code": result})
        return result

    delivery_flag_set = any(
        [
            args.delivery_config,
            args.delivery_run,
            args.delivery_allow_unfinished,
            args.delivery_enable_interim,
            args.delivery_project_solution,
        ]
    )
    if delivery_flag_set and not args.delivery:
        args.delivery = True
        logger.info("Delivery flags detected without --delivery; enabling delivery mode.")

    if args.requirements_path and not args.project_root:
        parser.error("--requirements requires --project")
    if args.project_output_dir and not args.project_root:
        parser.error("--project-output-dir requires --project")
    if args.delivery and not args.project_root:
        parser.error("--delivery requires --project")

    # Set environment variables from CLI args if provided
    if args.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = args.gemini_api_key
    if args.gemini_access_token:
        os.environ["GEMINI_ACCESS_TOKEN"] = args.gemini_access_token
    if args.disable_jira:
        os.environ["DISABLE_JIRA"] = "1"
    if args.disable_confluence:
        os.environ["DISABLE_CONFLUENCE"] = "1"

    try:
        if args.topic_research:
            if args.clear_research_cache:
                from topic_researcher import RESEARCH_CACHE_ROOT
                import shutil
                if os.path.exists(RESEARCH_CACHE_ROOT):
                    logger.info(f"Clearing research cache at {RESEARCH_CACHE_ROOT}")
                    shutil.rmtree(RESEARCH_CACHE_ROOT)

            return _run_with_events(
                "topic_research",
                _run_topic_research,
                args.topic_research,
                args.output or "researched_document.md",
                llm_provider=args.llm_provider,
                llm_model=args.llm_model,
                ollama_base_url=args.ollama_base_url,
                llm_max_tokens=args.llm_max_tokens,
                llm_temperature=args.llm_temperature,
                llm_timeout=args.llm_timeout,
                llm_reasoning_effort=args.llm_reasoning_effort,
                max_iterations=args.max_iterations,
                context_sources=args.context_sources,
                google_api_key=args.google_api_key,
                google_cse_id=args.google_cse_id,
                fallback_llm_provider=args.fallback_llm_provider,
                fallback_llm_model=args.fallback_llm_model,
                references_output=args.references_output,
                llm_inter_request_gap=args.llm_inter_request_gap,
                cache_ttl_hours=args.research_cache_ttl,
                disable_jira=args.disable_jira,
                disable_confluence=args.disable_confluence,
                agentic_role_overrides=agent_role_overrides,
            )

        if args.delivery:
            logger.info("Running delivery pipeline (project solver disabled for this run).")
            return _run_with_events(
                "delivery_pipeline",
                _run_delivery_pipeline,
                project_root=args.project_root,
                config_path=args.delivery_config,
                output=args.output,
                allow_run=args.delivery_run,
                project_solution_path=args.delivery_project_solution,
                allow_unfinished=args.delivery_allow_unfinished,
                enable_interim=args.delivery_enable_interim,
                llm_provider=args.llm_provider,
                llm_model=args.llm_model,
                fallback_llm_provider=args.fallback_llm_provider,
                fallback_llm_model=args.fallback_llm_model,
                ollama_base_url=args.ollama_base_url,
                llm_max_tokens=args.llm_max_tokens,
                llm_temperature=args.llm_temperature,
                llm_timeout=args.llm_timeout,
                llm_reasoning_effort=args.llm_reasoning_effort,
                codingagent=args.codingagent,
                codingagent_fallback=args.codingagent_fallback,
                codingagent_model=args.codingagent_model,
                codingagent_reasoning_effort=args.codingagent_reasoning_effort,
                project_output_dir=args.project_output_dir,
                solver_fallback_override=(
                    False if args.delivery_no_solver_fallback else (True if args.delivery_solver_fallback else None)
                ),
            )

        if args.project_root:
            return _run_with_events(
                "project_solver",
                _run_project_solver,
                project_root=args.project_root,
                requirements_path=args.requirements_path,
                output=args.output,
                llm_provider=args.llm_provider,
                llm_model=args.llm_model,
                fallback_llm_provider=args.fallback_llm_provider,
                fallback_llm_model=args.fallback_llm_model,
                ollama_base_url=args.ollama_base_url,
                llm_max_tokens=args.llm_max_tokens,
                llm_temperature=args.llm_temperature,
                llm_timeout=args.llm_timeout,
                llm_reasoning_effort=args.llm_reasoning_effort,
                llm_inter_request_gap=args.llm_inter_request_gap,
                allow_run=args.project_run,
                max_steps=args.project_max_steps,
                max_iterations=args.project_iterations,
                project_output_dir=args.project_output_dir,
                codingagent=args.codingagent,
                codingagent_fallback=args.codingagent_fallback,
                codingagent_model=args.codingagent_model,
                codingagent_reasoning_effort=args.codingagent_reasoning_effort,
                agentic_role_overrides=agent_role_overrides,
            )

        if args.analyze_confluence:
            if args.disable_confluence:
                parser.error("Cannot run Confluence analysis because Confluence is disabled via --disable-confluence")
            if not args.space:
                parser.error("--space is required when --analyze-confluence is specified")
            return _run_with_events(
                "confluence_analysis",
                _run_confluence_analysis,
                args.space,
                args.output,
                args.use_rovo,
                llm_provider=args.llm_provider,
                llm_model=args.llm_model,
                fallback_llm_provider=args.fallback_llm_provider,
                fallback_llm_model=args.fallback_llm_model,
                ollama_base_url=args.ollama_base_url,
                selection_path=args.selection,
                tree_depth=args.tree_depth,
                starting_depth=args.starting_depth,
                emit_templates=args.emit_templates,
                templates_dir=args.templates_dir,
                llm_max_tokens=args.llm_max_tokens,
                llm_chunk_size=args.llm_chunk_size,
                llm_temperature=args.llm_temperature,
                llm_timeout=args.llm_timeout,
                llm_reasoning_effort=args.llm_reasoning_effort,
                action_plan=args.action_plan,
                dry_run=args.dry_run,
                post_comments=args.post_comments,
                post_target=args.post_target,
                post_exec_summary=args.post_exec_summary,
                post_page_insights=args.post_page_insights,
                dry_run_post=args.dry_run_post,
                llm_inter_request_gap=args.llm_inter_request_gap
            )

        if args.analyze_jira:
            if args.disable_jira:
                parser.error("Cannot run Jira analysis because Jira is disabled via --disable-jira")
            # Output default for Jira analyser if none specified
            if not args.projects and not args.jql:
                # Allow empty selection; jira_analysis will fall back to discovery or safe default JQL
                pass
            return _run_with_events(
                "jira_analysis",
                _run_jira_analysis,
                projects=args.projects,
                jql=args.jql,
                output=(args.output or "jira_report.html"),
                llm_provider=args.llm_provider,
                llm_model=args.llm_model,
                fallback_llm_provider=args.fallback_llm_provider,
                fallback_llm_model=args.fallback_llm_model,
                ollama_base_url=args.ollama_base_url,
                selection_path=args.selection,
                llm_max_tokens=args.llm_max_tokens,
                llm_chunk_size=args.llm_chunk_size,
                llm_temperature=args.llm_temperature,
                llm_timeout=args.llm_timeout,
                llm_reasoning_effort=args.llm_reasoning_effort,
                action_plan=args.action_plan,
                dry_run=args.dry_run,
                post_comments=args.post_comments,
                post_target=args.post_target,
                dry_run_post=args.dry_run_post,
                llm_inter_request_gap=args.llm_inter_request_gap
            )

        if args.disable_jira:
            logger.info("Jira is disabled; skipping default Jira stats workflow.")
            return 0

        return _run_with_events("jira_stats", _run_jira_workflow)
    finally:
        emitter.close()


if __name__ == "__main__":
    raise SystemExit(run())
