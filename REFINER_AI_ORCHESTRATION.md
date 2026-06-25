# Refiner AI Orchestration

## Intent

Refiner now treats model selection as a workflow concern rather than a one-off provider choice.
The orchestration layer exists to:

- break workflow work into focused planner/researcher/reviewer/assistant subtasks,
- run the most appropriate available models concurrently where that is safe,
- keep rolling health, latency, and quality signals for each candidate, and
- keep one AARNN-capable neuromorphic option available while also allowing multiple specialist SNN/AER engines to run alongside the LLM pool.

## Modules

- `refiner/refiner_ai_orchestration.py`
  Central concurrent-provider wrapper, provider registry loading, scoring, and metrics persistence.
- `refiner/refiner_ai_routing_profiles.py`
  Shared routing-contract loader used to keep Refiner's fallback tags aligned with Gail's runtime contract.
- `refiner/refiner_ai_specialists.py`
  Specialist-engine registry, concurrent specialist analysis, and AARNN fallback attachment logic.
- `refiner/refiner_ai_aarnn.py`
  AARNN/SNN-AER adapter that can use HTTP, Unix datagram AER transport, or deterministic offline heuristics.
- `refiner/refiner_ai_aer.py`
  Python implementation of the `AER1` binary format used by `aarnn_rust`.

## Workflow Breakdown

### Jira Statistics

- Primary path remains deterministic analytics.
- No mandatory LLM step is added.
- The workflow remains compatible with the wider orchestration system because downstream assistant and delivery actions can still reuse the shared registry.

### Jira Analysis

- Issue/page-level analysis remains partitioned into concurrent map steps.
- Each LLM-backed review request now uses concurrent multi-provider selection rather than one provider plus ad hoc fallback.
- The chosen response is selected using JSON/quality heuristics plus rolling latency/success history.

### Confluence Analysis

- Page fetch and reduction stay concurrent.
- Reviewer/summariser calls are now routed through the shared orchestrator.
- Long-context/research-specialised models can be preferred automatically when available.

### Topic Research

- Query planning, synthesis, critique, and editing keep their role separation.
- Role providers are now orchestration-aware, so researcher/reviewer/planner roles can fan out across multiple configured models.
- Search-engine concurrency remains intact and now combines with provider concurrency.

### Project Solver

- Requirement sources remain the unit of execution.
- Planning, research, and audit roles now use the shared orchestrator, so multiple model candidates can race or be scored concurrently per planning step.
- Neuromorphic planning can also attach multiple concurrent specialist engines, so AARNN and non-AARNN SNN/AER systems can both contribute routing/context on the same step.
- Sequential file mutation is preserved; only the thinking stages are parallelised.
- Solver output now records the active orchestration state for general/planner/reviewer/researcher providers.

### Delivery Pipeline

- The pipeline itself stays deterministic.
- Its solver fallback automatically benefits from the project-solver orchestration changes.
- Pipeline reports now surface the project-solver `ai_orchestration` block so delivery gating keeps the model/engine traceability from fallback runs.

### Assistant / Playground / Form Fill / RAG+MCP

- Request-time assistant calls now use the same orchestration helper.
- JSON-heavy routes such as playground planning and form-fill prefer candidates that consistently return valid structured output.
- The orchestration helper still honours the user’s selected/default provider, but it can enrich that choice with additional configured candidates.

## Model Registry And Continuous Improvement

Provider telemetry is persisted under `job_data/ai/provider_metrics.json` by default.

Each candidate tracks:

- rolling success/failure counts,
- EWMA latency,
- EWMA quality score,
- latest health-check status, and
- per-workflow/per-role stats.

Selection uses:

- workflow/role specialisation tags,
- prompt keyword hints,
- health status,
- configured weights,
- preferred-provider bias, and
- historical metrics.

This allows Refiner to keep refining its own provider choices instead of hard-coding a permanent winner.

