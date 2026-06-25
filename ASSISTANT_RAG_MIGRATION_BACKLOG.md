# Assistant / RAG Migration Backlog

## Purpose

This backlog translates the target architecture into concrete engineering work.
It is organised so each step can ship without breaking the current Refiner API.

Status key:

- `[x]` completed in the current codebase
- `[ ]` planned and not yet implemented
- `[~]` partially implemented and requires follow-up

## Completed Foundation

### Assistant / RAG route extraction

- `[x]` Added [assistant_api/assistant_handlers.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_api/assistant_handlers.py)
- `[x]` Added [assistant_api/rag_handlers.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_api/rag_handlers.py)
- `[x]` Added [assistant_pipeline/contracts.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/contracts.py)
- `[x]` Added [assistant_pipeline/dependencies.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/dependencies.py)
- `[x]` Added [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py)
- `[x]` Added [assistant_pipeline/memory/episodic_store.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/memory/episodic_store.py)
- `[x]` Added [assistant_pipeline/memory/conversation_store.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/memory/conversation_store.py)
- `[x]` Added [assistant_pipeline/tracing/recorder.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/tracing/recorder.py)
- `[x]` Added dedicated RAG route registration in [refiner_routes/rag.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner_routes/rag.py)
- `[x]` Removed RAG route registration from [refiner_routes/jobs.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner_routes/jobs.py)
- `[x]` Rewired [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py) to register the extracted handlers
- `[x]` Deleted the superseded inline assistant/RAG route bodies from [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py)
- `[x]` Added pure-Python service coverage in [tests/test_assistant_pipeline_service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_assistant_pipeline_service.py)
- `[x]` Added helper coverage in [tests/test_assistant_pipeline_runtime_helpers.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_assistant_pipeline_runtime_helpers.py)

### Postgres metadata split

- `[x]` Added [central_store/base.py](${NM_LOCAL_REPO_ROOT}/rag_demo/central_store/base.py)
- `[x]` Added [central_store/assistant.py](${NM_LOCAL_REPO_ROOT}/rag_demo/central_store/assistant.py)
- `[x]` Added [central_store/rag.py](${NM_LOCAL_REPO_ROOT}/rag_demo/central_store/rag.py)
- `[x]` Added [central_store/__init__.py](${NM_LOCAL_REPO_ROOT}/rag_demo/central_store/__init__.py)
- `[x]` Extended [refiner/refiner_central_store.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_central_store.py) to expose:
  - `assistant_conversations`
  - `assistant_episodes`
  - `assistant_traces`
  - `assistant_semantic_cache`
  - `rag_metadata`
- `[x]` Added Postgres-backed dual-write for assistant episodic memory in [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py)
- `[x]` Added Postgres-backed fallback reads for assistant episodic memory in [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py)
- `[x]` Added request trace recording and RAG metadata recording inside [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py)
- `[x]` Added pure-Python metadata helper coverage in [tests/test_central_store_metadata_helpers.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_central_store_metadata_helpers.py)

## Phase 1 Follow-up

### Remove dead route logic from the monolith

Files:

- [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py)

Tasks:

- `[x]` Delete the superseded inline bodies for:
  - `rag_indexes`
  - `rag_index_create`
  - `rag_index_delete`
  - `rag_query`
  - `assistant_rag_mcp`
  - `assistant_requirements`
  - `assistant_form_fill`
  - `playground_plan`
- `[x]` Keep only thin wrappers or direct imported handler bindings
- `[~]` Re-ran pure-Python assistant/RAG service tests plus route-registration coverage after removal; Flask-backed integration tests remain environment-dependent

### Extract assistant memory helpers into the pipeline package

Files:

- [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py)
- [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py)
- New: `assistant_pipeline/memory/episodic_store.py`

Tasks:

- `[x]` Move `_assistant_memory_scope`
- `[x]` Move `_assistant_memory_query_text`
- `[x]` Move `_assistant_memory_prompt_block`
- `[x]` Move `_assistant_memory_reference_payload`
- `[x]` Move `_should_use_assistant_ask_memory`
- `[x]` Move `_record_assistant_memory`
- `[x]` Leave [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py) with compatibility shims only

