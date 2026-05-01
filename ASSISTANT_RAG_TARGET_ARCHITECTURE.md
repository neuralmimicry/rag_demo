# Assistant / RAG Target Architecture

## Scope

This document defines the target architecture for the assistant and RAG slice inside Refiner.
It is intentionally not a new public microservice.
The target is a modular monolith inside Refiner with background workers for ingestion and indexing.

Implementation work is tracked in
[ASSISTANT_RAG_MIGRATION_BACKLOG.md](/home/pbisaacs/Developer/neuralmimicry/rag_demo/ASSISTANT_RAG_MIGRATION_BACKLOG.md).

This keeps the current public contract stable:

- browser and device clients continue to use `https://api.neuralmimicry.ai`
- Refiner remains the single gateway and compatibility layer
- Customers remains the source of truth for identity and session state
- Billing remains the source of truth for token mutation orchestration
- nmchain remains the audit ledger of record for financial and privileged events
- nmstt remains the STT and gesture-planning service
- Gail remains an optional model/orchestration bridge

## Why This Shape

The existing system already has the right deployment envelope:

- Refiner is the public ingress-compatible API surface
- Postgres already exists and is already used for shared control-plane state
- `continuum-shared` storage already exists and is already used for large persistent artefacts
- Refiner already has background workers, subtasks, schedules, and LLM orchestration

The main problem is not missing infrastructure.
The main problem is that assistant and RAG logic still live in a single large module, with file-backed storage and only a minimal retrieval/security pipeline.

The target therefore is:

1. keep the runtime envelope stable
2. move assistant/RAG logic behind explicit package boundaries
3. move shared metadata/state into Postgres
4. keep large retrieval artefacts on existing shared storage
5. add the missing assistant pipeline stages incrementally behind feature flags

## Current-State Baseline

The current code already provides useful building blocks that should be preserved:

- extracted assistant admin/debug HTTP handlers in `assistant_api/admin_handlers.py`
- extracted assistant/RAG HTTP handlers in `assistant_api/*`
- extracted assistant/RAG application logic in `assistant_pipeline/service.py`
- extracted input/output security policy helpers in `assistant_pipeline/security/*`
- extracted RAG ingestion helpers in `assistant_pipeline/ingestion/source_loader.py`
- versioned RAG artefact helpers in `assistant_pipeline/ingestion/artifact_store.py`
- extracted assistant episodic memory helpers in `assistant_pipeline/memory/episodic_store.py`
- extracted assistant conversation persistence helpers in `assistant_pipeline/memory/conversation_store.py`
- deterministic conversation-aware retrieval query rewriting in `assistant_pipeline/memory/query_rewriter.py`
- extracted intent-routing helpers in `assistant_pipeline/routing/intent_router.py`
- extracted route-specific prompt profiles in `assistant_pipeline/routing/prompt_profiles.py`
- extracted conservative semantic-cache helpers in `assistant_pipeline/cache/semantic_cache.py`
- extracted sparse/dense hybrid retrieval helpers in `assistant_pipeline/retrieval/*`
- extracted assistant trace recorder in `assistant_pipeline/tracing/recorder.py`
- explicit assistant-side Jira/Confluence write execution in `refiner/integrations/atlassian/actions.py`
- layout-aware extraction in `refiner/file_converter.py`
- structure-preserving document blocks and locators in `refiner/document_schema.py`
- lexical BM25-like retrieval in `refiner/rag_engine.py`
- per-user episodic JSONL memory in `refiner/solver_memory.py`
- provider orchestration and Gail bridging in `refiner/refiner_ai_orchestration.py` and `refiner/refiner_ai_gail.py`
- Postgres-backed shared state in `refiner/refiner_central_store.py`
- Postgres-backed assistant/RAG metadata stores in `central_store/*`
- Postgres-backed MCP registry via `refiner/mcp_client.py`
- thin route registrars already separated under `refiner_routes/`

The current gaps are:

