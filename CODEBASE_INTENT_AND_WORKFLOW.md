# Refiner Codebase Intent And Workflow Analysis

## 1) Project intent and goals

Refiner is a multi-workflow engineering platform that combines:

- Jira and Confluence analytics/reporting.
- LLM-assisted research and solution planning.
- A web/API control plane for asynchronous jobs and assistant features.
- Safety-first integrations for RAG (read-only context) and MCP (action-oriented tools).

The codebase is built to support two primary operating modes:

- CLI-first execution for report generation, research, and project solving.
- Web/API-first execution for multi-user job orchestration, assistant endpoints, voice/STT, and operational controls.

## 2) Top-level architecture

### Entry points

- `run_refiner.py`: unified CLI workflow router.
- `cli.py`: package entry wrapper that delegates to `run_refiner.run`.
- `main.py`: default Jira statistics pipeline and shared config/credential helpers.
- `refiner_web.py`: primary Flask app serving UI + JSON APIs.
- `frontend_server.py`: optional frontend-only server for static/template hosting.

### Core workflow engines

- `topic_researcher.py`: iterative research loop with LLM + Jira/Confluence/Web context.
- `project_solver.py`: requirement extraction, planning, optional code-edit execution.
- `delivery_pipeline.py`: multi-stage delivery orchestration with gating.
- `confluence_analysis.py`: Confluence hierarchy/content analysis.
- `jira_analysis.py`: Jira issue-quality analysis and reporting.
- `agentic_workflow.py`: reusable plan -> act -> verify -> reflect orchestration primitives.

### Integrations and retrieval

- `rag_engine.py`: local chunking + BM25-like retrieval index/storage.
- `mcp_client.py`: JSON-RPC MCP client and server registry storage.
- `web_research.py`: search engine abstraction, Google search integration, fetch/content extraction.
- `llm_providers.py`: provider abstraction and fallback handling.
- `refiner_ai_orchestration.py`: concurrent provider routing, scoring, and metrics persistence.
- `refiner_ai_specialists.py`: specialist-engine registry and concurrent SNN/AER analysis orchestration.
- `refiner_ai_aarnn.py`: AARNN/SNN engine adapter with HTTP, UDS AER, offline heuristic modes, and generic `snn_aer` specialisation support.
- `refiner_ai_aer.py`: Python `AER1` encoder/decoder compatible with `aarnn_rust`.

### Security and platform controls

- `security_utils.py`: redaction, URL policy checks, audit event helpers.
- `refiner_routes/*.py`: modular route registration for voice, assistant, admin, auth, jobs.
- `capabilities.py`: runtime capability inventory + skills catalog and selector.

## 3) Workflow selection and control flow

The CLI routing logic in `run_refiner.py` selects workflows in this order:

1. `--topic-research` -> topic research workflow.
2. `--delivery` (or delivery flags) -> delivery pipeline workflow.
3. `--project` -> project solver workflow.
4. `--analyze-confluence` -> Confluence analysis workflow.
5. `--analyze-jira` -> Jira quality workflow.
6. Default (unless Jira disabled) -> Jira statistics workflow in `main.py`.

Each execution path emits structured lifecycle events through `EventEmitter` when enabled (`--emit-events`), including stage updates and completion status.

## 4) Detailed workflow behavior

### A) Default Jira statistics workflow (`main.py`)

Primary sequence:

1. Load externalized config (`config.json`) with safe defaults.
2. Resolve one or more Jira instances.
3. Authenticate and connect to Jira.
4. Optionally run discovery (`discover_hierarchy`) to refine query scope.
5. Apply retrieval with layered fallbacks and optional cache support.
6. Transform/sort issues and compute throughput/timeline metrics.
7. Write report artifacts (CSV, HTML, KPI/Gantt outputs).

Resilience mechanisms:

- Query sanitization and order-by fallback.
- Cache compaction and bounded cache age.
- Multi-step fallback JQL strategies when low/no result sets are returned.

### B) Jira/Confluence quality analysis workflows

`run_refiner.py` resolves LLM provider config (including named provider aliases in `config.json`), loads credentials, and dispatches to:

- `jira_analysis.analyze_jira_and_write_report`.
- `confluence_analysis.analyze_space_and_write_report`.

Both support dry-run/report-only operation and optional posting behaviors.

### C) Topic research workflow (`topic_researcher.py`)

Primary sequence:

1. Ingest source topic file or URL.
2. Generate iterative query plan (Jira/CQL/web search).
3. Gather cross-source evidence.
4. Fan out planner/researcher/reviewer roles through the shared AI orchestrator.
5. Draft and refine with agentic phases.
6. Verify completeness and write output document (+ optional references file).

