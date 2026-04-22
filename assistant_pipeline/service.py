"""Business logic for extracted assistant and RAG routes."""

from __future__ import annotations

import queue
import time
import uuid
from typing import Any, Dict, List, Optional

from llm_providers import LLMError

from assistant_pipeline.cache import (
    lookup_semantic_cache,
    semantic_cache_policy_from_config,
    store_semantic_cache,
)
from assistant_pipeline.contracts import ServiceError, ServiceResult
from assistant_pipeline.dependencies import AssistantPipelineDependencies
from assistant_pipeline.ingestion.artifact_store import (
    delete_versioned_collection_artifacts,
    load_index_artifact,
    versioned_index_artifact_path,
    write_versioned_index_artifact,
)
from assistant_pipeline.memory.conversation_store import (
    append_turn,
    conversation_id_from_payload,
    ensure_conversation,
    recent_turns,
)
from assistant_pipeline.memory.query_rewriter import QueryRewrite, rewrite_query
from assistant_pipeline.retrieval import (
    dense_artifact_path_for_index,
    grade_retrieval_coverage,
    hybrid_retrieval_policy_from_config,
    hybrid_retrieval_scope_fragment,
    merge_retrieval_matches,
    plan_retrieval_retry,
    retrieval_coverage_policy_from_config,
    retrieval_coverage_scope_fragment,
    retrieval_planner_policy_from_config,
    retrieval_planner_scope_fragment,
    retrieve_matches,
)
from assistant_pipeline.security import (
    apply_input_guard,
    apply_output_guard,
    apply_rag_source_guard,
    assistant_security_policy_from_config,
    build_assistant_reply_payload,
)
from assistant_pipeline.routing import (
    assistant_routing_policy_from_config,
    build_assistant_form_fill_system_prompt,
    build_assistant_rag_mcp_system_prompt,
    build_assistant_requirements_system_prompt,
    build_playground_plan_system_prompt,
    resolve_route_intent,
)
from assistant_pipeline.tracing.recorder import TraceRecorder