- `refiner/refiner_web.py` still owns configuration wiring and compatibility shims for the extracted assistant/RAG modules
- RAG storage is still file-backed, but the read path now has an optional hybrid sparse+dense retrieval layer behind feature flags
- queued/versioned RAG builds now stage immutable artefacts in Postgres before the legacy active mirror is finalised, but asynchronous mode is still opt-in behind `REFINER_RAG_ASYNC_INDEX_BUILDS` or request-level `"async": true`
- the security envelope is now modularised, but stricter message-role blocking, prompt-leak blocking, remote-URL validation, and output PII redaction still roll out behind feature flags
- intent-specific prompt routing now exists, but it is deterministic and policy-driven rather than model-classified
- a conservative Postgres-backed semantic cache now exists for safe read-only routes, but it is token/string-similarity-based rather than embedding-based
- hybrid retrieval now exists behind feature flags using lexical search plus a deterministic hashed dense backend with persisted sidecar artefacts on shared storage; deterministic reranking now sits on top of those candidates behind its own rollout flags
- deterministic retrieval coverage grading, richer decomposition, one retry/decomposition pass, deterministic reranking, and citation-level claim binding now exist behind feature flags for RAG-backed routes; broader multi-pass planning is still pending
- Refiner request chokepoints now use owner-aware assistant/STT capacity limiting plus fair subtask and job-action admission so one active user cannot monopolise shared execution slots
- the core FIFO job queue now emits owner-distribution telemetry through autoscaler, health, admin, and worker-capacity views so any future owner-aware admission decision can be evidence-led rather than speculative
- basic per-stage trace recording now exists in a dedicated recorder module, and admin read-side trace/conversation APIs now exist, but retention and richer analytics are still pending
- assistant episodic memory now dual-writes to Postgres, route-level rollout checks cover both JSONL-backed and Postgres-backed read/write paths, and conversation persistence helpers are modularised, but broader assistant state is still partly file-backed or request-local

Current rollout flags for the security slice:

- `REFINER_ASSISTANT_SECURITY_POLICY_ENABLED`
- `REFINER_ASSISTANT_SECURITY_STRICT_MESSAGE_ROLES`
- `REFINER_ASSISTANT_SECURITY_BLOCK_PROMPT_LEAK`
- `REFINER_ASSISTANT_MCP_ADMIN_ONLY`
- `REFINER_ASSISTANT_BLOCK_UNSAFE_TOOL_REQUESTS`
- `REFINER_ASSISTANT_SECURITY_VALIDATE_RAG_SOURCE_URLS`
- `REFINER_ASSISTANT_OUTPUT_REDACT_PII`

Current runtime fairness flags for multi-user Refiner operation:

- `REFINER_ASSISTANT_MAX_CONCURRENT`
- `REFINER_ASSISTANT_MAX_CONCURRENT_PER_USER`
- `REFINER_STT_MAX_CONCURRENT`
- `REFINER_STT_MAX_CONCURRENT_PER_USER`
- `REFINER_JOB_ACTION_MAX_OUTSTANDING_PER_OWNER`
- `REFINER_JOB_ACTION_MAX_INFLIGHT_PER_OWNER`
- `REFINER_SUBTASK_MAX_OUTSTANDING_PER_OWNER`
- `REFINER_SUBTASK_MAX_INFLIGHT_PER_OWNER`
- `REFINER_ASSISTANT_OUTPUT_VALIDATE_SHAPES`

Queue-owner skew for the core FIFO job queue is now observable, but it remains a telemetry-only signal.
Admission for the core queue should stay FIFO unless production evidence shows sustained cross-user contention there as well.

Current rollout flags for routing and cache:

- `REFINER_ASSISTANT_INTENT_ROUTING_ENABLED`
- `REFINER_ASSISTANT_ROUTING_SKILL_HINT_LIMIT`
- `REFINER_ASSISTANT_ROUTING_CAPABILITY_MAX_ITEMS`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_ENABLED`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_TTL_HOURS`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_MIN_SIMILARITY`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_MAX_CANDIDATES`

Current rollout flags for hybrid retrieval:

- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_ENABLED`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_SPARSE_WEIGHT`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_DENSE_WEIGHT`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_CANDIDATE_MULTIPLIER`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_MIN_DENSE_SCORE`

Current rollout flags for retrieval grading and retry:

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

## Target Runtime Architecture

```text
Browser / Voice Client
        |
        v
api.neuralmimicry.ai
        |
        v