Cross-cutting controls:

- Provider fallback on quota/execution errors.
- Research cache TTL controls.
- Optional Jira/Confluence disabling while keeping workflow active.

### D) Project solver workflow (`project_solver.py`)

Primary sequence:

1. Discover/extract requirements.
2. Build structured plans with concurrent planner candidates and role-specific routing.
3. Optionally enrich planning with reviewer/researcher/multi-engine SNN-AER specialist context, including auto-attached AARNN fallback when no explicit AARNN engine is registered.
4. Optionally apply generated file edits.
5. Optionally execute constrained commands.
6. Verify outcomes and produce completion metadata.

The output includes iteration status, applied steps, and requirement-traceability fields for downstream delivery gating.

### E) Delivery pipeline workflow (`delivery_pipeline.py`)

Primary sequence:

1. Load pipeline configuration (project-local or bundled default).
2. Execute stage graph (sandbox -> dev -> integration/staging/uat/deploy patterns).
3. Enforce approval and gating rules.
4. Persist stage reports and final status.

Optional integration with project-solver fallback can be controlled from CLI flags.
When solver fallback is used, delivery reports also preserve the solver `ai_orchestration` metadata so model/engine decisions remain traceable through release stages.

## 5) Web/API runtime workflow (`refiner_web.py`)

The web server acts as a control plane with these major capability groups:

- Authentication/session:
  - Local login/setup/session endpoints.
  - OIDC exchange and SSO token issuance/consumption.
  - Login throttling and audit trail integration.
- Job system:
  - Queue-based background job execution and status APIs.
  - Workspace/session collaboration helpers.
  - Job action task queue for side-actions.
- Assistant endpoints:
  - Requirements drafting/form-fill and planning helpers.
  - RAG + MCP assistant fusion endpoint.
- Voice/STT:
  - Voice token and provider-specific endpoints (Siri/Alexa/Google).
  - Command/server STT backends with retry/capacity controls.
  - Optional gesture planning integration.
- Governance and operations:
  - Token ledger/balance APIs.
  - Refund and admin operations.
  - Metrics and health endpoints.
  - AI orchestration status visibility on health/admin surfaces.

## 6) Data and state model

Primary runtime data root: `job_data/` (configurable via env). Key stores include:

- Job metadata/logs/events.
- User records and secret storage.
- Access/project/team structures.
- Session history and TODO data.
- Token ledgers and audit logs.
- RAG index files (scoped per user/owner).
- MCP server registry entries.

Most mutable stores are JSON/JSONL files with explicit locking or atomic-write patterns where needed.

## 7) Safety, security, and robustness mechanisms

Implemented across modules:

- Secrets/log redaction filters (`security_utils.RedactionFilter`).
- File and directory permission enforcement for sensitive stores.
- URL allowlist/blocklist/private-network checks for outbound fetches.
- One-time SSO token stores (memory or Redis-backed).
- Auth attempt throttling and audit logging.
- Bounded queue/semaphore controls for STT/assistant throughput.
- Retry and backoff wrappers for external HTTP dependencies.
- Fallback behavior when optional dependencies or external systems are unavailable.
- Provider orchestration telemetry with rolling health/latency/quality signals.
- Neuromorphic/AARNN routing fallback to offline heuristic AER translation when live runtime is unavailable, while still allowing multiple specialist SNN/AER engines to contribute concurrently.

## 8) Testing posture and verification strategy

Existing repository includes broad tests under `tests/` for:

- Jira/Confluence querying, pagination, fallback behaviors.
- Topic research robustness and cache behavior.
- Route registration and STT server resilience.
- Output generation and transformation utilities.

Current full-suite baseline (captured during this documentation pass):

- `161 passed`, `11 skipped`, `30 failed`.

Interpretation:

- The suite is broad and valuable, but there are known regressions already present.
- New tests added in this pass focus on core safety/resilience modules and are intended to be run independently while upstream regressions are triaged.

## 9) Recommended maintenance workflow

1. Keep `run_refiner.py` as the single workflow routing authority.
2. Treat `main.py` as the canonical Jira statistics pipeline; avoid duplicating fetch/fallback logic elsewhere.
3. Keep route modules (`refiner_routes/*`) thin and business logic in shared modules for testability.
4. Keep provider selection logic in `refiner_ai_orchestration.py`; do not reintroduce per-module fallback wrappers.
5. Expand tests around security boundaries, provider scoring, and neuromorphic routing whenever adding new endpoints or integrations.
6. Preserve additive auditability (events/logs) for all state-changing API operations.
