# Refiner vs Open Claude Code

## Scope

This note analyses Refiner's current codebase intent and compares it with the
public `Heigke/open-claude-code` repository. The goal is not code reuse. The
goal is to identify concepts that match Refiner's architecture and improve its
own implementation without importing a mismatched product model.

## Refiner: workflows, intent, and operating model

### Primary workflows

Refiner currently operates as a multi-workflow engineering platform rather than
a single-purpose RAG application.

- `refiner/run_refiner.py` is the top-level workflow router.
- `refiner/main.py` still owns the historical Jira analytics path.
- `refiner/topic_researcher.py` handles iterative evidence gathering and drafting.
- `refiner/project_solver.py` is the coding and remediation engine for requirement
  sources inside a local repository.
- `refiner/delivery_pipeline.py` provides staged execution and gating.
- `refiner/refiner_web.py` exposes the control-plane UI and JSON APIs for jobs,
  sessions, workspaces, RAG, MCP, STT, auth, and admin operations.

### Structural intent

Refiner's code consistently shows the following intent:

- configuration-driven behaviour instead of hardcoded company logic,
- traceable requirement handling using explicit requirement IDs,
- incremental refinement rather than one-shot generation,
- verification-first execution for coding workflows,
- privacy-aware reuse and redaction,
- fallbacks for external dependencies and provider quotas,
- file-based, inspectable state over opaque background services,
- service-split readiness for identity, billing, STT, and chain ledgers.

### Tone and aims in the code

The code tone is pragmatic, operational, and safety-conscious:

- prompts and helper modules push for deterministic baselines before optional
  enhancements,
- CLI and web flows prefer auditability and resumability,
- path safety, secret handling, and restricted roots are recurring themes,
- the solver already assumes model output is fallible and needs repair,
  verification, and retries.

## Open Claude Code: what actually matches Refiner

The external repository exposes three categories of ideas:

### Strong conceptual matches

- context pressure management,
- permission and trust boundaries for tool execution,
- persistent session or episode memory,
- background task and session abstractions,
- replay or telemetry-oriented analysis of agent behaviour.

### Partial matches

- multi-agent coordination,
- skill loading and command routing,
- remote session concepts,
- task state models.

These are directionally relevant but would need significant adaptation to fit
Refiner's JSON report and Flask control-plane model.

### Weak or poor matches

- terminal-first React/Ink UI,
- IDE bridge and direct connect layers,
- plugin marketplace and extension management,
- buddy or companion UX features.

Those are product-shape features for a developer REPL. Refiner is currently a
workflow engine and service edge, so those areas would add complexity without
addressing the present bottlenecks.

## Recommended concept mapping

### 1. Predictive context management -> Refiner solver prompt budgeting

Refiner's solver already has many high-value context sources:

- requirements register,
- source context,
- repo search matches,
- related tests,
- explicit file excerpts,
- verification failures,
- audit findings,
- web research,
- progress history,
- previous actions.

The weakness was that these sections were concatenated monolithically. The best
matching concept from the external repo is not "chat compaction" in general,
but explicit section prioritisation for constrained prompt assembly.

### 2. Episodic memory -> Refiner per-source solver memory

Refiner already had:

- in-run progress memory via `ProgressTracker`,
- previous JSON output resume logic,
- source-specific action logs.

What it lacked was durable, queryable memory across runs that could answer:

- what failed last time for this exact requirement source,
- which commands were useful,
- which files were touched,
- whether the last attempt ended in success, partial progress, failure, or
  defer.

That maps directly to episodic memory, but Refiner does not need embeddings or
session chat memories here. A compact lexical store is the right fit.

### 3. Permission and trust ideas -> Refiner solver command policy

The external repo has a broad interactive permission system. Refiner's solver
is non-interactive in this path, so the direct analogue is a command execution
policy:

- reject shell chaining,
- reject privilege escalation,
- reject destructive commands,
- execute vetted commands with `shell=False`,
- record why blocked commands were denied.

This is a better fit for Refiner than adding an interactive trust dashboard.

## Implemented in this pass

### `refiner/solver_memory.py`

Adds a durable solver episode store with:

- append-only JSONL persistence,
- bounded compaction,
- lexical relevance scoring by source path, requirement IDs, recency, and text
  overlap,
- prompt-ready formatting for the current requirement source.

### `refiner/solver_context.py`

Adds prompt section budgeting with:

- explicit section priorities,
- required-section retention,
- optional-section omission when over budget,
- deterministic trimming for large sections,
- a transparent inclusion report.

### `refiner/solver_command_policy.py`

Adds a non-interactive execution policy with:

- blocking for shell control operators and pipe-to-shell bootstraps,
- blocking for destructive git and system commands,
- support for environment-prefixed simple commands,
- preparation for `subprocess.run(..., shell=False)`.

### `refiner/project_solver.py`

Updated to:

- use `.refiner/` as internal solver metadata storage,
- load and query episodic memory during prompt construction,
- assemble prompts through section budgeting instead of raw concatenation,
- execute commands through the new policy layer,
- persist iteration outcomes as solver episodes.

## Future candidates worth considering

### Session replay for solver runs

Refiner already logs rich action traces. A replay or post-run analyser would be
useful for:

- identifying repeated failure loops,
- measuring verification hit rate,
- highlighting expensive context sections that rarely matter,
- surfacing tool or command bottlenecks.

This fits better as offline analysis than a live REPL feature.

### Job-level trust history

If Refiner later needs more automation in shared workspaces, it could add a
small trust ledger on top of the command policy:

- successful verification commands increase trust,
- new command shapes still require stricter handling,
- high-trust commands may allow reduced friction in controlled modes.

That should remain subordinate to the hard safety blocks added here.

### Assistant memory beyond the solver

The same episodic pattern could later be extended to:

- `/api/playground/plan`,
- assistant requirement drafting,
- delivery pipeline recovery loops.

For now, the solver is the highest-value target because it already has the
strongest requirement traceability and verification model.

## Additional refinements after the second comparison pass

The next best matches from the external repository were not more agent types or
terminal UX. The useful additions were smaller and operational:

- adaptive feedback from recent solver replay data, so prompt construction can
  warn about recurring verification failures, prompt-budget omissions, and
  unstable command shapes before the next iteration runs,
- operator-facing controls for the new profile-backed defaults, so the web UI
  exposes assistant memory, solver safety, and LLM defaults directly instead of
  leaving them hidden behind `POST /api/profile`,
- deferring automatic model-routing until Refiner has broader persisted
  provider latency/cost telemetry, because premature routing heuristics would
  add policy complexity without enough signal.

## Components intentionally not recommended

- UI bridge, React terminal UX, and remote desktop handoff
- plugin marketplace and plugin autoupdate layers
- buddy or companion systems
- full chat-session memory files for every workflow

These are expensive to integrate and do not address Refiner's current
operational constraints.

## Summary

The best-matching ideas from `open-claude-code` for Refiner were:

1. prioritized context assembly,
2. durable episodic memory,
3. stricter execution permissions.

Those ideas are now implemented in Refiner's own style: file-based,
inspectable, deterministic, and centred on requirement-source workflows rather
than a terminal chat product.