## Phase 2 Follow-up

### Flesh out conversation persistence

Files:

- [central_store/assistant.py](${NM_LOCAL_REPO_ROOT}/rag_demo/central_store/assistant.py)
- [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py)
- New: `assistant_pipeline/memory/conversation_store.py`

Tasks:

- `[x]` Persist conversation headers and turns when `conversation_id` is supplied
- `[x]` Add read APIs for recent turns by conversation id
- `[x]` Add conversation listing by owner and route family
- `[ ]` Decide whether to expose conversation ids in route responses or keep them client-supplied only

### Harden trace observability

Files:

- [central_store/assistant.py](${NM_LOCAL_REPO_ROOT}/rag_demo/central_store/assistant.py)
- [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py)
- New: `assistant_pipeline/tracing/recorder.py`

Tasks:

- `[~]` Record route-level traces and basic stage spans
- `[x]` Move the recorder into [assistant_pipeline/tracing/recorder.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/tracing/recorder.py)
- `[ ]` Add trace span schema for richer timestamps if needed
- `[x]` Add admin/debug read endpoints for trace drill-down
- `[x]` Add admin/debug read endpoints for conversation drill-down
- `[ ]` Add retention and pruning rules for trace rows and raw trace files

### Harden RAG metadata ownership

Files:

- [central_store/rag.py](${NM_LOCAL_REPO_ROOT}/rag_demo/central_store/rag.py)
- [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py)
- New: `assistant_pipeline/retrieval/collection_service.py`

Tasks:

- `[x]` Record collection versions and query audits in Postgres
- `[~]` Add explicit collection status transitions such as `building`, `ready`, `failed`, `deleted`
- `[ ]` Add team-scope ownership support in addition to personal ownership
- `[ ]` Add metadata backfill for pre-existing file-backed RAG indexes

## Phase 3: Async Collection Builds

Files:

- New: `assistant_pipeline/ingestion/source_loader.py`
- New: `assistant_pipeline/ingestion/extractor.py`
- New: `assistant_pipeline/ingestion/chunker.py`
- New: `assistant_pipeline/ingestion/metadata.py`
- New: `assistant_pipeline/ingestion/index_builder.py`
- New: `assistant_pipeline/ingestion/artifact_store.py`
- [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py)

Tasks:

- `[x]` Move `_coerce_rag_sources` out of [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py)
- `[x]` Move `_build_rag_documents` out of [refiner/refiner_web.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_web.py)
- `[x]` Add `rag_collection_build` subtask execution path
- `[~]` Switch `/api/rag/index` from synchronous build to queued build
- `[x]` Version collection artefacts under `job_data/rag/collections/...`
- `[x]` Stage version publication in Postgres before the final active-version switch

Notes:

- Queued builds are now available through `assistant_pipeline.service` and the existing `SubtaskManager`.
- `/api/rag/index` remains synchronous by default to preserve the current contract; queued mode is enabled by request (`"async": true`) or by the feature flag `REFINER_RAG_ASYNC_INDEX_BUILDS=1`.
- Successful builds still publish the legacy flat active index file for compatibility while also writing immutable version artefacts under `job_data/rag/collections/...`.
- Publication is now staged explicitly in Postgres before the compatibility mirror is finalised, so active-version metadata and the legacy mirror follow a clearer lifecycle.

## Phase 4: Security Envelope

Files:

- New: `assistant_pipeline/security/input_guard.py`
- New: `assistant_pipeline/security/output_guard.py`
- New: `assistant_pipeline/security/policies.py`
- [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py)

Tasks:

- `[x]` Replace direct `_guardrail_scan` call sites in the extracted assistant routes with a typed request-policy layer
- `[~]` Block user-supplied message roles that impersonate `system` or `developer`
- `[~]` Add SSRF-safe URL validation for remote RAG sources
- `[~]` Add output validation and PII redaction before responses leave the assistant pipeline
- `[~]` Add explicit refusal policies for prompt-leak requests
- `[x]` Add explicit refusal policies for unsafe tool use

Notes:

- `assistant_pipeline/security/input_guard.py`, `assistant_pipeline/security/output_guard.py`, and `assistant_pipeline/security/policies.py` now exist and are wired through [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py).
- `assistant_pipeline/security/tool_guard.py` now applies unsafe MCP tool-use policy checks before tool execution and emits rollout telemetry through the existing Postgres LLM telemetry roll-up.
- Response-shape validation is now active for the extracted assistant/RAG routes.
- Stricter message-role blocking, prompt-leak blocking, remote RAG URL validation, and output PII redaction are intentionally behind rollout flags to preserve the current contract by default:
  - `REFINER_ASSISTANT_SECURITY_POLICY_ENABLED`
  - `REFINER_ASSISTANT_SECURITY_STRICT_MESSAGE_ROLES`
  - `REFINER_ASSISTANT_SECURITY_BLOCK_PROMPT_LEAK`
  - `REFINER_ASSISTANT_MCP_ADMIN_ONLY`
  - `REFINER_ASSISTANT_BLOCK_UNSAFE_TOOL_REQUESTS`
  - `REFINER_ASSISTANT_SECURITY_VALIDATE_RAG_SOURCE_URLS`
  - `REFINER_ASSISTANT_OUTPUT_REDACT_PII`
  - `REFINER_ASSISTANT_OUTPUT_VALIDATE_SHAPES`

## Phase 5: Query Rewriting, Intent Routing, and Semantic Cache

Files:

- New: `assistant_pipeline/routing/intent_router.py`
- New: `assistant_pipeline/routing/prompt_profiles.py`
- New: `assistant_pipeline/memory/query_rewriter.py`
- New: `assistant_pipeline/cache/semantic_cache.py`
- [central_store/assistant.py](${NM_LOCAL_REPO_ROOT}/rag_demo/central_store/assistant.py)
- [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py)

Tasks:

- `[x]` Add standalone-query rewriting for follow-up turns
- `[x]` Add route strategies for requirements, marketing, form fill, playground, RAG, and RAG+MCP
- `[x]` Add read-only semantic cache with collection-version-aware invalidation
- `[x]` Extend trace metadata with cache-hit and rewrite signals

Notes:

- `assistant_pipeline/routing/intent_router.py` and `assistant_pipeline/routing/prompt_profiles.py` are now wired through [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py).
- Routing remains feature-flagged by default to preserve current behaviour until explicitly enabled:
  - `REFINER_ASSISTANT_INTENT_ROUTING_ENABLED`
  - `REFINER_ASSISTANT_ROUTING_SKILL_HINT_LIMIT`
  - `REFINER_ASSISTANT_ROUTING_CAPABILITY_MAX_ITEMS`
- `central_store/assistant.py` now provides `nm_assistant_semantic_cache` via `PostgresAssistantSemanticCacheStore`, exposed through [refiner/refiner_central_store.py](${NM_LOCAL_REPO_ROOT}/rag_demo/refiner/refiner_central_store.py).
- The first cache rollout is intentionally conservative:
  - Postgres-backed rather than file-backed
  - version-aware through the active RAG version id
  - enabled only for `rag_query` and `assistant_rag_mcp` without MCP
  - matched using deterministic normalisation plus token/string similarity
  - still passed through the output guard on cache hits
- Added focused regression coverage in:
  - [tests/test_assistant_pipeline_routing.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_assistant_pipeline_routing.py)
  - [tests/test_assistant_pipeline_semantic_cache.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_assistant_pipeline_semantic_cache.py)

## Phase 6: Hybrid Retrieval and Self-Correction

Files:

- New: `assistant_pipeline/retrieval/sparse_retriever.py`
- New: `assistant_pipeline/retrieval/dense_retriever.py`
- New: `assistant_pipeline/retrieval/hybrid_retriever.py`
- New: `assistant_pipeline/retrieval/reranker.py`
- New: `assistant_pipeline/retrieval/coverage_grader.py`
- New: `assistant_pipeline/retrieval/retrieval_planner.py`
- New: `assistant_pipeline/retrieval/citation_enricher.py`

Tasks:

- `[x]` Keep the existing lexical path as the sparse baseline
- `[~]` Add a persisted dense retrieval backend using existing storage first
- `[x]` Fuse sparse and dense candidates
- `[x]` Add reranking and coverage grading
- `[x]` Retry once with decomposed sub-queries before refusing
- `[x]` Bind answer claims to exact source chunks and locators

Notes:

- `assistant_pipeline/retrieval/sparse_retriever.py`, `assistant_pipeline/retrieval/dense_retriever.py`, and `assistant_pipeline/retrieval/hybrid_retriever.py` now exist and are wired through [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py).
- `assistant_pipeline/retrieval/coverage_grader.py`, `assistant_pipeline/retrieval/retrieval_planner.py`, and `assistant_pipeline/retrieval/reranker.py` now exist and are wired through [assistant_pipeline/service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/assistant_pipeline/service.py).
- The current hybrid retrieval rollout is intentionally conservative:
  - disabled by default behind feature flags
  - keeps `RagIndex.search()` as the sparse baseline
  - uses a deterministic hashed token/character projection for the dense side
  - persists dense sidecar artefacts beside both immutable versioned indexes and legacy active index files
  - prefers immutable active-version artefacts from Postgres metadata when assistant routes load a collection
  - fuses sparse and dense candidates without changing the public Refiner API
  - includes the active retrieval strategy in semantic-cache scope keys so sparse-only and hybrid cache entries do not collide
- The first self-correcting retrieval rollout is also conservative:
  - disabled by default behind feature flags
  - grades evidence deterministically from retrieved text coverage
  - reranks candidates deterministically using query-term, phrase, and metadata signals
  - performs at most one retry/decomposition pass with richer cleaned clauses and quoted-phrase variants
  - refuses early only for pure RAG answers when evidence remains insufficient
  - keeps MCP-backed flows permissive so live tool data can still answer when RAG coverage is weak
- Citation claim binding now runs in the response path with exact chunk ids plus locator metadata preserved for grounded claims.
- Broader multi-pass planning remains follow-up work.
- Added focused regression coverage in:
  - [tests/test_assistant_pipeline_retrieval.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_assistant_pipeline_retrieval.py)
  - [tests/test_assistant_pipeline_service.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_assistant_pipeline_service.py)
  - [tests/test_rag_engine_core.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_rag_engine_core.py)

## Phase 7: Testing and Operational Hardening

Files:

- New: `tests/evals/assistant_rag/`
- [tests/test_route_registration.py](${NM_LOCAL_REPO_ROOT}/rag_demo/tests/test_route_registration.py)
- New: `tests/test_assistant_rag_pipeline_core.py`
- New: `tests/test_central_store_assistant_schema.py`

Tasks:

- `[x]` Route registration test now asserts the standalone RAG routes
- `[x]` Add pure-Python tests for the extracted assistant pipeline service with fake dependencies
- `[x]` Add tests for central-store assistant/RAG metadata helpers that do not require a live database
- `[x]` Add golden evaluation fixtures for query rewriting, citation coverage, and refusal correctness
- `[x]` Add rollout checks covering both file-backed and Postgres-backed assistant memory paths

## Deployment Backlog

Files:

- [ASSISTANT_RAG_TARGET_ARCHITECTURE.md](${NM_LOCAL_REPO_ROOT}/rag_demo/ASSISTANT_RAG_TARGET_ARCHITECTURE.md)
- [continuum_tenant_refiner_site.yml](${SWARMHPC_ROOT}/swarmhpc/ansible/continuum_tenant_refiner_site.yml)
- [defaults/main.yml](${SWARMHPC_ROOT}/swarmhpc/ansible/roles/continuum_tenant_refiner/defaults/main.yml)

Tasks:

- `[x]` Add feature flags for the assistant pipeline, security rollout, traces, async RAG build, and future hybrid retrieval
- `[x]` Expose first-class assistant/STT/job-action/subtask concurrency tuning variables in the Refiner Ansible role defaults
- `[ ]` Add secret-env support for any new embedding or cache credentials required later
- `[ ]` Decide whether the Refiner PVC size should be increased before async collection builds are enabled by default
- `[ ]` Decide whether Postgres pool sizes should increase once trace and RAG metadata volume grows