Operational visibility:

- `GET /api/health` returns a lightweight orchestration summary for control-plane checks.
- `GET /api/admin/stats` returns provider/engine registry details plus condensed metrics history for operators.
- `GET /api/admin/ai-orchestration` returns the dedicated admin drill-down payload used by the admin dashboard panel, with optional engine probing via `probe_engines=1`.
- The admin dashboard panel adds client-side search/filter controls, per-section sorting, and JSON/CSV export of the currently visible orchestration view.

## AARNN / SNN / AER Support

Refiner can register multiple neuromorphic specialist engines concurrently.
Configured `ai_orchestration.engines` entries can include AARNN and other `snn_aer`-style runtimes side by side.
If no explicit AARNN engine is configured, Refiner auto-attaches the legacy/default AARNN path unless it has been explicitly disabled.

Supported modes:

- `http`
  Uses the AARNN FastAPI inference service, typically `http://127.0.0.1:8000`.
- `uds`
  Uses the `aarnn_rust` Unix datagram server and exchanges `AER1` payloads directly.
- `offline_heuristic`
  Used when the repo is present but no runtime is live. Refiner still produces deterministic routing scores and valid AER payloads so planning and documentation can proceed.

Specialist analysis behavior:

- specialist engines are filtered by workflow role before invocation,
- matching engines analyze the task concurrently,
- relevant engines contribute prompt-context blocks back into the orchestrated LLM request, and
- the admin drill-down endpoint surfaces every configured engine plus the auto-attached AARNN fallback entry.

Environment overrides:

- `REFINER_AARNN_ENABLED`
- `REFINER_AARNN_REPO_ROOT`
- `REFINER_AARNN_ENDPOINT`
- `REFINER_AARNN_SOCKET`
- `REFINER_AARNN_SENSORY_SIZE`
- `REFINER_AARNN_OUTPUT_SIZE`
- `REFINER_AARNN_AER_SENSORY_BASE`
- `REFINER_AARNN_AER_OUTPUT_BASE`
- `REFINER_AARNN_TIMEOUT`
- `REFINER_AARNN_ALWAYS_ROUTE`
- `REFINER_NEUROMORPHIC_ALWAYS_ROUTE`
- `REFINER_SPECIALIST_ENGINES_ALWAYS_ROUTE`

## Config Surface

The new `config.json` section is `ai_orchestration`.

Important fields:

- `enabled`
- `selection_mode`
- `max_parallel_candidates`
- `health_ttl_seconds`
- `registry_path`
- `REFINER_AI_ROUTING_PROFILES_PATH`
  Optional override for the shared routing-contract file. By default Refiner uses `config/ai-routing-profiles.json`.
- `providers`
  Extra provider descriptors with roles/specialties/weights.
- `engines`
  Non-LLM specialist engines such as AARNN or other `snn_aer`/AER-connected runtimes.

Example engine registry:

```json
{
  "ai_orchestration": {
    "engines": [
      {
        "name": "AARNNPrimary",
        "type": "aarnn",
        "repo_root": "${NM_LOCAL_REPO_ROOT}/aarnn_rust",
        "roles": ["planner", "assistant"],
        "specialties": ["aarnn", "snn", "neuromorphic", "aer"]
      },
      {
        "name": "VisionSpikes",
        "type": "snn_aer",
        "endpoint": "http://127.0.0.1:8010",
        "roles": ["reviewer"],
        "specialties": ["snn", "aer", "vision"]
      }
    ]
  }
}
```

## Operational Notes

- Concurrency is applied to planning/research/review calls, not to file mutation.
- Single-provider mode still works; the wrapper collapses back to one candidate when no alternatives are available.
- Single-LLM-provider mode still stays orchestrated when specialist engines are present, so specialist context is not dropped.
- Tests should prefer monkeypatching the local module `get_provider` call sites; the orchestration layer intentionally respects those factories.