Refiner HTTP routes
  - /api/assistant/*
  - /api/rag/*
        |
        v
assistant_api handlers
        |
        v
assistant_pipeline.service
  |-- input_guard
  |-- conversation_store
  |-- intent_router
  |-- query_rewriter
  |-- semantic_cache
  |-- retrieval_coordinator
  |     |-- collection resolver
  |     |-- sparse retriever
  |     |-- dense retriever
  |     |-- fusion + reranker
  |     |-- coverage grader
  |     `-- citation enricher
  |-- mcp_bridge
  |-- atlassian_action_bridge
  |-- prompt_builder
  |-- generation_gateway
  |     `-- existing refiner_ai_orchestration / Gail bridge
  |-- output_guard
  `-- trace_recorder
        |
        +--> Postgres
        |     - conversations
        |     - turns
        |     - traces
        |     - cache metadata
        |     - RAG metadata
        |
        +--> continuum-shared storage under job_data
        |     - extraction artefacts
        |     - chunk payloads
        |     - sparse index artefacts
        |     - dense vector artefacts
        |     - raw trace/event streams
        |
        +--> Customers
        +--> Billing
        +--> nmchain
        +--> nmstt
        `--> MCP servers / Gail / model providers
```

## Request Pipeline

Not every route uses every stage, but all assistant/RAG routes should pass through the same application service boundary.

### Common read path

1. HTTP handler validates auth and request size.
2. `input_guard` canonicalizes the payload and rejects unsafe or malformed input before any model call.
3. `conversation_store` loads recent turns when a conversation id is present.
4. `intent_router` classifies the request into a concrete route strategy.
5. `query_rewriter` turns follow-up queries into standalone retrieval queries when needed.
6. `semantic_cache` checks whether a safe, scope-compatible answer already exists.
7. `retrieval_coordinator` runs if the intent requires retrieval.
8. `prompt_builder` assembles the route-specific prompt.
9. `generation_gateway` calls the existing Refiner provider/orchestration layer.
10. `citation_enricher` binds claims to the retrieved evidence set.
11. `output_guard` validates/refuses/redacts the final response.
12. `trace_recorder` writes the request trace and metrics.
13. `conversation_store` and `episodic_store` persist post-turn state.

### Retrieval path

The target retrieval loop is:

1. resolve collection(s) and access scope
2. run sparse retrieval over the structured chunks
3. run dense retrieval over the same collection version
4. fuse results using weighted reciprocal rank fusion
5. rerank the fused candidate set
6. grade evidence coverage
7. if coverage is insufficient, decompose into sub-queries and retry once
8. if still insufficient, refuse honestly instead of hallucinating

### Tool-augmented path

For `assistant_rag_mcp` and future tool-enabled intents:

- input guard decides whether the request is eligible for tool use
- tool execution remains explicit and policy-gated
- semantic cache is disabled for live MCP calls and live Atlassian write actions
- MCP remains separate from retrieval and is merged only at prompt-building time
- Jira/Confluence write actions run through the same guard/trace boundary, but use the built-in Atlassian executor rather than the MCP registry

### Structured-output path

For `assistant_form_fill`, `playground_plan`, and `execution_plan`:

- use intent-specific prompt profiles
- keep schema validation strict
- keep output guard schema-aware
- only cache when the request is read-only and the result is safe to reuse

## Capability Map

| Capability | Target module | Notes |
| --- | --- | --- |
| Input security | `assistant_pipeline/security/input_guard.py` | Compatibility-preserving request normalisation now exists; stricter role allow-listing, prompt-leak blocking, and remote source URL checks are feature-flagged for rollout |
| Conversation memory | `assistant_pipeline/memory/conversation_store.py` and `assistant_pipeline/memory/query_rewriter.py` | Short-term turns plus standalone-query rewriting |
| Semantic cache | `assistant_pipeline/cache/semantic_cache.py` | Conservative Postgres-backed cache is now implemented for `rag_query` and RAG-only `assistant_rag_mcp`; embedding-backed matching can replace the current token/string scorer later without changing the service boundary |
| Intent routing | `assistant_pipeline/routing/intent_router.py` | Deterministic route strategies and prompt profiles now exist for requirements, marketing, form fill, playground, RAG, and RAG+MCP flows |
| Hybrid retrieval | `assistant_pipeline/retrieval/hybrid_retriever.py` | Sparse + dense + fusion now exist behind rollout flags; the dense side now persists hashed feature sidecars alongside index artefacts so a richer embedding backend can replace it later without changing the service boundary |
| Self-correcting retrieval | `assistant_pipeline/retrieval/coverage_grader.py`, `assistant_pipeline/retrieval/retrieval_planner.py`, and `assistant_pipeline/retrieval/reranker.py` | Deterministic coverage grading, richer decomposition, reranking, one retry pass, evidence-based refusal, and citation-aware generation now exist behind rollout flags; broader multi-pass planning is still pending |
| Citation enrichment | `assistant_pipeline/retrieval/citation_enricher.py` | Claim-to-chunk binding with page/block locator preservation |
| Output security | `assistant_pipeline/security/output_guard.py` | Response-shape validation is live; optional PII redaction is available behind feature flags before wider rollout |
| Observability | `assistant_pipeline/tracing/recorder.py` | Per-stage spans, latency, cache, retrieval, provider, refusal signals |
| Evaluation | `tests/evals/assistant_rag/` | Golden datasets, retrieval metrics, refusal tests, citation checks |
| Offline data pipeline | `assistant_pipeline/ingestion/*` | Extraction, chunking, metadata enrichment, sparse/dense artefact builds |

## Module Boundaries

The target package layout is:

```text
assistant_api/
  __init__.py
  admin_handlers.py
  assistant_handlers.py
  rag_handlers.py
  schemas.py

assistant_pipeline/
  __init__.py
  contracts.py
  dependencies.py
  service.py

  security/
    input_guard.py
    output_guard.py
    policies.py

  routing/
    intent_router.py
    prompt_profiles.py

  memory/
    conversation_store.py
    episodic_store.py
    query_rewriter.py

  cache/
    semantic_cache.py

  retrieval/
    collection_service.py
    source_loader.py
    sparse_retriever.py
    dense_retriever.py
    hybrid_retriever.py
    reranker.py
    coverage_grader.py
    retrieval_planner.py
    citation_enricher.py

  ingestion/
    source_loader.py
    artifact_store.py
    publication.py
    extractor.py
    chunker.py
    metadata.py
    index_builder.py

  tracing/
    recorder.py
    models.py

  repositories/
    postgres.py
    filesystem.py

  integrations/
    llm_gateway.py
    mcp_bridge.py
    customers.py
    billing.py
    nmchain.py
    nmstt.py
    gail.py

central_store/
  __init__.py
  base.py
  users.py
  tokens.py
  telemetry.py
  jobs.py
  mcp.py
  assistant.py
  rag.py
```

Boundary rules:

- `assistant_api/*` is HTTP-only and must not import Flask globals deep into the pipeline.
- `assistant_pipeline/service.py` owns orchestration and must not know about Flask request objects.
- `assistant_pipeline/repositories/*` owns persistence and must not contain prompt logic.
- `assistant_pipeline/integrations/*` owns external services and must not know HTTP route details.
- `assistant_pipeline/retrieval/*` owns retrieval behavior and must not talk directly to Billing or Customers.
- `central_store/*` owns Postgres stores and schema evolution.
- `refiner_routes/admin.py` owns assistant admin/debug route registration rather than leaving those routes inline in `refiner/refiner_web.py`.

## Route Strategy Matrix

| Route / intent | Rewrite | Retrieval | MCP | Output shape | Cache eligible |
| --- | --- | --- | --- | --- | --- |
| `assistant_requirements` ask | optional | episodic only by default | no | text | yes |
| `assistant_requirements` draft | optional | episodic only by default | no | markdown | limited |
| `assistant_requirements` marketing | optional | knowledge-source lookup | no | text | yes |
| `assistant_form_fill` | optional | episodic hints only | no | strict JSON | limited |
| `playground_plan` | usually no | episodic hints only | no | strict JSON | yes |
| `execution_plan` | usually no | episodic hints only | no | strict JSON | yes |
| `rag_query` | yes | hybrid required | no | structured retrieval payload | yes |
| `assistant_rag_mcp` without MCP | yes | hybrid when requested | no | text + citations | yes |
| `assistant_rag_mcp` with MCP | yes | optional | yes | text + citations when retrieval used | no |

Current implementation note:

- query rewriting is active on `rag_query` and `assistant_rag_mcp`
- semantic cache lookup/store is active only on `rag_query` and `assistant_rag_mcp` without MCP, and only when the rollout flag is enabled
- hybrid retrieval is active only on `rag_query` and `assistant_rag_mcp` when RAG is used and the rollout flag is enabled
- retrieval coverage grading and one retry/decomposition pass are active only on `rag_query` and RAG-enabled `assistant_rag_mcp` when the rollout flags are enabled
- `assistant_requirements`, `assistant_form_fill`, `playground_plan`, and `execution_plan` now use routed prompt profiles but do not yet write to the semantic cache

## Storage Model

### Postgres owns shared control-plane state

Add the following tables to the central store domain.

| Table | Purpose |
| --- | --- |
| `nm_assistant_conversations` | Conversation header, owner, route family, scope, latest turn pointer |
| `nm_assistant_turns` | User/assistant turns, normalized request metadata, rewritten query, provider/model summary |
| `nm_assistant_episodes` | Durable episodic memories replacing JSONL-only state |
| `nm_assistant_traces` | Query-level trace header with status, intent, cache outcome, provider outcome |
| `nm_assistant_trace_spans` | Per-stage spans for guard, rewrite, retrieve, rerank, generate, output-guard |
| `nm_assistant_semantic_cache` | Semantic cache metadata, scope key, collection fingerprint, expiry, payload refs |
| `nm_rag_collections` | Named user/team collections, owner scope, active version, status |
| `nm_rag_collection_versions` | Immutable build versions, artefact manifest, source fingerprint, chunk counts |
| `nm_rag_documents` | Source document metadata, checksum, extraction refs |
| `nm_rag_chunks` | Chunk metadata, citation label, locator metadata, text preview, artefact ref |
| `nm_rag_query_audits` | Query, rewritten query, selected evidence set, answer payload ref, refusal/citation metadata |

Postgres stores metadata, references, and compact previews.
It does not store the heavy dense/sparse artefacts as the primary payload.

### Shared storage owns heavy artefacts

Use the existing shared storage mounted under `job_data`.

Target layout:

```text
job_data/
  rag/
    collections/<scope>/<collection_id>/<version>/
      manifest.json
      sources.jsonl
      documents/<document_id>/extraction.json
      chunks/chunks.jsonl
      sparse/bm25.json
      dense/embeddings.npy
      dense/manifest.json
      rerank/features.jsonl
  assistant_memory/
    legacy/<owner_hash>.jsonl
  ai/
    assistant_traces/<yyyy>/<mm>/<dd>/<trace_id>.jsonl
    semantic_cache/<scope>/<partition>.json
    evals/<run_id>/results.jsonl
```

Rules:

- collection builds are versioned and immutable once published
- Postgres points to one active version per collection
- cache entries include the active collection fingerprint so collection updates invalidate old cache hits
- full raw traces are optional and sampled; trace headers stay in Postgres
- legacy JSONL memory remains during migration only
- the current implementation still publishes the legacy flat active index file alongside the immutable version artefact so retrieval remains backwards compatible; publication is now staged explicitly in Postgres before the active mirror is finalised, and assistant reads prefer the immutable active-version artefact recorded there when it is available

### Dense retrieval backend choice

Do not introduce a new vector database service for the first implementation.

Use an interface-driven dense backend:

- current compatibility backend: deterministic hashed token/character projection persisted as a dense sidecar on shared storage and loaded in-process for cosine search
- next backend: persisted embedding matrix on shared storage behind the same retrieval interface
- optional later backend: HNSW or pgvector without changing the application-service interface

This keeps the first delivery aligned with existing infrastructure and avoids premature operational sprawl.

## Offline Ingestion Pipeline

The ingestion path should be asynchronous and versioned.

### Build flow

1. create or refresh collection request arrives at `/api/rag/index`
2. HTTP handler creates a background subtask such as `rag_collection_build`
3. source loader validates paths, URLs, and ownership scope
4. extractor uses the existing `FileConverter.extract()` output
5. chunker preserves structure, heading path, page, block, and locator metadata
6. metadata enricher computes source fingerprints, previews, and chunk manifests
7. sparse index builder builds the lexical artefact
8. dense index builder computes the current hashed dense projections and writes the dense sidecar artefact
9. collection version artefact is written to storage
10. Postgres stages the new version as `publishing` with the immutable artefact path
11. the legacy flat active file is mirrored for compatibility with older consumers
12. Postgres finalises the version as active after the mirror succeeds

### Why use subtasks first

Refiner already has a `SubtaskManager` for generic background work.
That is the lowest-risk first implementation for collection builds, cache warming, and repair jobs.
If index build times later require richer logs or scheduling semantics, the same service boundary can be promoted to the existing `JobManager` without changing the HTTP contract.

## Security Architecture

### Input guard

The target input guard is layered, not a single regex pass.

It should do the following before any model call:

- request schema validation and canonicalization
- message-role allow-listing so user payloads cannot inject `system` or `developer` roles
- prompt injection and system-prompt extraction heuristics
- explicit prompt-leak policy for routes that expose assistant output
- path allow-listing for local document sources
- URL safety checks for remote sources, including private-network and SSRF blocking
- MCP action gating by route, user role, and server/tool allow-list
- request-size and token-budget pre-checks

### Output guard

The output guard runs after generation and has a different threat model.

It should do the following:

- structured output validation for JSON routes
- refusal normalization for unsafe requests
- PII redaction for configured data classes
- citation coverage checks for retrieval-backed claims
- response-shape sanitization before STT gesture planning or downstream display

## Cross-Product Interaction Model

### Customers

Customers remains the identity and session authority.
The assistant/RAG slice uses it for:

- session resolution
- team membership and access-tree lookups
- scope resolution for personal vs team collections

No assistant state should be stored in Customers.
Assistant state belongs in Refiner-owned Postgres tables keyed by the authenticated subject.

### Billing

Billing remains the internal token mutation interface.
The assistant/RAG slice should call Billing for:

- generation token reservations and debits
- embedding/rerank charges if those become billable
- zero-debit or reduced-cost metadata for semantic cache hits

Billing does not need a new public API surface for this.
The existing internal token-event pattern is sufficient if stage metadata is included in `meta`.

### nmchain

nmchain should continue to hold financial and privileged audit records, not every assistant trace.
The assistant/RAG slice should emit nmchain events only for high-value actions such as:

- token mutations via Billing
- privileged MCP executions
- collection create/delete operations when operator auditability matters

Full assistant traces belong in Postgres and shared storage, not on-chain.

### nmstt

nmstt remains unchanged as the STT and gesture service.
The assistant pipeline should:

- treat STT transcript text as a normal conversation turn input
- run output guard before gesture planning
- preserve current nmstt integration points and timeout controls

### Gail

Gail remains optional and should stay behind the existing orchestration helper.
The new pipeline should not call providers directly from route handlers.
It should call an LLM gateway adapter that can use:

- local Refiner provider/orchestration logic, or
- the existing Gail bridge when enabled

If Gail later gains embedding or reranking endpoints, the new pipeline should be able to swap to them behind the same adapter boundary.

## Monolith Modularisation Plan

### First routing cleanup

The current route registrars are a good start, but assistant/RAG routes are still split awkwardly.
Make the first routing cleanup:

- keep `refiner_routes/assistant.py` for assistant-only route registration
- add `refiner_routes/rag.py` for `/api/rag/*`
- optionally add `refiner_routes/mcp.py` for `/api/mcp/*`
- remove RAG and MCP route registration from `refiner_routes/jobs.py`

### Business-logic extraction map

| Current area | Extract to | Notes |
| --- | --- | --- |
| `/api/admin/assistant/conversations*`, `/api/admin/assistant/traces*` | `assistant_api/admin_handlers.py` | Admin-only HTTP wrappers over central-store read helpers |
| `rag_indexes`, `rag_index_create`, `rag_index_delete`, `rag_query` | `assistant_api/rag_handlers.py` | HTTP wrappers only |
| `assistant_rag_mcp`, `assistant_requirements`, `assistant_form_fill`, `playground_plan`, `execution_plan` | `assistant_api/assistant_handlers.py` | Thin HTTP wrappers calling `assistant_pipeline.service` |
| `_coerce_rag_sources`, `_build_rag_documents` | `assistant_pipeline/ingestion/source_loader.py` | Shared source normalization and loading |
| versioned collection artefact paths and writes | `assistant_pipeline/ingestion/artifact_store.py` | Immutable version payloads under `job_data/rag/collections/...` |
| staged/final collection publication orchestration | `assistant_pipeline/ingestion/publication.py` | Coordinates immutable writes, legacy mirroring, and Postgres publication states |
| `_render_rag_context`, `_serialize_rag_match`, citation helpers | `assistant_pipeline/retrieval/citation_enricher.py` | Shared retrieval presentation helpers |
| `_assistant_memory_*` helpers | `assistant_pipeline/memory/episodic_store.py` | Start as adapter over `refiner/solver_memory.py`, then migrate to Postgres |
| `_guardrail_scan` | `assistant_pipeline/security/input_guard.py` | Replaced by richer request policy engine |
| `_assistant_reply_payload` | `assistant_pipeline/security/output_guard.py` and `assistant_pipeline/service.py` | Split response validation from presentation |
| background build actions | `assistant_pipeline/ingestion/index_builder.py` | New `rag_collection_build` subtask action |

### Central-store modularisation

Do not extend `refiner/refiner_central_store.py` as one larger file.
Split it into the `central_store/` package and keep `refiner/refiner_central_store.py` as a compatibility import shim until the migration is complete.

Add new store classes there rather than in `refiner/refiner_web.py`.

## Implementation Sequence

### Phase 0: freeze current contracts and add evaluation fixtures

Deliverables:

- route-level regression tests for current assistant and RAG endpoints
- golden datasets under `tests/evals/assistant_rag/`
- baseline metrics for latency, retrieval quality, citation coverage, refusal correctness

Reason:

Do not refactor the monolith blind.
Lock the observable contract first.

### Phase 1: introduce the assistant package boundary with no behavior change

Deliverables:

- `assistant_api/assistant_handlers.py`
- `assistant_api/rag_handlers.py`
- `assistant_pipeline/contracts.py`
- `assistant_pipeline/dependencies.py`
- `assistant_pipeline/service.py`
- `refiner_routes/rag.py`

Behavior:

- move current route logic out of `refiner/refiner_web.py`
- keep the existing lexical RAG and JSONL memory behavior intact
- keep the HTTP contract unchanged

This phase is a lift-and-shift extraction, not a feature phase.

### Phase 2: move assistant state and traces into Postgres with dual-write

Deliverables:

- `central_store/assistant.py`
- `central_store/rag.py`
- assistant conversation, turn, trace, episodic-memory tables
- dual-write from legacy JSONL memory to Postgres

Behavior:

- read from Postgres when available
- continue writing legacy file state during cutover
- add trace headers and per-stage spans even while retrieval remains lexical

### Phase 3: make RAG collections asynchronous and versioned

Deliverables:

- `assistant_pipeline/ingestion/*`
- `rag_collection_build` and `rag_collection_refresh` background actions
- versioned collection manifests in `job_data/rag/collections/...`
- Postgres collection/version/document/chunk metadata

Behavior:

- `/api/rag/index` can queue builds through the existing subtask manager without changing the route shape
- asynchronous mode is opt-in first via `REFINER_RAG_ASYNC_INDEX_BUILDS` or request-level `"async": true`
- collections become versioned artefacts while continuing to publish the legacy active file for compatibility; publication is staged in Postgres before the active mirror is finalised, and assistant retrieval prefers the immutable active-version path from metadata
- retrieval still uses sparse lexical search first

### Phase 4: add the security and observability envelopes

Deliverables:

- `assistant_pipeline/security/input_guard.py`
- `assistant_pipeline/security/output_guard.py`
- `assistant_pipeline/tracing/*`
- admin/debug views for traces and collection health

Behavior:

- pre-model request policy enforcement
- post-model output validation and redaction
- per-stage trace visibility for every assistant/RAG request
- assistant admin/debug endpoints read conversation and trace data from Postgres rather than from monolith-local state
- preserve the current public API shape while introducing stricter controls behind `REFINER_ASSISTANT_SECURITY_*` and `REFINER_ASSISTANT_OUTPUT_*` flags first

### Phase 5: add conversation state, rewriting, routing, and semantic cache

Deliverables:

- `assistant_pipeline/memory/conversation_store.py`
- `assistant_pipeline/memory/query_rewriter.py`
- `assistant_pipeline/routing/intent_router.py`
- `assistant_pipeline/cache/semantic_cache.py`

Behavior:

- follow-up questions become standalone retrieval queries
- prompt profiles become intent-specific rather than route-only
- safe read-only requests can short-circuit on cache hit
- the current codebase already applies deterministic standalone-query rewriting to `rag_query` and retrieval-enabled `assistant_rag_mcp`

### Phase 6: add hybrid retrieval, reranking, and self-correction

Deliverables:

- `assistant_pipeline/retrieval/dense_retriever.py`
- `assistant_pipeline/retrieval/hybrid_retriever.py`
- `assistant_pipeline/retrieval/reranker.py`
- `assistant_pipeline/retrieval/coverage_grader.py`
- `assistant_pipeline/retrieval/retrieval_planner.py`
- `assistant_pipeline/retrieval/citation_enricher.py`

Behavior:

- sparse plus dense retrieval with fused ranking
- evidence grading, deterministic reranking, and one retry loop with richer decomposition
- refusal when evidence remains insufficient
- claim-to-source binding using exact chunk and locator ids

### Phase 7: remove compatibility shims and harden operations

Deliverables:

- Postgres becomes the default assistant state store
- legacy JSONL memory becomes fallback-only or is removed
- legacy sync RAG code paths are deleted
- alerting, dashboards, retention, and cleanup jobs are finalized

Behavior:

- old helper functions disappear from `refiner/refiner_web.py`
- assistant/RAG logic is fully owned by the new package tree

## Suggested Feature Flags

Use environment flags so the migration can be gradual.
Current and recommended names:

- `REFINER_ASSISTANT_PIPELINE_ENABLED`
- `REFINER_ASSISTANT_PIPELINE_DUAL_WRITE`
- `REFINER_ASSISTANT_TRACE_ENABLED`
- `REFINER_ASSISTANT_QUERY_REWRITE_ENABLED`
- `REFINER_ASSISTANT_SEMANTIC_CACHE_ENABLED`
- `REFINER_RAG_ASYNC_INDEX_BUILDS`
- `REFINER_ASSISTANT_HYBRID_RETRIEVAL_ENABLED`
- `REFINER_ASSISTANT_RETRIEVAL_RERANK_ENABLED`
- `REFINER_ASSISTANT_RETRIEVAL_COVERAGE_ENABLED`
- `REFINER_ASSISTANT_RETRIEVAL_RETRY_ENABLED`

## Continuum / Deployment Changes

No new public tenant workload is required for the target architecture.
This should remain part of the Refiner tenant deployment.

### Reuse existing infrastructure

- keep using the existing Refiner workload and ingress
- keep using the existing Postgres tenant service
- keep using the existing `continuum-shared` PVC for `job_data`
- keep using existing Customers, Billing, nmstt, nmchain, and optional Gail service URLs

### Refiner role changes

Use the existing Refiner Ansible role mechanisms:

- non-secret flags through `continuum_tenant_refiner_extra_env`
- secret values by extending the Refiner role's existing secret-env map and rendered `refiner-secrets` manifest, following the current `REFINER_*_API_TOKEN` pattern

Likely new config values:

- assistant pipeline feature flags
- assistant security rollout flags for stricter message-role blocking, prompt-leak blocking, remote source URL validation, and output PII redaction
- embedding provider settings or Gail embedding endpoint settings
- semantic cache TTL and threshold values
- trace sampling / retention controls
- RAG build worker limits

### Capacity notes

Potential infra tuning that may become necessary:

- increase Refiner PVC size beyond `20Gi` if collection artefacts grow materially
- increase Postgres pool size above the current default if trace volume becomes significant
- if embedding models are local, ensure model assets are either image-baked or stored on shared storage

No ingress or hostname changes are required.

## Evaluation Plan

Create an offline evaluation harness under `tests/evals/assistant_rag/`.

The first golden suites now exist for rewrite/regression checks, citation binding coverage, and unsafe tool-use refusals.

Minimum suites:

- query rewriting correctness
- retrieval recall at K
- citation coverage and exact-source correctness
- refusal correctness for insufficient evidence
- prompt injection and prompt leak resistance
- structured-output validity for JSON routes
- semantic-cache precision and stale-hit rejection

Minimum tracked metrics:

- cache hit ratio
- p50 / p95 latency by stage
- retrieval recall@5 and recall@10
- citation precision
- refusal precision / false-positive refusal rate
- answer groundedness

## Acceptance Criteria

The assistant/RAG slice reaches the target architecture when all of the following are true:

- assistant and RAG route logic no longer lives in `refiner/refiner_web.py`
- assistant shared state is Postgres-backed by default
- collection artefacts are versioned on shared storage
- hybrid retrieval is available behind a stable service interface
- follow-up retrieval queries are rewritten to standalone queries
- read-only cache hits can bypass generation safely
- citations are bound to exact retrieved chunks with locator metadata
- every assistant/RAG request emits stage traces
- existing cross-product interactions remain behind Refiner and continue to use the current service topology

## Recommended First Implementation Cut

The safest first cut is:

1. extract handlers and service boundaries without changing behavior
2. split RAG route registration out of `refiner_routes/jobs.py`
3. add Postgres-backed assistant conversations, traces, and episodic-memory dual-write
4. add asynchronous versioned collection builds

That sequence reduces risk immediately and creates the stable foundation required for the later semantic cache, hybrid retrieval, and self-correcting retrieval work.
