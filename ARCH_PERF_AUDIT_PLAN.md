# Architecture & Performance Audit (2026-03-18)

## Scope
- Backend API/runtime: `refiner/refiner_web.py`, `refiner/project_solver.py`, `refiner/llm_providers.py`
- STT low-latency path: `../nmstt/src/main.rs` + Python bridge in `refiner/refiner_web.py`
- Frontend surface in this repo: `web/static/*.js`
- Frontend alignment context (external repo): `neuralmimicry.ai-website/src/components/AIChatWidget.jsx`

## Baseline Snapshot
- `refiner/refiner_web.py`: 11,604 LOC, 88 Flask routes, 22 `requests.*` calls, 3 `subprocess.run` calls.
- `refiner/project_solver.py`: 10,130 LOC, 9 `requests.*` calls, 5 `subprocess.run` calls.
- `../nmstt/src/main.rs`: 2,106 LOC, async server with `spawn_blocking` + semaphore concurrency gating.
- `web/static/app.js`: 4,900 LOC / ~172 KB source file.
- External frontend build (already observed): main JS chunk warning around ~1.4 MB minified.

## Priority Findings
1. `P0` monolithic backend module risk: route, auth, orchestration, storage, and provider logic are coupled in `refiner/refiner_web.py`.
2. `P0` blocking request path risk: network + subprocess operations execute in request handlers (increases tail latency and error amplification).
3. `P0` uneven resilience policy: retries/backoff/session pooling are not consistently applied to external HTTP calls.
4. `P1` deployment/runtime risk: `refiner/refiner_web.py` runs Flask dev server in `__main__`; production worker model is not enforced.
5. `P1` frontend bundle/component concentration: very large chat widget/module path likely drives parse/execute cost.
6. `P2` observability gap: no unified request-stage timing metrics for STT/assistant critical paths.

## Phased Refactor Plan

## Phase 0 - Audit Guardrails (complete)
- [x] Capture baseline hotspots and bottlenecks.
- [x] Define refactor priorities and measurable targets.
- [x] Add repeatable benchmark scripts for p50/p95 endpoint latency and CPU saturation.

Success criteria:
- Stable benchmark harness checked into `scripts/` and executable in CI/local.

## Phase 1 - STT Path Hardening (complete)
- [x] Add resilient STT server client behavior (bounded retries + backoff + pooled HTTP sessions).
- [x] Add direct in-memory STT server path to avoid temp file round-trip when preprocess is not needed.
- [x] Add regression/integration tests for retry behavior and direct-path behavior.
- [x] Extend resilient HTTP helper use to remaining STT-adjacent outbound calls.
- [x] Add per-stage latency instrumentation (`extract`, `preprocess`, `transcribe`, `gesture_plan`).

Success criteria:
- `/api/voice/stt` p95 latency improved in server mode.
- Transient STT upstream failures no longer fail on first timeout spike.

## Phase 2 - Backend Modularization
- [x] Extract route wiring into modular registries for `voice` and `assistant` domains.
- [x] Keep handlers stable while decoupling route registration from the monolith file.
- [x] Continue split for `jobs`, `auth`, and `admin` route domains.
- [ ] Move more business logic into service modules and thin route handlers.
- [ ] Isolate request validation/serialization schemas.

Success criteria:
- No single backend module > 2,500 LOC.
- Route handlers average < 60 LOC and service functions are unit-testable.

## Phase 3 - Non-Blocking & Workload Isolation
- [x] Add bounded backpressure for STT and assistant request paths via capacity semaphores.
- [x] Return explicit `503` capacity errors instead of overloading worker threads.
- [x] Move long-running job/workspace operations off request threads into background workers.
- [x] Add bounded queues + backpressure + cancellation/timeout policies for job actions.
- [x] Standardize external-call timeout/retry policy for Continuum + GitHub integration paths.

Success criteria:
- API p95 remains stable under concurrent background load.
- Queue depth and worker saturation metrics exported.

## Phase 4 - Rust Expansion for Latency-Critical Paths
- [x] Keep STT in Rust as primary low-latency path.
- [x] Add Rust `/gesture-plan` endpoint to reuse Rust motion planning beyond transcription.
- [x] Wire Python assistant/STT gesture fallback to Rust endpoint with safe fallback to Python planner.
- [x] Evaluate migration of additional canonicalization hot loops to Rust boundary.
- [x] Define strict typed API contracts between Python backend and Rust services.

Success criteria:
- CPU-heavy post-processing shifted off Python request threads.
- Measured lower tail latency for motion payload generation.

## Phase 5 - Frontend Decomposition & Bundle Control
- [x] Split large chat widget utilities into a dedicated reusable module (`sttTextUtils`).
- [x] Code-split motion/voice-heavy UI (`ChatOfficeEnvironment`) behind a lazy boundary.
- [x] Add bundle-size budget checks script for this repository static assets.
- [x] Add repeatable API benchmark script for latency/throughput checks.

Success criteria:
- Main chunk significantly reduced.
- Interaction responsiveness (initial input, open-widget latency) improves on mid-tier devices.

## KPI Targets
- Backend API p95 latency: reduce by >=30% on STT and assistant critical paths.
- Error rate from transient upstream failures: reduce by >=50%.
- Frontend initial JS transfer/parse: reduce main bundle by >=35%.
- Coverage: add focused integration tests for each critical path refactor.