def _maybe_record_rag_query(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    name: str,
    route: str,
    query_text: str,
    rewritten_query: str = "",
    top_k: int,
    match_count: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    store = deps.get_rag_metadata_store()
    if store is None or not owner or not name:
        return
    try:
        active = store.get_active_version(owner, name) or {}
        store.record_query_audit(
            owner,
            name,
            route=route,
            query_text=query_text,
            rewritten_query=rewritten_query or query_text,
            top_k=top_k,
            match_count=match_count,
            version_id=str(active.get("active_version_id") or "").strip() or None,
            metadata=metadata,
        )
    except Exception as exc:  # pragma: no cover - best effort only
        deps.logger.debug("RAG query metadata skipped for %s/%s: %s", route, name, exc)


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _serialise_subtask(task: Any) -> Dict[str, Any]:
    if task is None:
        return {}
    if hasattr(task, "to_dict"):
        try:
            return dict(task.to_dict(include_result=False))
        except Exception:
            pass
    payload: Dict[str, Any] = {}
    for field in (
        "task_id",
        "owner",
        "action",
        "scope_type",
        "scope_id",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "timeout_sec",
    ):
        value = getattr(task, field, None)
        if value is not None:
            payload[field] = value
    return payload


def _assistant_runtime(deps: AssistantPipelineDependencies) -> Dict[str, Any]:
    runtime = dict(deps.get_assistant_runtime_config() or {})
    return {
        "request_capacity": runtime.get("request_capacity"),
        "capacity_wait_sec": float(runtime.get("capacity_wait_sec") or 0.0),
    }


def _assistant_security_policy(deps: AssistantPipelineDependencies):
    return assistant_security_policy_from_config(deps.get_assistant_security_config())


def _assistant_routing_policy(deps: AssistantPipelineDependencies):
    return assistant_routing_policy_from_config(deps.get_assistant_routing_config())


def _assistant_cache_policy(deps: AssistantPipelineDependencies):
    return semantic_cache_policy_from_config(deps.get_assistant_cache_config())


def _assistant_retrieval_policy(deps: AssistantPipelineDependencies):
    return hybrid_retrieval_policy_from_config(deps.get_assistant_retrieval_config())


def _assistant_retrieval_coverage_policy(deps: AssistantPipelineDependencies):
    return retrieval_coverage_policy_from_config(deps.get_assistant_retrieval_config())


def _assistant_retrieval_planner_policy(deps: AssistantPipelineDependencies):
    return retrieval_planner_policy_from_config(deps.get_assistant_retrieval_config())


def _assistant_retrieval_scope_fragment(deps: AssistantPipelineDependencies) -> str:
    return ":".join(
        [
            hybrid_retrieval_scope_fragment(_assistant_retrieval_policy(deps)),
            retrieval_coverage_scope_fragment(_assistant_retrieval_coverage_policy(deps)),
            retrieval_planner_scope_fragment(_assistant_retrieval_planner_policy(deps)),
        ]
    )


def _resolve_assistant_intent(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    route: str,
    payload: Dict[str, Any],
    is_marketing_assistant: bool = False,
    has_rag: bool = False,
    has_mcp: bool = False,
):
    policy = _assistant_routing_policy(deps)
    stage_started = time.monotonic()
    decision = resolve_route_intent(
        route=route,
        payload=payload,
        policy=policy,
        is_marketing_assistant=is_marketing_assistant,
        has_rag=has_rag,
        has_mcp=has_mcp,
    )
    if policy.enabled:
        trace.record_span(
            "intent_route",
            stage_started,
            metadata={
                "intent_id": decision.intent_id,
                "prompt_profile": decision.prompt_profile,
                "cacheable": decision.cacheable,
                **decision.metadata,
            },
        )
    return decision


def _active_rag_version_id(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    name: str,
) -> str:
    metadata_store = deps.get_rag_metadata_store()
    if metadata_store is None or not owner or not name:
        return ""
    try:
        active = metadata_store.get_active_version(owner, name) or {}
    except Exception as exc:  # pragma: no cover - best effort only
        deps.logger.debug("RAG active-version lookup skipped for %s/%s: %s", owner, name, exc)
        return ""
    return str(active.get("active_version_id") or "").strip()


def _load_active_rag_index(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    name: str,
):
    metadata_store = deps.get_rag_metadata_store()
    active: Dict[str, Any] = {}
    if metadata_store is not None and owner and name:
        try:
            active = dict(metadata_store.get_active_version(owner, name) or {})
        except Exception as exc:  # pragma: no cover - best effort only
            deps.logger.debug("RAG active artefact lookup skipped for %s/%s: %s", owner, name, exc)
    artifact_path = str(active.get("artifact_path") or "").strip()
    if artifact_path:
        index = load_index_artifact(artifact_path)
        if index is not None:
            return index
        deps.logger.debug("RAG active artefact load failed for %s/%s from %s", owner, name, artifact_path)
    version_id = str(active.get("active_version_id") or "").strip()
    rag_store = deps.get_rag_store()
    artifact_root = str(getattr(rag_store, "root", "") or "").strip()
    if version_id and artifact_root:
        version_path = versioned_index_artifact_path(artifact_root, owner, name, version_id)
        if version_path and version_path != artifact_path:
            index = load_index_artifact(version_path)
            if index is not None:
                return index
            deps.logger.debug("RAG version artefact load failed for %s/%s from %s", owner, name, version_path)
    return rag_store.load_index(owner, name)


def _rag_query_cache_scope(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    name: str,
    top_k: int,
    min_score: float,
    retrieval_scope: str,
) -> str:
    version_id = _active_rag_version_id(deps, owner=owner, name=name)
    if not version_id:
        return ""
    return (
        f"rag_query:{name}:{version_id}:top_k={int(top_k)}:min_score={float(min_score):.4f}:"
        f"{retrieval_scope}"
    )


def _assistant_rag_cache_scope(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    rag_index_name: str,
    top_k: int,
    prompt_profile: str,
    provider_name: str,
    model_name: str,
    retrieval_scope: str,
) -> str:
    version_id = _active_rag_version_id(deps, owner=owner, name=rag_index_name)
    if not version_id:
        return ""
    return (
        f"assistant_rag_mcp:{rag_index_name}:{version_id}:top_k={int(top_k)}:"
        f"profile={prompt_profile}:provider={provider_name or 'default'}:model={model_name or 'default'}:"
        f"{retrieval_scope}"
    )


def _lookup_semantic_cache(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    owner: str,
    route: str,
    intent: str,
    scope_key: str,
    query_text: str,
):
    policy = _assistant_cache_policy(deps)
    if not (policy.enabled and scope_key and query_text):
        return None
    stage_started = time.monotonic()
    result = lookup_semantic_cache(
        deps.get_assistant_cache_store(),
        owner=owner,
        route=route,
        intent=intent,
        scope_key=scope_key,
        query_text=query_text,
        policy=policy,
    )
    trace.record_span(
        "cache_lookup",
        stage_started,
        metadata={
            "enabled": policy.enabled,
            "scope_key": scope_key,
            "candidate_count": result.candidate_count,
            "hit": bool(result.hit),
            "similarity": result.hit.similarity if result.hit else 0.0,
        },
    )
    return result


def _store_semantic_cache(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    owner: str,
    route: str,
    intent: str,
    scope_key: str,
    query_text: str,
    response_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    policy = _assistant_cache_policy(deps)
    if not (policy.enabled and scope_key and query_text):
        return
    stage_started = time.monotonic()
    result = store_semantic_cache(
        deps.get_assistant_cache_store(),
        owner=owner,
        route=route,
        intent=intent,
        scope_key=scope_key,
        query_text=query_text,
        response_payload=response_payload,
        policy=policy,
        metadata=metadata,
    )
    trace.record_span(
        "cache_store",
        stage_started,
        metadata={
            "enabled": policy.enabled,
            "scope_key": scope_key,
            **result.metadata,
        },
    )


def _rag_query_response_from_cache(payload: Dict[str, Any], *, original_query: str) -> Dict[str, Any]:
    cached_payload = dict(payload or {})
    cached_payload["query"] = original_query
    return cached_payload


def _retrieve_rag_matches(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    index: Any,
    query_text: str,
    top_k: int,
    min_score: float,
    rewritten: bool,
    stage_name: str = "rag_search",
    stage_metadata: Optional[Dict[str, Any]] = None,
):
    policy = _assistant_retrieval_policy(deps)
    stage_started = time.monotonic()
    result = retrieve_matches(
        index,
        query_text,
        limit=top_k,
        min_score=min_score,
        policy=policy,
    )
    trace.record_span(
        stage_name,
        stage_started,
        metadata={
            "match_count": len(result.matches),
            "top_k": top_k,
            "min_score": min_score,
            "rewritten": rewritten,
            **result.metadata,
            **dict(stage_metadata or {}),
        },
    )
    return result


def _grade_retrieval_coverage(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    query_text: str,
    matches: List[Any],
):
    policy = _assistant_retrieval_coverage_policy(deps)
    grade = grade_retrieval_coverage(query_text, matches, policy)
    if policy.enabled:
        stage_started = time.monotonic()
        trace.record_span("coverage_grade", stage_started, metadata=grade.metadata)
    return grade


def _plan_retrieval_retry(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    query_text: str,
    grade: Any,
):
    policy = _assistant_retrieval_planner_policy(deps)
    plan = plan_retrieval_retry(query_text, grade, policy)
    if policy.enabled and not grade.sufficient:
        stage_started = time.monotonic()
        trace.record_span("retrieval_plan", stage_started, metadata=plan.metadata)
    return plan


def _run_retrieval_loop(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    index: Any,
    query_text: str,
    top_k: int,
    min_score: float,
    rewritten: bool,
) -> Dict[str, Any]:
    coverage_policy = _assistant_retrieval_coverage_policy(deps)
    initial = _retrieve_rag_matches(
        deps,
        trace,
        index=index,
        query_text=query_text,
        top_k=top_k,
        min_score=min_score,
        rewritten=rewritten,
    )
    initial_grade = _grade_retrieval_coverage(
        deps,
        trace,
        query_text=query_text,
        matches=list(initial.matches),
    )
    plan = _plan_retrieval_retry(
        deps,
        trace,
        query_text=query_text,
        grade=initial_grade,
    )
    if not plan.queries:
        return {
            "matches": list(initial.matches),
            "grade": initial_grade,
            "initial_grade": initial_grade,
            "retry_queries": (),
            "retried": False,
            "coverage_enabled": coverage_policy.enabled,
        }
    retry_match_groups: List[List[Any]] = [list(initial.matches)]
    for retry_index, retry_query in enumerate(plan.queries, start=1):
        retry = _retrieve_rag_matches(
            deps,
            trace,
            index=index,
            query_text=retry_query,
            top_k=top_k,
            min_score=min_score,
            rewritten=rewritten,
            stage_name="rag_retry_search",
            stage_metadata={
                "retry_index": retry_index,
                "retry_query_chars": len(retry_query),
            },
        )
        retry_match_groups.append(list(retry.matches))
    merged_matches = merge_retrieval_matches(retry_match_groups, limit=top_k)
    final_grade = _grade_retrieval_coverage(
        deps,
        trace,
        query_text=query_text,
        matches=merged_matches,
    )
    return {
        "matches": merged_matches,
        "grade": final_grade,
        "initial_grade": initial_grade,
        "retry_queries": tuple(plan.queries),
        "retried": True,
        "coverage_enabled": coverage_policy.enabled,
    }


def _retrieval_loop_metadata(loop_result: Dict[str, Any]) -> Dict[str, Any]:
    grade = loop_result.get("grade")
    metadata: Dict[str, Any] = {}
    if bool(loop_result.get("coverage_enabled")) and grade is not None and getattr(grade, "status", ""):
        metadata["coverage_status"] = str(getattr(grade, "status", "") or "")
        metadata["coverage_ratio"] = float(getattr(grade, "coverage_ratio", 0.0) or 0.0)
    retry_queries = tuple(loop_result.get("retry_queries") or ())
    if retry_queries:
        metadata["retry_used"] = True
        metadata["retry_query_count"] = len(retry_queries)
    return metadata


def _retrieval_refusal_answer(grade: Any, *, collection_name: str = "") -> str:
    answer = "I do not have enough retrieved evidence to answer that reliably from the current indexed sources."
    missing_terms = [str(term).strip() for term in getattr(grade, "missing_terms", ()) if str(term).strip()]
    if missing_terms:
        answer = f"{answer} Missing evidence areas: {', '.join(missing_terms[:4])}."
    elif collection_name:
        answer = f"{answer} I need more relevant material in '{collection_name}' or a narrower question."
    return answer


def _guard_input(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    route: str,
    payload: Dict[str, Any],
    text_fields: tuple[str, ...] = (),
    message_field: str = "",
    use_legacy_guardrail: bool = True,
    policy: Optional[Any] = None,
):
    stage_started = time.monotonic()
    security_policy = policy or _assistant_security_policy(deps)
    result = apply_input_guard(
        route=route,
        payload=payload,
        policy=security_policy,
        text_fields=text_fields,
        message_field=message_field,
        guardrail_scan=deps.guardrail_scan,
        use_legacy_guardrail=use_legacy_guardrail,
    )
    trace.record_span(
        "input_guard",
        stage_started,
        status="blocked" if result.blocked_reason else "success",
        metadata=result.metadata,
    )
    if result.blocked_reason:
        raise ServiceError("guardrail_blocked", payload={"details": result.blocked_reason})
    return result


def _guard_rag_sources(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    route: str,
    sources: List[Dict[str, Any]],
    policy: Optional[Any] = None,
) -> None:
    security_policy = policy or _assistant_security_policy(deps)
    if not (security_policy.input.policy_enabled and security_policy.input.validate_rag_source_urls):
        return
    stage_started = time.monotonic()
    result = apply_rag_source_guard(
        route=route,
        sources=sources,
        policy=security_policy,
    )
    trace.record_span(
        "input_guard",
        stage_started,
        status="blocked" if result.blocked_reason else "success",
        metadata=result.metadata,
    )
    if result.blocked_reason:
        raise ServiceError("guardrail_blocked", payload={"details": result.blocked_reason})


def _guard_output(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    route: str,
    response_payload: Dict[str, Any],
    policy: Optional[Any] = None,
) -> Dict[str, Any]:
    stage_started = time.monotonic()
    try:
        result = apply_output_guard(
            route=route,
            response_payload=response_payload,
            policy=policy or _assistant_security_policy(deps),
        )
    except ServiceError as exc:
        trace.record_span("output_guard", stage_started, status="failed", metadata={"route": route, "error_code": exc.code})
        raise
    trace.record_span("output_guard", stage_started, metadata=result.metadata)
    return result.payload


def _build_guarded_reply_payload(
    deps: AssistantPipelineDependencies,
    trace: TraceRecorder,
    *,
    route: str,
    reply_text: str,
    provider: str,
    model: str,
    request_payload: Dict[str, Any],
    policy: Optional[Any] = None,
) -> Dict[str, Any]:
    stage_started = time.monotonic()
    try:
        result = build_assistant_reply_payload(
            route=route,
            reply_text=reply_text,
            provider=provider,
            model=model,
            request_payload=request_payload,
            policy=policy or _assistant_security_policy(deps),
            reply_payload_builder=deps.assistant_reply_payload,
        )
    except ServiceError as exc:
        trace.record_span("output_guard", stage_started, status="failed", metadata={"route": route, "error_code": exc.code})
        raise
    trace.record_span("output_guard", stage_started, metadata=result.metadata)
    return result.payload


def _predict_with_capacity(
    deps: AssistantPipelineDependencies,
    provider: Any,
    *,
    messages: List[Dict[str, Any]],
    system: str,
    temperature: Any,
    max_tokens: Any,
    reasoning_effort: Any,
) -> Any:
    runtime = _assistant_runtime(deps)
    capacity = runtime.get("request_capacity")
    wait_seconds = float(runtime.get("capacity_wait_sec") or 0.0)
    if not deps.acquire_request_capacity(capacity, wait_seconds):
        raise ServiceError("assistant_capacity_unavailable", status_code=503)
    try:
        return provider.predict(
            messages=messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError("llm_request_failed", details=str(exc), status_code=400) from exc
    finally:
        try:
            capacity.release()
        except Exception:
            pass


def _rewrite_retrieval_query(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    conversation_id: Optional[str],
    query_text: str,
    trace: TraceRecorder,
) -> QueryRewrite:
    if not conversation_id:
        return QueryRewrite(
            original_query=str(query_text or "").strip(),
            retrieval_query=str(query_text or "").strip(),
            rewritten=False,
            reason="no_conversation_id",
            history_turns=0,
        )
    stage_started = time.monotonic()
    history = recent_turns(deps, owner=owner, conversation_id=conversation_id, limit=8)
    rewrite = rewrite_query(query_text, history)
    trace.record_span(
        "rewrite_query",
        stage_started,
        metadata={
            "rewritten": rewrite.rewritten,
            "history_turns": rewrite.history_turns,
            "reason": rewrite.reason,
            "anchor_chars": len(rewrite.anchor_text),
        },
    )
    return rewrite


def _rag_build_settings(
    deps: AssistantPipelineDependencies,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    rag_cfg = dict(deps.get_rag_config() or {})
    name = str(payload.get("name") or "default").strip()
    if not name:
        raise ServiceError("name_required")
    return {
        "name": name,
        "rag_cfg": rag_cfg,
        "chunk_size": deps.safe_int(payload.get("chunk_size"), int(rag_cfg.get("default_chunk_size") or 1200)),
        "chunk_overlap": deps.safe_int(payload.get("chunk_overlap"), int(rag_cfg.get("default_chunk_overlap") or 200)),
        "max_chunks": deps.safe_int(payload.get("max_chunks"), int(rag_cfg.get("default_max_chunks") or 2000)),
        "async_builds_enabled": bool(rag_cfg.get("async_index_builds")),
        "build_timeout_sec": float(rag_cfg.get("build_timeout_sec") or 90.0),
    }


def _should_queue_rag_build(payload: Dict[str, Any], settings: Dict[str, Any]) -> bool:
    if "async" in payload:
        return _is_truthy(payload.get("async"))
    if "queued" in payload:
        return _is_truthy(payload.get("queued"))
    return bool(settings.get("async_builds_enabled"))


def _rag_collection_version_id(payload: Dict[str, Any]) -> str:
    version_id = str(payload.get("_rag_version_id") or payload.get("version_id") or "").strip()
    return version_id or uuid.uuid4().hex


def _record_collection_build_failure(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    name: str,
    version_id: str,
    code: str,
    details: str = "",
    status: str = "failed",
) -> None:
    metadata_store = deps.get_rag_metadata_store()
    if metadata_store is None or not owner or not name or not version_id:
        return
    try:
        metadata_store.fail_collection_build(
            owner,
            name,
            version_id=version_id,
            status=status,
            metadata={
                "error_code": code,
                "error_detail": details,
            },
        )
    except Exception as exc:  # pragma: no cover - best effort only
        deps.logger.debug("RAG build failure metadata skipped for %s/%s: %s", owner, name, exc)


def _build_rag_collection(
    deps: AssistantPipelineDependencies,
    *,
    owner: str,
    payload: Dict[str, Any],
    trace: TraceRecorder,
    route: str,
    version_id: str = "",
) -> Dict[str, Any]:
    settings = _rag_build_settings(deps, payload)
    security_policy = _assistant_security_policy(deps)
    name = str(settings["name"])
    rag_cfg = dict(settings["rag_cfg"])
    chunk_size = int(settings["chunk_size"])
    chunk_overlap = int(settings["chunk_overlap"])
    max_chunks = int(settings["max_chunks"])
    version_id = version_id or _rag_collection_version_id(payload)
    stage_started = time.monotonic()
    sources = deps.coerce_rag_sources(payload)
    trace.record_span("coerce_sources", stage_started, metadata={"source_count": len(sources)})
    if not sources:
        raise ServiceError("sources_required")
    _guard_rag_sources(
        deps,
        trace,
        route=route,
        sources=sources,
        policy=security_policy,
    )
    metadata_store = deps.get_rag_metadata_store()
    if metadata_store is not None:
        try:
            metadata_store.start_collection_build(
                owner,
                name,
                version_id=version_id,
                status="building",
                metadata={
                    "route": route,
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    "max_chunks": max_chunks,
                    "source_count": len(sources),
                },
            )
        except Exception as exc:  # pragma: no cover - best effort only
            deps.logger.debug("RAG build start metadata skipped for %s/%s: %s", owner, name, exc)

    stage_started = time.monotonic()
    docs = deps.build_rag_documents(
        sources,
        max_docs=int(rag_cfg.get("max_docs") or 60),
        max_doc_bytes=int(rag_cfg.get("max_doc_bytes") or 600000),
    )
    trace.record_span("build_documents", stage_started, metadata={"document_count": len(docs)})
    if not docs:
        raise ServiceError("no_documents")

    stage_started = time.monotonic()
    index = deps.build_rag_index(
        name=name,
        documents=docs,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        max_chunks=max_chunks,
    )
    chunk_count = len(getattr(index, "chunks", []) or [])
    trace.record_span("build_index", stage_started, metadata={"chunk_count": chunk_count, "version_id": version_id})

    version_artifact_path = ""
    version_dense_artifact_path = ""
    rag_store = deps.get_rag_store()
    artifact_root = str(getattr(rag_store, "root", "") or "").strip()
    if artifact_root:
        stage_started = time.monotonic()
        version_artifact_path = write_versioned_index_artifact(artifact_root, owner, name, version_id, index)
        version_dense_artifact_path = dense_artifact_path_for_index(version_artifact_path)
        trace.record_span(
            "save_version_artifact",
            stage_started,
            metadata={
                "artifact_path": version_artifact_path,
                "dense_artifact_path": version_dense_artifact_path,
                "version_id": version_id,
            },
        )

    stage_started = time.monotonic()
    active_artifact_path = rag_store.save_index(owner, index)
    active_dense_artifact_path = dense_artifact_path_for_index(active_artifact_path)
    trace.record_span(
        "publish_index",
        stage_started,
        metadata={"artifact_path": active_artifact_path, "dense_artifact_path": active_dense_artifact_path},
    )

    if metadata_store is not None:
        stage_started = time.monotonic()
        metadata_store.record_collection_version(
            owner,
            name,
            artifact_path=version_artifact_path or active_artifact_path,
            source_count=len(sources),
            documents=docs,
            chunks=getattr(index, "chunks", []) or [],
            metadata={
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "max_chunks": max_chunks,
                "documents": len(docs),
                "chunks": chunk_count,
                "active_artifact_path": active_artifact_path,
                "active_dense_artifact_path": active_dense_artifact_path,
                "version_dense_artifact_path": version_dense_artifact_path,
            },
            version_id=version_id,
        )
        trace.record_span("record_metadata", stage_started, metadata={"document_count": len(docs), "version_id": version_id})

    return {
        "name": name,
        "documents": len(docs),
        "chunks": chunk_count,
        "version_id": version_id,
        "artifact_path": version_artifact_path or active_artifact_path,
        "active_artifact_path": active_artifact_path,
    }


def rag_collection_build(deps: AssistantPipelineDependencies, *, user: Optional[str], payload: Dict[str, Any]) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    name = str(payload.get("name") or "default").strip()
    version_id = _rag_collection_version_id(payload)
    trace = TraceRecorder(
        deps,
        owner=user,
        route="rag_collection_build",
        intent="rag_collection_build",
        request_meta={"name": name, "version_id": version_id},
    )
    try:
        settings = _rag_build_settings(deps, payload)
        name = str(settings["name"])
        response_payload = _build_rag_collection(
            deps,
            owner=user,
            payload=payload,
            trace=trace,
            route="rag_collection_build",
            version_id=version_id,
        )
        response_payload["status"] = "ready"
        trace.finish(status="success", response_meta=response_payload)
        return ServiceResult(response_payload)
    except ServiceError as exc:
        _record_collection_build_failure(
            deps,
            owner=user,
            name=name,
            version_id=version_id,
            code=exc.code,
            details=exc.details or str(exc),
        )
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        _record_collection_build_failure(
            deps,
            owner=user,
            name=name,
            version_id=version_id,
            code="rag_collection_build_failed",
            details=str(exc),
        )
        trace.finish(status="failed", error_code="rag_collection_build_failed", error_detail=str(exc))
        raise


def rag_indexes(deps: AssistantPipelineDependencies, *, user: Optional[str]) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    trace = TraceRecorder(
        deps,
        owner=user,
        route="rag_indexes",
        intent="rag_indexes",
        request_meta={},
    )
    try:
        indexes = deps.get_rag_store().list_indexes(user)
        trace.finish(status="success", response_meta={"index_count": len(indexes)})
        return ServiceResult({"indexes": indexes})
    except ServiceError as exc:
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        trace.finish(status="failed", error_code="rag_indexes_failed", error_detail=str(exc))
        raise


def rag_index_create(deps: AssistantPipelineDependencies, *, user: Optional[str], payload: Dict[str, Any]) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    name = str(payload.get("name") or "default").strip()
    trace = TraceRecorder(
        deps,
        owner=user,
        route="rag_index_create",
        intent="rag_index_create",
        request_meta={"name": name},
    )
    try:
        settings = _rag_build_settings(deps, payload)
        security_policy = _assistant_security_policy(deps)
        name = str(settings["name"])
        version_id = _rag_collection_version_id(payload)
        if _should_queue_rag_build(payload, settings):
            sources = deps.coerce_rag_sources(payload)
            if not sources:
                raise ServiceError("sources_required")
            _guard_rag_sources(
                deps,
                trace,
                route="rag_index_create",
                sources=sources,
                policy=security_policy,
            )
            if deps.submit_subtask is None:
                raise ServiceError("rag_async_build_unavailable", status_code=503)
            metadata_store = deps.get_rag_metadata_store()
            if metadata_store is not None:
                try:
                    metadata_store.start_collection_build(
                        user,
                        name,
                        version_id=version_id,
                        status="queued",
                        metadata={
                            "route": "rag_index_create",
                            "source_count": len(sources),
                            "chunk_size": settings["chunk_size"],
                            "chunk_overlap": settings["chunk_overlap"],
                            "max_chunks": settings["max_chunks"],
                        },
                    )
                except Exception as exc:  # pragma: no cover - best effort only
                    deps.logger.debug("Queued RAG build metadata skipped for %s/%s: %s", user, name, exc)
            stage_started = time.monotonic()
            subtask_payload = dict(payload)
            subtask_payload["_rag_version_id"] = version_id
            try:
                task = deps.submit_subtask(
                    owner=user,
                    action="rag_collection_build",
                    payload=subtask_payload,
                    scope_type="rag_collection",
                    scope_id=name,
                    timeout_sec=float(settings["build_timeout_sec"]),
                )
            except queue.Full as exc:
                _record_collection_build_failure(
                    deps,
                    owner=user,
                    name=name,
                    version_id=version_id,
                    code="subtask_capacity_unavailable",
                    details=str(exc) or "Subtask queue is full.",
                )
                trace.record_span("queue_build", stage_started, status="failed", metadata={"version_id": version_id})
                raise ServiceError("subtask_capacity_unavailable", status_code=503) from exc
            except Exception as exc:
                _record_collection_build_failure(
                    deps,
                    owner=user,
                    name=name,
                    version_id=version_id,
                    code="rag_build_queue_failed",
                    details=str(exc),
                )
                trace.record_span("queue_build", stage_started, status="failed", metadata={"version_id": version_id})
                raise ServiceError("rag_build_queue_failed", details=str(exc), status_code=503) from exc
            task_payload = _serialise_subtask(task)
            trace.record_span("queue_build", stage_started, metadata={"task_id": task_payload.get("task_id"), "version_id": version_id})
            response_payload = {
                "status": "queued",
                "name": name,
                "version_id": version_id,
                "task": task_payload,
            }
            trace.finish(status="success", response_meta=response_payload)
            return ServiceResult(response_payload, status_code=202)

        build_result = _build_rag_collection(
            deps,
            owner=user,
            payload=payload,
            trace=trace,
            route="rag_index_create",
            version_id=version_id,
        )
        response_payload = {
            "name": build_result["name"],
            "documents": build_result["documents"],
            "chunks": build_result["chunks"],
        }
        trace.finish(status="success", response_meta=response_payload)
        return ServiceResult(response_payload)
    except ServiceError as exc:
        if exc.code not in {
            "sources_required",
            "subtask_capacity_unavailable",
            "rag_async_build_unavailable",
            "rag_build_queue_failed",
        }:
            _record_collection_build_failure(
                deps,
                owner=user,
                name=name,
                version_id=version_id if "version_id" in locals() else "",
                code=exc.code,
                details=exc.details or str(exc),
            )
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        _record_collection_build_failure(
            deps,
            owner=user,
            name=name,
            version_id=version_id if "version_id" in locals() else _rag_collection_version_id(payload),
            code="rag_index_create_failed",
            details=str(exc),
        )
        trace.finish(status="failed", error_code="rag_index_create_failed", error_detail=str(exc))
        raise


def rag_index_delete(deps: AssistantPipelineDependencies, *, user: Optional[str], name: str) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    cleaned_name = str(name or "").strip()
    trace = TraceRecorder(
        deps,
        owner=user,
        route="rag_index_delete",
        intent="rag_index_delete",
        request_meta={"name": cleaned_name},
    )
    try:
        if not cleaned_name:
            raise ServiceError("name_required")
        rag_store = deps.get_rag_store()
        deleted = rag_store.delete_index(user, cleaned_name)
        versioned_deleted = delete_versioned_collection_artifacts(
            str(getattr(rag_store, "root", "") or ""),
            user,
            cleaned_name,
        )
        if not deleted and not versioned_deleted:
            raise ServiceError("not_found", status_code=404)
        metadata_store = deps.get_rag_metadata_store()
        if metadata_store is not None:
            stage_started = time.monotonic()
            metadata_store.delete_collection(user, cleaned_name)
            trace.record_span("delete_metadata", stage_started, metadata={"name": cleaned_name})
        response_payload = {"status": "deleted", "name": cleaned_name}
        trace.finish(status="success", response_meta=response_payload)
        return ServiceResult(response_payload)
    except ServiceError as exc:
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        trace.finish(status="failed", error_code="rag_index_delete_failed", error_detail=str(exc))
        raise


def rag_query(deps: AssistantPipelineDependencies, *, user: Optional[str], payload: Dict[str, Any]) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    name = str(payload.get("name") or "default").strip()
    query = str(payload.get("query") or "").strip()
    conversation_id = conversation_id_from_payload(payload)
    trace = TraceRecorder(
        deps,
        owner=user,
        route="rag_query",
        intent="rag_query",
        conversation_id=conversation_id,
        request_meta={"name": name, "query_chars": len(query)},
    )
    try:
        if not query:
            raise ServiceError("query_required")
        decision = _resolve_assistant_intent(
            deps,
            trace,
            route="rag_query",
            payload=payload,
        )
        rewrite = _rewrite_retrieval_query(
            deps,
            owner=user,
            conversation_id=conversation_id,
            query_text=query,
            trace=trace,
        )
        retrieval_scope = _assistant_retrieval_scope_fragment(deps)
        retrieval_query = rewrite.retrieval_query or query
        top_k = deps.safe_int(payload.get("top_k"), 5) or 5
        min_score = float(payload.get("min_score") or 0.0)
        ensure_conversation(
            deps,
            owner=user,
            conversation_id=conversation_id,
            route="rag_query",
            title=query[:120],
            metadata={"mode": "rag_query", "index": name},
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="user",
            route="rag_query",
            content=query,
            rewritten_query=retrieval_query if rewrite.rewritten else "",
            request_payload=payload,
            metadata={"index": name, "mode": "rag_query"},
        )
        cache_scope = _rag_query_cache_scope(
            deps,
            owner=user,
            name=name,
            top_k=top_k,
            min_score=min_score,
            retrieval_scope=retrieval_scope,
        )
        cache_lookup = _lookup_semantic_cache(
            deps,
            trace,
            owner=user,
            route="rag_query",
            intent=decision.intent_id,
            scope_key=cache_scope,
            query_text=retrieval_query,
        )
        if cache_lookup and cache_lookup.hit is not None:
            response_payload = _guard_output(
                deps,
                trace,
                route="rag_query",
                response_payload=_rag_query_response_from_cache(cache_lookup.hit.payload, original_query=query),
            )
            _maybe_record_rag_query(
                deps,
                owner=user,
                name=name,
                route="rag_query",
                query_text=query,
                rewritten_query=retrieval_query,
                top_k=top_k,
                match_count=len(response_payload.get("matches") or []),
                metadata={
                    "min_score": min_score,
                    "conversation_id": conversation_id,
                    "cache_hit": True,
                    "cache_similarity": cache_lookup.hit.similarity,
                },
            )
            append_turn(
                deps,
                owner=user,
                conversation_id=conversation_id,
                role="assistant",
                route="rag_query",
                content=str(response_payload.get("context") or ""),
                response_payload=response_payload,
                metadata={"index": name, "match_count": len(response_payload.get("matches") or []), "mode": "rag_query", "cache_hit": True},
            )
            trace.finish(
                status="success",
                cache_hit=True,
                response_meta={
                    "match_count": len(response_payload.get("matches") or []),
                    "top_k": top_k,
                },
            )
            return ServiceResult(response_payload)
        index = _load_active_rag_index(deps, owner=user, name=name)
        if not index:
            raise ServiceError("index_not_found", status_code=404)
        retrieval_loop = _run_retrieval_loop(
            deps,
            trace,
            index=index,
            query_text=retrieval_query,
            top_k=top_k,
            min_score=min_score,
            rewritten=rewrite.rewritten,
        )
        matches = list(retrieval_loop.get("matches") or [])
        context = deps.render_rag_context(matches)
        response_payload = _guard_output(
            deps,
            trace,
            route="rag_query",
            response_payload={
                "name": name,
                "query": query,
                "matches": [deps.serialize_rag_match(match) for match in matches],
                "context": context,
            },
        )
        _maybe_record_rag_query(
            deps,
            owner=user,
            name=name,
            route="rag_query",
            query_text=query,
            rewritten_query=retrieval_query,
            top_k=top_k,
            match_count=len(matches),
            metadata={
                "min_score": min_score,
                "conversation_id": conversation_id,
                **_retrieval_loop_metadata(retrieval_loop),
            },
        )
        _store_semantic_cache(
            deps,
            trace,
            owner=user,
            route="rag_query",
            intent=decision.intent_id,
            scope_key=cache_scope,
            query_text=retrieval_query,
            response_payload=response_payload,
            metadata={"name": name, "top_k": top_k, "min_score": min_score},
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="assistant",
            route="rag_query",
            content=context,
            response_payload=response_payload,
            metadata={"index": name, "match_count": len(matches), "mode": "rag_query"},
        )
        trace.finish(
            status="success",
            cache_hit=False,
            response_meta={
                "match_count": len(matches),
                "top_k": top_k,
                **_retrieval_loop_metadata(retrieval_loop),
            },
        )
        return ServiceResult(response_payload)
    except ServiceError as exc:
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        trace.finish(status="failed", error_code="rag_query_failed", error_detail=str(exc))
        raise


def assistant_rag_mcp(deps: AssistantPipelineDependencies, *, user: Optional[str], payload: Dict[str, Any]) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    prompt = str(payload.get("prompt") or "").strip()
    conversation_id = conversation_id_from_payload(payload)
    trace = TraceRecorder(
        deps,
        owner=user,
        route="assistant_rag_mcp",
        intent="assistant_rag_mcp",
        conversation_id=conversation_id,
        request_meta={"prompt_chars": len(prompt)},
    )
    try:
        if not prompt:
            raise ServiceError("prompt_required")
        security_policy = _assistant_security_policy(deps)
        input_result = _guard_input(
            deps,
            trace,
            route="assistant_rag_mcp",
            payload=payload,
            text_fields=("prompt",),
            use_legacy_guardrail=False,
            policy=security_policy,
        )
        payload = input_result.payload
        prompt = str(payload.get("prompt") or "").strip()

        rag_cfg = payload.get("rag") if isinstance(payload.get("rag"), dict) else {}
        rag_index_name = str(rag_cfg.get("index") or "").strip()
        mcp_cfg = payload.get("mcp") if isinstance(payload.get("mcp"), dict) else {}
        decision = _resolve_assistant_intent(
            deps,
            trace,
            route="assistant_rag_mcp",
            payload=payload,
            has_rag=bool(rag_index_name),
            has_mcp=bool(mcp_cfg),
        )
        rewrite = _rewrite_retrieval_query(
            deps,
            owner=user,
            conversation_id=conversation_id,
            query_text=prompt,
            trace=trace,
        ) if rag_index_name else QueryRewrite(
            original_query=prompt,
            retrieval_query=prompt,
            rewritten=False,
            reason="rag_disabled",
            history_turns=0,
        )

        ensure_conversation(
            deps,
            owner=user,
            conversation_id=conversation_id,
            route="assistant_rag_mcp",
            title=prompt[:120],
            metadata={"mode": "assistant_rag_mcp"},
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="user",
            route="assistant_rag_mcp",
            content=prompt,
            rewritten_query=rewrite.retrieval_query if rewrite.rewritten else "",
            request_payload=payload,
            metadata={"mode": "assistant_rag_mcp"},
        )

        provider_hint = payload.get("provider") or payload.get("llm_provider")
        model_hint = payload.get("model") or payload.get("llm_model")
        settings = deps.resolve_llm_settings(user=user, provider_hint=provider_hint, model_hint=model_hint)
        top_k = deps.safe_int(rag_cfg.get("top_k"), 4) or 4
        retrieval_scope = _assistant_retrieval_scope_fragment(deps)
        cache_scope = ""
        cache_query_text = ""
        cache_lookup = None
        if decision.cacheable and rag_index_name:
            cache_scope = _assistant_rag_cache_scope(
                deps,
                owner=user,
                rag_index_name=rag_index_name,
                top_k=top_k,
                prompt_profile=decision.prompt_profile,
                provider_name=str(settings.get("provider") or provider_hint or ""),
                model_name=str(settings.get("model") or model_hint or ""),
                retrieval_scope=retrieval_scope,
            )
            cache_query_text = prompt
            retrieval_basis = rewrite.retrieval_query or prompt
            if retrieval_basis and retrieval_basis != prompt:
                cache_query_text = f"{prompt}\n\nretrieval:{retrieval_basis}"
            cache_lookup = _lookup_semantic_cache(
                deps,
                trace,
                owner=user,
                route="assistant_rag_mcp",
                intent=decision.intent_id,
                scope_key=cache_scope,
                query_text=cache_query_text,
            )
            if cache_lookup and cache_lookup.hit is not None:
                response_payload = _guard_output(
                    deps,
                    trace,
                    route="assistant_rag_mcp",
                    response_payload=dict(cache_lookup.hit.payload),
                    policy=security_policy,
                )
                _maybe_record_rag_query(
                    deps,
                    owner=user,
                    name=rag_index_name,
                    route="assistant_rag_mcp",
                    query_text=prompt,
                    rewritten_query=rewrite.retrieval_query or prompt,
                    top_k=top_k,
                    match_count=len(response_payload.get("rag_matches") or []),
                    metadata={
                        "mcp_enabled": False,
                        "conversation_id": conversation_id,
                        "cache_hit": True,
                        "cache_similarity": cache_lookup.hit.similarity,
                    },
                )
                append_turn(
                    deps,
                    owner=user,
                    conversation_id=conversation_id,
                    role="assistant",
                    route="assistant_rag_mcp",
                    content=str(response_payload.get("answer") or ""),
                    provider=str(settings.get("provider") or provider_hint or ""),
                    model=str(settings.get("model") or model_hint or ""),
                    response_payload=response_payload,
                    metadata={"rag_index": rag_index_name, "has_mcp": False, "cache_hit": True},
                )
                trace.finish(
                    status="success",
                    provider=str(settings.get("provider") or provider_hint or ""),
                    model=str(settings.get("model") or model_hint or ""),
                    cache_hit=True,
                    response_meta={
                        "rag_match_count": len(response_payload.get("rag_matches") or []),
                        "has_mcp": False,
                    },
                )
                return ServiceResult(response_payload)

        routing_policy = _assistant_routing_policy(deps)
        skills_started = time.monotonic()
        skills = deps.select_skills(prompt, limit=routing_policy.skill_hint_limit if routing_policy.enabled else 4)
        skills_hint = deps.format_skill_brief(skills)
        capabilities_hint = deps.capability_summary(
            max_items=routing_policy.capability_hint_max_items if routing_policy.enabled else 4
        )
        trace.record_span(
            "route_hints",
            skills_started,
            metadata={
                "skill_count": len(skills or []),
                "has_capabilities": bool(capabilities_hint),
                "intent_id": decision.intent_id,
            },
        )

        rag_matches: List[Dict[str, Any]] = []
        rag_context = ""
        retrieval_loop_metadata: Dict[str, Any] = {}
        if rag_index_name:
            index = _load_active_rag_index(deps, owner=user, name=rag_index_name)
            if not index:
                raise ServiceError("rag_index_not_found", status_code=404)
            retrieval_loop = _run_retrieval_loop(
                deps,
                trace,
                index=index,
                query_text=rewrite.retrieval_query or prompt,
                top_k=top_k,
                min_score=0.0,
                rewritten=rewrite.rewritten,
            )
            matches = list(retrieval_loop.get("matches") or [])
            retrieval_loop_metadata = _retrieval_loop_metadata(retrieval_loop)
            rag_matches = [deps.serialize_rag_match(match) for match in matches]
            rag_context = deps.render_rag_context(matches)
            _maybe_record_rag_query(
                deps,
                owner=user,
                name=rag_index_name,
                route="assistant_rag_mcp",
                query_text=prompt,
                rewritten_query=rewrite.retrieval_query or prompt,
                top_k=top_k,
                match_count=len(matches),
                metadata={
                    "mcp_enabled": bool(payload.get("mcp")),
                    "conversation_id": conversation_id,
                    **retrieval_loop_metadata,
                },
            )
            coverage_policy = _assistant_retrieval_coverage_policy(deps)
            if coverage_policy.enabled and coverage_policy.refuse_on_insufficient and not mcp_cfg:
                grade = retrieval_loop.get("grade")
                if grade is not None and not bool(getattr(grade, "sufficient", False)):
                    response_payload = _guard_output(
                        deps,
                        trace,
                        route="assistant_rag_mcp",
                        response_payload={
                            "answer": _retrieval_refusal_answer(grade, collection_name=rag_index_name),
                            "rag_matches": rag_matches,
                            "mcp_result": None,
                        },
                        policy=security_policy,
                    )
                    append_turn(
                        deps,
                        owner=user,
                        conversation_id=conversation_id,
                        role="assistant",
                        route="assistant_rag_mcp",
                        content=str(response_payload.get("answer") or ""),
                        provider="rule",
                        model="retrieval_coverage_refusal",
                        response_payload=response_payload,
                        metadata={
                            "rag_index": rag_index_name,
                            "has_mcp": False,
                            **retrieval_loop_metadata,
                            "refused": True,
                        },
                    )
                    trace.finish(
                        status="success",
                        provider="rule",
                        model="retrieval_coverage_refusal",
                        cache_hit=False,
                        response_meta={
                            "rag_match_count": len(rag_matches),
                            "has_mcp": False,
                            **retrieval_loop_metadata,
                            "refused": True,
                        },
                    )
                    return ServiceResult(response_payload)

        mcp_result = None
        if mcp_cfg:
            if not deps.is_admin_user(user):
                raise ServiceError("mcp_forbidden", status_code=403)
            server_name = str(mcp_cfg.get("server") or "").strip()
            tool_name = str(mcp_cfg.get("tool") or "").strip()
            if not server_name or not tool_name:
                raise ServiceError("mcp_server_and_tool_required")
            arguments = mcp_cfg.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                raise ServiceError("mcp_invalid_arguments")
            stage_started = time.monotonic()
            try:
                mcp_result = deps.mcp_execute(
                    user,
                    server_name,
                    "call",
                    lambda client: client.call_tool(tool_name, arguments or {}),
                    audit_details={"tool": tool_name, "source": "assistant"},
                    runtime_from_result=lambda _result: {"last_tool": tool_name},
                )
            except KeyError as exc:
                trace.record_span("mcp_call", stage_started, status="failed", metadata={"server": server_name, "tool": tool_name})
                raise ServiceError("mcp_server_not_found", status_code=404) from exc
            except ServiceError:
                raise
            except Exception as exc:
                trace.record_span("mcp_call", stage_started, status="failed", metadata={"server": server_name, "tool": tool_name})
                raise ServiceError("mcp_request_failed", details=str(exc), status_code=400) from exc
            trace.record_span("mcp_call", stage_started, metadata={"server": server_name, "tool": tool_name})

        try:
            provider = deps.build_request_llm_provider(
                user,
                settings,
                workflow="assistant_rag_mcp",
                role="assistant",
            )
        except Exception as exc:
            raise ServiceError("llm_init_failed", details=str(exc), status_code=400) from exc

        system = build_assistant_rag_mcp_system_prompt(
            decision,
            capabilities_hint=capabilities_hint,
            skills_hint=skills_hint,
            rag_context_present=bool(rag_context),
        )
        user_blocks = [f"User request:\n{prompt}"]
        if rag_context:
            user_blocks.append(f"RAG context:\n{rag_context}")
        if mcp_result is not None:
            user_blocks.append(f"MCP result:\n{deps.json_dumps(mcp_result, ensure_ascii=True)}")
        user_text = "\n\n".join(user_blocks)

        stage_started = time.monotonic()
        response = _predict_with_capacity(
            deps,
            provider,
            messages=[{"role": "user", "content": user_text}],
            system=system,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens", 1200),
            reasoning_effort=payload.get("reasoning_effort")
            or payload.get("llm_reasoning_effort")
            or settings.get("reasoning_effort"),
        )
        trace.record_span(
            "generate",
            stage_started,
            metadata={
                "provider": str(getattr(response, "provider", None) or settings.get("provider") or ""),
                "model": str(getattr(response, "model", None) or settings.get("model") or ""),
            },
        )
        answer = str(getattr(response, "text", "") or "")
        response_payload = _guard_output(
            deps,
            trace,
            route="assistant_rag_mcp",
            response_payload={
                "answer": answer,
                "rag_matches": rag_matches,
                "mcp_result": mcp_result,
            },
            policy=security_policy,
        )
        _store_semantic_cache(
            deps,
            trace,
            owner=user,
            route="assistant_rag_mcp",
            intent=decision.intent_id,
            scope_key=cache_scope,
            query_text=cache_query_text,
            response_payload=response_payload,
            metadata={
                "rag_index": rag_index_name,
                "top_k": top_k,
                "prompt_profile": decision.prompt_profile,
                "provider": str(getattr(response, "provider", None) or settings.get("provider") or ""),
                "model": str(getattr(response, "model", None) or settings.get("model") or ""),
            },
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="assistant",
            route="assistant_rag_mcp",
            content=answer,
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            response_payload=response_payload,
            metadata={"rag_index": rag_index_name, "has_mcp": bool(mcp_result)},
        )
        trace.finish(
            status="success",
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            cache_hit=False,
            response_meta={"rag_match_count": len(rag_matches), "has_mcp": bool(mcp_result), **retrieval_loop_metadata},
        )
        return ServiceResult(response_payload)
    except ServiceError as exc:
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        trace.finish(status="failed", error_code="assistant_rag_mcp_failed", error_detail=str(exc))
        raise


def assistant_requirements(deps: AssistantPipelineDependencies, *, user: Optional[str], payload: Dict[str, Any]) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    prompt = str(payload.get("prompt") or "").strip()
    requirements_text = str(payload.get("requirements_text") or "").strip()
    mode = str(payload.get("mode") or "ask").strip().lower()
    conversation_id = conversation_id_from_payload(payload)
    trace = TraceRecorder(
        deps,
        owner=user,
        route="assistant_requirements",
        intent=f"assistant_requirements:{mode}",
        conversation_id=conversation_id,
        request_meta={
            "mode": mode,
            "prompt_chars": len(prompt),
            "requirements_chars": len(requirements_text),
        },
    )
    try:
        security_policy = _assistant_security_policy(deps)
        gesture_mode, avatar_mode, _office_flag = deps.stt_motion_context(payload)
        input_result = _guard_input(
            deps,
            trace,
            route="assistant_requirements",
            payload=payload,
            text_fields=("requirements_text", "prompt"),
            message_field="messages",
            policy=security_policy,
        )
        payload = input_result.payload
        prompt = str(payload.get("prompt") or "").strip()
        requirements_text = str(payload.get("requirements_text") or "").strip()
        raw_prompt = prompt
        messages = input_result.messages
        marketing_context = ""
        marketing_vocab_hint = ""

        if mode not in {"ask", "draft"}:
            raise ServiceError("invalid_mode")

        ensure_conversation(
            deps,
            owner=user,
            conversation_id=conversation_id,
            route="assistant_requirements",
            scope=mode,
            title=(prompt or requirements_text)[:120],
            metadata={"mode": mode},
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="user",
            route="assistant_requirements",
            content=prompt or requirements_text,
            prompt_text=prompt,
            requirements_text=requirements_text,
            request_payload=payload,
            metadata={"mode": mode},
        )

        provider_hint = payload.get("provider") or payload.get("llm_provider")
        model_hint = payload.get("model") or payload.get("llm_model")
        settings = deps.resolve_llm_settings(
            user=user,
            provider_hint=provider_hint,
            model_hint=model_hint,
        )
        if not payload.get("assistant_profile") and settings.get("assistant_profile"):
            payload = dict(payload)
            payload["assistant_profile"] = settings.get("assistant_profile")
        reasoning_effort = str(
            payload.get("reasoning_effort")
            or payload.get("llm_reasoning_effort")
            or settings.get("reasoning_effort")
            or "medium"
        ).strip().lower()
        assistant_memory_enabled = bool(settings.get("assistant_use_memory", True))
        is_marketing_assistant = deps.is_marketing_assistant_request(payload, requirements_text)
        decision = _resolve_assistant_intent(
            deps,
            trace,
            route="assistant_requirements",
            payload=payload,
            is_marketing_assistant=is_marketing_assistant,
        )
        assistant_memory_scope = deps.assistant_memory_scope(
            "assistant_requirements",
            mode=mode,
            profile="marketing" if is_marketing_assistant else "requirements",
        )

        if mode == "ask" and is_marketing_assistant and deps.is_simple_greeting(prompt):
            greeting_payload = _build_guarded_reply_payload(
                deps,
                trace,
                route="assistant_requirements",
                reply_text=(
                    "Hello! I'm the NeuralMimicry marketing assistant, here to help with questions about our "
                    "neuromorphic AI products and services. What would you like to know?"
                ),
                provider="rule",
                model="greeting_fastpath",
                request_payload=payload,
                policy=security_policy,
            )
            append_turn(
                deps,
                owner=user,
                conversation_id=conversation_id,
                role="assistant",
                route="assistant_requirements",
                content=str(greeting_payload.get("reply") or ""),
                response_payload=greeting_payload,
                metadata={"mode": mode, "assistant_profile": "marketing"},
            )
            trace.finish(status="success", provider="rule", model="greeting_fastpath", response_meta={"mode": mode, "greeting_fastpath": True})
            return ServiceResult(greeting_payload)

        stt_learning_store = deps.get_stt_learning_store()
        if is_marketing_assistant and stt_learning_store:
            stage_started = time.monotonic()
            try:
                seed_query = prompt or requirements_text
                marketing_vocab_hint = stt_learning_store.build_prompt_hint(context=seed_query, max_terms=24)
                matches = stt_learning_store.query_context(seed_query, limit=4)
                if matches:
                    chunks = []
                    for match in matches:
                        source = str(match.get("source") or "knowledge")
                        text = str(match.get("text") or "").strip()
                        if not text:
                            continue
                        chunks.append(f"[{source}]\\n{text}")
                    marketing_context = "\\n\\n".join(chunks)
                trace.record_span("marketing_context", stage_started, metadata={"match_count": len(matches or [])})
            except Exception as exc:
                trace.record_span("marketing_context", stage_started, status="failed")
                deps.logger.debug("Marketing assistant knowledge lookup skipped: %s", exc)

        try:
            provider = deps.build_request_llm_provider(
                user,
                settings,
                workflow="assistant_requirements",
                role="assistant",
            )
        except LLMError as exc:
            raise ServiceError("llm_unavailable", details=str(exc), status_code=400) from exc
        except Exception as exc:
            raise ServiceError("llm_init_failed", details=str(exc), status_code=400) from exc
        if not provider:
            raise ServiceError("llm_unavailable", status_code=400)

        routing_policy = _assistant_routing_policy(deps)
        capabilities_hint = deps.capability_summary(
            max_items=min(3, routing_policy.capability_hint_max_items) if routing_policy.enabled else 3
        )
        system = build_assistant_requirements_system_prompt(
            decision,
            gesture_mode=gesture_mode,
            capabilities_hint=capabilities_hint,
            marketing_vocab_hint=marketing_vocab_hint,
        )

        chat_messages: List[Dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                chat_messages.append({"role": role, "content": content})

        assistant_memory_context = ""
        use_assistant_ask_memory = False
        if assistant_memory_enabled:
            if mode == "draft":
                assistant_memory_query = deps.assistant_memory_query_text(
                    prompt=prompt,
                    requirements_text=requirements_text,
                    messages=chat_messages,
                    extra_parts=[marketing_context] if marketing_context else None,
                )
                assistant_memory_context = deps.assistant_memory_prompt_block(
                    user,
                    scope=assistant_memory_scope,
                    query_text=assistant_memory_query,
                    header=(
                        "Relevant patterns from this user's earlier successful drafts "
                        "(adapt useful structure, but do not copy stale details blindly):"
                    ),
                )
            else:
                use_assistant_ask_memory = deps.should_use_assistant_ask_memory(
                    prompt,
                    requirements_text=requirements_text,
                    messages=chat_messages,
                    is_marketing_assistant=is_marketing_assistant,
                )
                if use_assistant_ask_memory:
                    assistant_memory_query = deps.assistant_memory_query_text(
                        prompt=prompt,
                        requirements_text=requirements_text,
                        messages=chat_messages,
                    )
                    assistant_memory_context = deps.assistant_memory_prompt_block(
                        user,
                        scope=assistant_memory_scope,
                        query_text=assistant_memory_query,
                        header=(
                            "Relevant prior successful guidance for similar requirements questions "
                            "(reuse useful reasoning, but resolve conflicts using the current notes first):"
                        ),
                        max_chars=1200,
                    )

        if mode == "draft":
            user_text = "Draft a complete requirements document."
            if requirements_text:
                user_text += f"\n\nCurrent notes:\n{requirements_text}"
            if marketing_context:
                user_text += f"\n\nNeuralMimicry context:\n{marketing_context}"
            if assistant_memory_context:
                user_text += f"\n\n{assistant_memory_context}"
            chat_messages.append({"role": "user", "content": user_text})
        else:
            if not prompt:
                raise ServiceError("prompt_required")
            prompt_blocks: List[str] = []
            if requirements_text:
                if is_marketing_assistant:
                    prompt_blocks.append(f"Assistant context:\\n{requirements_text}")
                else:
                    prompt_blocks.append(f"Current requirements notes:\\n{requirements_text}")
            if marketing_context:
                prompt_blocks.append(f"Retrieved NeuralMimicry knowledge:\\n{marketing_context}")
            if assistant_memory_context:
                prompt_blocks.append(assistant_memory_context)
            if is_marketing_assistant:
                prompt_blocks.append(f"User message: {prompt}")
            else:
                prompt_blocks.append(f"User question: {prompt}")
            chat_messages.append({"role": "user", "content": "\\n\\n".join(prompt_blocks)})

        stage_started = time.monotonic()
        response = _predict_with_capacity(
            deps,
            provider,
            messages=chat_messages,
            system=system,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens"),
            reasoning_effort=reasoning_effort,
        )
        trace.record_span(
            "generate",
            stage_started,
            metadata={
                "provider": str(getattr(response, "provider", None) or settings.get("provider") or ""),
                "model": str(getattr(response, "model", None) or settings.get("model") or ""),
                "mode": mode,
            },
        )

        reply_text = str(getattr(response, "text", "") or "")
        if mode == "draft":
            reply_text = deps.ensure_req_register_in_draft(reply_text)
        if mode == "draft" and assistant_memory_enabled:
            deps.record_assistant_memory(
                user,
                scope=assistant_memory_scope,
                prompt_text=raw_prompt or "Draft a complete requirements document.",
                requirements_text=requirements_text,
                reply_text=reply_text,
                extra_notes=[
                    "assistant_profile: marketing" if is_marketing_assistant else "assistant_profile: requirements"
                ],
            )
        elif use_assistant_ask_memory and assistant_memory_enabled:
            deps.record_assistant_memory(
                user,
                scope=assistant_memory_scope,
                prompt_text=raw_prompt,
                requirements_text=requirements_text,
                reply_text=reply_text,
                extra_notes=["assistant_profile: requirements", "mode: ask"],
                metadata={"mode": "ask"},
            )
        if is_marketing_assistant:
            deps.stt_record_learning(raw_prompt, source="assistant_marketing_user")
            deps.stt_record_learning(reply_text, source="assistant_marketing_reply")

        response_payload = _build_guarded_reply_payload(
            deps,
            trace,
            route="assistant_requirements",
            reply_text=reply_text,
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            request_payload=payload,
            policy=security_policy,
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="assistant",
            route="assistant_requirements",
            content=str(response_payload.get("reply") or reply_text),
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            response_payload=response_payload,
            metadata={
                "mode": mode,
                "assistant_profile": decision.prompt_profile,
            },
        )
        trace.finish(
            status="success",
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            response_meta={"mode": mode, "assistant_profile": decision.prompt_profile},
        )
        return ServiceResult(response_payload)
    except ServiceError as exc:
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        trace.finish(status="failed", error_code="assistant_requirements_failed", error_detail=str(exc))
        raise


def assistant_form_fill(deps: AssistantPipelineDependencies, *, user: Optional[str], payload: Dict[str, Any]) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    fields = payload.get("fields") if isinstance(payload.get("fields"), list) else []
    prompt = str(payload.get("prompt") or "").strip()
    workflow = str(payload.get("workflow") or "").strip()
    scope = str(payload.get("scope") or "").strip()
    conversation_id = conversation_id_from_payload(payload)
    trace = TraceRecorder(
        deps,
        owner=user,
        route="assistant_form_fill",
        intent="assistant_form_fill",
        conversation_id=conversation_id,
        request_meta={"field_count": len(fields), "prompt_chars": len(prompt), "workflow": workflow},
    )
    try:
        security_policy = _assistant_security_policy(deps)
        if not isinstance(fields, list) or not fields:
            raise ServiceError("fields_required")

        input_result = _guard_input(
            deps,
            trace,
            route="assistant_form_fill",
            payload=payload,
            text_fields=("prompt",),
            policy=security_policy,
        )
        payload = input_result.payload
        prompt = str(payload.get("prompt") or "").strip()

        ensure_conversation(
            deps,
            owner=user,
            conversation_id=conversation_id,
            route="assistant_form_fill",
            scope=scope,
            title=(workflow or prompt)[:120],
            metadata={"workflow": workflow, "scope": scope},
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="user",
            route="assistant_form_fill",
            content=prompt,
            prompt_text=prompt,
            request_payload=payload,
            metadata={"workflow": workflow, "scope": scope},
        )

        provider_hint = payload.get("provider") or payload.get("llm_provider")
        model_hint = payload.get("model") or payload.get("llm_model")
        settings = deps.resolve_llm_settings(
            user=user,
            provider_hint=provider_hint,
            model_hint=model_hint,
        )
        reasoning_effort = str(
            payload.get("reasoning_effort")
            or payload.get("llm_reasoning_effort")
            or settings.get("reasoning_effort")
            or "medium"
        ).strip().lower()
        assistant_memory_enabled = bool(settings.get("assistant_use_memory", True))
        decision = _resolve_assistant_intent(
            deps,
            trace,
            route="assistant_form_fill",
            payload=payload,
        )
        try:
            provider = deps.build_request_llm_provider(
                user,
                settings,
                workflow="assistant_form_fill",
                role="assistant",
            )
        except Exception as exc:
            raise ServiceError("llm_init_failed", details=str(exc), status_code=400) from exc
        if not provider:
            raise ServiceError("llm_unavailable", status_code=400)

        field_descriptions = []
        allowed_ids = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_id = field.get("id") or field.get("field_id")
            if not field_id:
                continue
            allowed_ids.append(field_id)
            label = field.get("label") or field_id
            ftype = field.get("type") or "text"
            entry = {
                "id": field_id,
                "label": label,
                "type": ftype,
                "value": field.get("value"),
                "options": field.get("options"),
                "description": field.get("description") or "",
            }
            field_descriptions.append(entry)

        assistant_memory_scope = deps.assistant_memory_scope("assistant_form_fill")
        field_context_lines: List[str] = []
        for entry in field_descriptions[:10]:
            field_id = str(entry.get("id") or "").strip()
            label = str(entry.get("label") or "").strip()
            description = str(entry.get("description") or "").strip()
            options = entry.get("options")
            option_preview = ""
            if isinstance(options, list):
                preview_items = [str(item).strip() for item in options[:4] if str(item).strip()]
                if preview_items:
                    option_preview = " options=" + ", ".join(preview_items)
            summary_bits = [bit for bit in [field_id, label, description] if bit]
            if summary_bits:
                field_context_lines.append(" | ".join(summary_bits) + option_preview)
        form_memory_references: List[Dict[str, Any]] = []
        if assistant_memory_enabled:
            form_memory_references = deps.assistant_memory_reference_payload(
                user,
                scope=assistant_memory_scope,
                query_text=deps.assistant_memory_query_text(
                    prompt=prompt,
                    extra_parts=[workflow, scope, " ".join(allowed_ids)] + field_context_lines,
                ),
            )

        routing_policy = _assistant_routing_policy(deps)
        capabilities_hint = deps.capability_summary(
            max_items=min(3, routing_policy.capability_hint_max_items) if routing_policy.enabled else 3
        )
        system = build_assistant_form_fill_system_prompt(
            decision,
            capabilities_hint=capabilities_hint,
        )

        user_text: Dict[str, Any] = {
            "goal": prompt,
            "workflow": workflow,
            "scope": scope,
            "allowed_fields": allowed_ids,
            "fields": field_descriptions,
        }
        if form_memory_references:
            user_text["reference_suggestions"] = form_memory_references

        stage_started = time.monotonic()
        response = _predict_with_capacity(
            deps,
            provider,
            messages=[{"role": "user", "content": deps.json_dumps(user_text)}],
            system=system,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens"),
            reasoning_effort=reasoning_effort,
        )
        trace.record_span(
            "generate",
            stage_started,
            metadata={
                "provider": str(getattr(response, "provider", None) or settings.get("provider") or ""),
                "model": str(getattr(response, "model", None) or settings.get("model") or ""),
            },
        )

        parsed = deps.extract_json_payload(getattr(response, "text", ""))
        if not isinstance(parsed, list):
            raise ServiceError("invalid_llm_response", payload={"details": str(getattr(response, "text", ""))[:500]})

        cleaned = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            field_id = item.get("field_id") or item.get("id")
            if not field_id or field_id not in allowed_ids:
                continue
            cleaned.append(
                {
                    "field_id": field_id,
                    "value": item.get("value"),
                    "rationale": item.get("rationale"),
                }
            )
        if not cleaned:
            raise ServiceError("no_suggestions")

        if assistant_memory_enabled:
            deps.record_assistant_memory(
                user,
                scope=assistant_memory_scope,
                prompt_text=prompt,
                requirements_text=deps.json_dumps({"workflow": workflow, "scope": scope, "fields": allowed_ids[:12]}),
                reply_text=(
                    f"Suggested {len(cleaned)} field value(s) for workflow '{workflow or 'unknown'}' "
                    f"in scope '{scope or 'workflow'}'."
                ),
                extra_notes=[
                    f"workflow: {workflow}" if workflow else "",
                    f"scope: {scope}" if scope else "",
                    ("fields: " + ", ".join(allowed_ids[:10])) if allowed_ids else "",
                ],
                metadata={
                    "workflow": workflow,
                    "scope": scope,
                    "field_ids": allowed_ids[:12],
                    "suggestions": cleaned[:6],
                },
            )

        response_payload = _guard_output(
            deps,
            trace,
            route="assistant_form_fill",
            response_payload={"suggestions": cleaned},
            policy=security_policy,
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="assistant",
            route="assistant_form_fill",
            content=deps.json_dumps(cleaned, ensure_ascii=True),
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            response_payload=response_payload,
            metadata={"workflow": workflow, "scope": scope},
        )
        trace.finish(
            status="success",
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            response_meta={"suggestion_count": len(cleaned), "workflow": workflow},
        )
        return ServiceResult(response_payload)
    except ServiceError as exc:
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        trace.finish(status="failed", error_code="assistant_form_fill_failed", error_detail=str(exc))
        raise


def playground_plan(deps: AssistantPipelineDependencies, *, user: Optional[str], payload: Dict[str, Any]) -> ServiceResult:
    if not user:
        raise ServiceError("unauthorized", status_code=401)
    prompt = str(payload.get("prompt") or "").strip()
    conversation_id = conversation_id_from_payload(payload)
    trace = TraceRecorder(
        deps,
        owner=user,
        route="playground_plan",
        intent="playground_plan",
        conversation_id=conversation_id,
        request_meta={"prompt_chars": len(prompt)},
    )
    try:
        if not prompt:
            raise ServiceError("prompt_required")

        security_policy = _assistant_security_policy(deps)
        input_result = _guard_input(
            deps,
            trace,
            route="playground_plan",
            payload=payload,
            text_fields=("prompt",),
            policy=security_policy,
        )
        payload = input_result.payload
        prompt = str(payload.get("prompt") or "").strip()

        ensure_conversation(
            deps,
            owner=user,
            conversation_id=conversation_id,
            route="playground_plan",
            title=prompt[:120],
            metadata={"mode": "playground"},
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="user",
            route="playground_plan",
            content=prompt,
            prompt_text=prompt,
            request_payload=payload,
            metadata={"mode": "playground"},
        )

        provider_hint = payload.get("provider") or payload.get("llm_provider")
        model_hint = payload.get("model") or payload.get("llm_model")
        settings = deps.resolve_llm_settings(
            user=user,
            provider_hint=provider_hint,
            model_hint=model_hint,
        )
        reasoning_effort = str(
            payload.get("reasoning_effort")
            or payload.get("llm_reasoning_effort")
            or settings.get("reasoning_effort")
            or "medium"
        ).strip().lower()
        assistant_memory_enabled = bool(settings.get("assistant_use_memory", True))
        decision = _resolve_assistant_intent(
            deps,
            trace,
            route="playground_plan",
            payload=payload,
        )
        try:
            provider = deps.build_request_llm_provider(
                user,
                settings,
                workflow="playground_plan",
                role="planner",
            )
        except Exception as exc:
            raise ServiceError("llm_init_failed", details=str(exc), status_code=400) from exc
        if not provider:
            raise ServiceError("llm_unavailable", status_code=400)

        system = build_playground_plan_system_prompt(decision)

        assistant_memory_scope = deps.assistant_memory_scope("playground_plan")
        memory_references: List[Dict[str, Any]] = []
        if assistant_memory_enabled:
            memory_references = deps.assistant_memory_reference_payload(
                user,
                scope=assistant_memory_scope,
                query_text=deps.assistant_memory_query_text(prompt=prompt),
            )
        user_text: Dict[str, Any] = {
            "prompt": prompt,
            "constraints": {
                "speed": "quick",
                "scope": "small",
            },
        }
        if memory_references:
            user_text["reference_patterns"] = memory_references

        stage_started = time.monotonic()
        response = _predict_with_capacity(
            deps,
            provider,
            messages=[{"role": "user", "content": deps.json_dumps(user_text)}],
            system=system,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens", 900),
            reasoning_effort=reasoning_effort,
        )
        trace.record_span(
            "generate",
            stage_started,
            metadata={
                "provider": str(getattr(response, "provider", None) or settings.get("provider") or ""),
                "model": str(getattr(response, "model", None) or settings.get("model") or ""),
            },
        )

        parsed = deps.extract_json_payload(getattr(response, "text", ""))
        if not isinstance(parsed, dict):
            raise ServiceError("invalid_llm_response", payload={"details": str(getattr(response, "text", ""))[:400]})

        summary = str(parsed.get("summary") or "").strip()
        steps_raw = parsed.get("steps") or []
        steps = [str(item).strip() for item in steps_raw if isinstance(item, (str, int, float)) and str(item).strip()]
        requirements_text = str(parsed.get("requirements_text") or "").strip()
        project_name = str(parsed.get("project_name") or "").strip()

        summary = deps.to_uk_english(summary)
        steps = [deps.to_uk_english(step) for step in steps]
        requirements_text = deps.to_uk_english(requirements_text)

        if not project_name:
            project_name = (summary or prompt).strip()
        project_name = project_name[:60] if project_name else "Playground Project"

        if not requirements_text:
            steps_block = "\n".join([f"- {step}" for step in steps]) if steps else ""
            requirements_text = (
                f"{summary or prompt}\n\nKey Features:\n{steps_block}\n\nRequirements Register:\n"
                "- REQ-001: Provide a simple web app\n"
                "- REQ-002: Keep the scope small and fast to build\n"
                "- REQ-003: Include clear, child-friendly UI copy\n"
                "- REQ-004: Add basic navigation between screens\n"
                "- REQ-005: Provide simple progress feedback\n"
                "- REQ-006: Use a clean, responsive layout\n"
            )
            requirements_text = deps.to_uk_english(requirements_text)

        req_count = sum(1 for line in requirements_text.splitlines() if line.strip().startswith("- REQ-"))
        if req_count <= 0:
            req_count = max(1, len(steps)) if steps else 6
        requirements_lower = requirements_text.lower()
        global_titles = deps.global_requirements_titles()
        global_detected = "global requirement" in requirements_lower or "global-" in requirements_lower
        if not global_detected and global_titles:
            global_detected = any(title in requirements_lower for title in global_titles)
        if not global_detected:
            req_count += deps.global_requirements_count()

        playground_cfg = dict(deps.get_playground_config() or {})
        project_iterations = min(
            int(playground_cfg.get("project_max_iterations") or 12),
            max(int(playground_cfg.get("project_min_iterations") or 1), req_count),
        )
        job_payload = {
            "workflow": "project_solver",
            "project_name": project_name,
            "requirements_text": requirements_text,
            "project_run": True,
            "project_max_steps": int(playground_cfg.get("project_max_steps") or 0),
            "project_iterations": project_iterations,
            "llm_provider": settings.get("provider") or provider_hint,
            "llm_model": settings.get("model") or model_hint,
            "llm_reasoning_effort": reasoning_effort,
            "llm_temperature": 0.2,
            "llm_max_tokens": int(playground_cfg.get("llm_max_tokens") or 0),
            "disable_jira": True,
            "disable_confluence": True,
            "action_plan": False,
            "dry_run": False,
            "token_scope": "personal",
            "source": "playground",
        }
        codingagent = payload.get("codingagent")
        if codingagent:
            job_payload["codingagent"] = codingagent
        elif deps.opencode_available_for_playground():
            job_payload["codingagent"] = "opencode"
        token_estimate = deps.estimate_job_tokens(job_payload)
        if assistant_memory_enabled:
            deps.record_assistant_memory(
                user,
                scope=assistant_memory_scope,
                prompt_text=prompt,
                requirements_text=requirements_text,
                reply_text=f"{summary}\n\n{requirements_text}".strip(),
                project_name=project_name,
                steps=steps,
                extra_notes=[f"project_name: {project_name}"] if project_name else None,
            )

        response_payload = _guard_output(
            deps,
            trace,
            route="playground_plan",
            response_payload={
                "summary": summary,
                "steps": steps,
                "project_name": project_name,
                "requirements_text": requirements_text,
                "job_payload": job_payload,
                "token_estimate": token_estimate,
                "provider": str(getattr(response, "provider", None) or settings.get("provider") or ""),
                "model": str(getattr(response, "model", None) or settings.get("model") or ""),
            },
            policy=security_policy,
        )
        append_turn(
            deps,
            owner=user,
            conversation_id=conversation_id,
            role="assistant",
            route="playground_plan",
            content=f"{summary}\n\n{requirements_text}".strip(),
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            response_payload=response_payload,
            metadata={"project_name": project_name},
        )
        trace.finish(
            status="success",
            provider=str(getattr(response, "provider", None) or settings.get("provider") or ""),
            model=str(getattr(response, "model", None) or settings.get("model") or ""),
            response_meta={"project_name": project_name, "token_estimate": token_estimate},
        )
        return ServiceResult(response_payload)
    except ServiceError as exc:
        trace.finish(status="failed", error_code=exc.code, error_detail=exc.details or str(exc))
        raise
    except Exception as exc:
        trace.finish(status="failed", error_code="playground_plan_failed", error_detail=str(exc))
        raise