Current assistant-pipeline rollout flags now include:

- `REFINER_ASSISTANT_SECURITY_POLICY_ENABLED`
- `REFINER_ASSISTANT_SECURITY_STRICT_MESSAGE_ROLES`
- `REFINER_ASSISTANT_SECURITY_BLOCK_PROMPT_LEAK`
- `REFINER_ASSISTANT_SECURITY_VALIDATE_RAG_SOURCE_URLS`
- `REFINER_ASSISTANT_OUTPUT_REDACT_PII`
- `REFINER_ASSISTANT_OUTPUT_VALIDATE_SHAPES`
- `REFINER_ASSISTANT_INTENT_ROUTING_ENABLED`
- `REFINER_ASSISTANT_ROUTING_SKILL_HINT_LIMIT`
- `REFINER_ASSISTANT_ROUTING_CAPABILITY_MAX_ITEMS`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_ENABLED`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_TTL_HOURS`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_MIN_SIMILARITY`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_MAX_CANDIDATES`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_ENABLED`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_SPARSE_WEIGHT`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_DENSE_WEIGHT`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_CANDIDATE_MULTIPLIER`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_MIN_DENSE_SCORE`
- `REFINER_ASSISTANT_RETRIEVAL_COVERAGE_ENABLED`
- `REFINER_ASSISTANT_RETRIEVAL_MIN_QUERY_TERM_COVERAGE`
- `REFINER_ASSISTANT_RETRIEVAL_MIN_MATCH_COUNT`
- `REFINER_ASSISTANT_RETRIEVAL_MIN_CONTEXT_CHARS`
- `REFINER_ASSISTANT_RETRIEVAL_RETRY_ENABLED`
- `REFINER_ASSISTANT_RETRIEVAL_MAX_RETRY_QUERIES`
- `REFINER_ASSISTANT_RETRIEVAL_MIN_CLAUSE_TERMS`
- `REFINER_ASSISTANT_RETRIEVAL_RERANK_ENABLED`
- `REFINER_ASSISTANT_RETRIEVAL_RERANK_MAX_PHRASE_TERMS`
- `REFINER_ASSISTANT_RETRIEVAL_REFUSE_ON_INSUFFICIENT`

## Recommended Next Engineering Slice

1. Use the new core job queue owner-distribution telemetry to decide whether the core FIFO queue also needs owner-aware admission under real production contention.
2. Consider broader multi-pass planning only after citation binding, security rollout telemetry, and evaluation coverage are stable.
3. Finalise deployment sizing for PVC capacity and Postgres pools once rollout telemetry stabilises.

## Objective Status Snapshot

Achieved in code:

- assistant/RAG logic extracted from the monolith into `assistant_pipeline/*`, `assistant_api/*`, `refiner_routes/*`, and `central_store/*`
- input and output security guards modularised behind feature flags
- multi-turn standalone-query rewriting for retrieval-backed routes
- conservative Postgres-backed semantic cache
- deterministic intent routing and prompt-profile selection
- hybrid sparse+dense retrieval with persisted dense sidecars on shared storage
- deterministic coverage grading, retry/decomposition planning, and reranking
- exact claim-to-chunk citation binding with locator-preserving citation payloads
- unsafe tool-use refusal checks with rollout telemetry
- golden rewrite/citation/refusal evaluation fixtures under `tests/evals/assistant_rag/`
- owner-aware assistant/STT capacity limiting and fair subtask/job-action scheduling for multi-user Refiner operation
- owner-distribution telemetry for the core FIFO job queue across autoscaler, health, admin, and worker-capacity telemetry surfaces
- staged RAG publication semantics across immutable artefacts, Postgres metadata, and the legacy active mirror
- rollout checks covering both file-backed and Postgres-backed assistant memory paths
- first-class concurrency tuning variables for assistant/STT/job-action/subtask execution in the Refiner Ansible role defaults

In progress or still pending:

- telemetry-driven decision on whether the core FIFO job queue also needs owner-aware admission
- deployment sizing decisions for PVC capacity and Postgres pools
- broader multi-pass planning after citation and evaluation work stabilise
