"""Dependency container for the extracted assistant pipeline.

The dependencies are intentionally expressed as callables so tests and runtime
monkeypatches on `refiner_web` continue to affect behaviour after extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class AssistantPipelineDependencies:
    """Callable accessors and helpers required by assistant pipeline services."""

    current_user: Callable[[], Optional[str]]
    logger: Any
    json_dumps: Callable[..., str]
    new_trace_id: Callable[[], str]
    get_rag_store: Callable[[], Any]
    get_rag_metadata_store: Callable[[], Optional[Any]]
    get_assistant_conversation_store: Callable[[], Optional[Any]]
    get_assistant_trace_store: Callable[[], Optional[Any]]
    get_assistant_cache_store: Callable[[], Optional[Any]]
    get_stt_learning_store: Callable[[], Optional[Any]]
    get_rag_config: Callable[[], Mapping[str, Any]]
    get_assistant_runtime_config: Callable[[], Mapping[str, Any]]
    get_assistant_security_config: Callable[[], Mapping[str, Any]]
    get_assistant_routing_config: Callable[[], Mapping[str, Any]]
    get_assistant_cache_config: Callable[[], Mapping[str, Any]]
    get_assistant_retrieval_config: Callable[[], Mapping[str, Any]]
    get_playground_config: Callable[[], Mapping[str, Any]]
    safe_int: Callable[[Any, int], int]
    coerce_rag_sources: Callable[[Dict[str, Any]], List[Dict[str, Any]]]
    build_rag_documents: Callable[..., List[Any]]
    build_rag_index: Callable[..., Any]
    render_rag_context: Callable[[List[Any]], str]
    serialize_rag_match: Callable[[Any], Dict[str, Any]]
    capability_summary: Callable[..., str]
    select_skills: Callable[..., Any]
    format_skill_brief: Callable[[Any], str]
    is_admin_user: Callable[[str], bool]
    mcp_execute: Callable[..., Any]
    resolve_llm_settings: Callable[..., Dict[str, Any]]
    build_request_llm_provider: Callable[..., Any]
    acquire_request_capacity: Callable[[Any, float], bool]
    guardrail_scan: Callable[[str], Optional[str]]
    stt_motion_context: Callable[[Optional[Dict[str, Any]]], Tuple[str, str, bool]]
    is_marketing_assistant_request: Callable[[Dict[str, Any], str], bool]
    is_simple_greeting: Callable[[str], bool]
    assistant_memory_scope: Callable[..., str]
    assistant_memory_query_text: Callable[..., str]
    assistant_memory_prompt_block: Callable[..., str]
    assistant_memory_reference_payload: Callable[..., List[Dict[str, Any]]]
    should_use_assistant_ask_memory: Callable[..., bool]
    record_assistant_memory: Callable[..., None]
    assistant_reply_payload: Callable[..., Dict[str, Any]]
    ensure_req_register_in_draft: Callable[[str], str]
    stt_record_learning: Callable[..., None]
    extract_json_payload: Callable[[Any], Any]
    to_uk_english: Callable[[str], str]
    global_requirements_titles: Callable[[], List[str]]
    global_requirements_count: Callable[[], int]
    estimate_job_tokens: Callable[[Dict[str, Any]], int]
    opencode_available_for_playground: Callable[[], bool]
    submit_subtask: Optional[Callable[..., Any]] = None
