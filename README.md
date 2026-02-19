# Refiner

Refiner is a lightweight reporting and analysis toolkit for Jira and Confluence. It discovers relevant scope (projects, epics, and spaces), fetches only the necessary data, and produces CSV/HTML reports on throughput, timelines, and resourcing. It also includes LLM-backed workflows for topic research and project solving.

Key aspects:
- Configuration-driven: company names, Jira URL, custom field IDs, engineer lists, and issue schemas live outside the code in config.json and environment variables.
- Minimal downloads: an optional discovery step narrows JQL so Jira performs most filtering server-side.
- Reusable core: algorithms are data-agnostic so the tool can be reused across organisations and schemas.
- Small-batch fetching with local cache: issue searches are paginated (page_size configurable) to reduce per-request load. A lightweight JSONL cache accumulates fetched pages and can be used as a last-resort data source when servers return little/no data.
- Multi-workflow CLI: a single entry point drives Jira statistics, Jira quality analysis, Confluence space analysis, topic research, and project solving.
- Project solver: scan a local folder for requirement signals, derive a plan with an LLM, and optionally apply file edits/run commands.
- Agentic workflows: explicit plan → act → verify → reflect loops, verification-first execution, and role-based LLM overrides for planning/review.


## Quickstart
1) Install dependencies
- pip install -r requirements.txt
- Optional package install for CLI: pip install -e .

2) Configure
- Copy or edit config.json to match your Jira/Confluence instance(s).
- Optionally export environment variables (see below) to supply credentials and overrides.

3) Run (choose a workflow)
- Default Jira statistics: refiner (or python run_refiner.py)
- Jira quality analysis: refiner --analyze-jira --projects CAT --output jira_report.html
- Confluence analysis: refiner --analyze-confluence --space CAT --output confluence_report.html --use-rovo
- Topic research: refiner --topic-research req.txt --output researched_document.md --llm-provider openai
- Project solver: refiner --project /path/to/project --llm-provider openai --output project_solution.json

## Web UI + API auth
The web UI is backed by the same Flask server (`refiner_web.py`). It uses session cookies, with dedicated JSON endpoints for headless or cloud-hosted frontends.

### API auth endpoints
- `POST /api/login` with JSON `{ "username": "...", "password": "..." }` sets the session cookie.
- `POST /api/logout` clears the session cookie.
- `GET /api/session` returns `{ authenticated, user, role }`.
- `POST /api/setup` creates the first admin account (only allowed when no users exist).

The web login and setup pages now call these endpoints directly.

### Cross-origin setup (cloud frontend + public backend)
Set these environment variables on the backend:
- `REFINER_CORS_ORIGINS`: comma-separated allowlist of frontend origins, e.g. `https://app.example.com`.
- `REFINER_ENFORCE_HTTPS`: set to `1` to require HTTPS (recommended for public endpoints).
- `REFINER_TRUST_PROXY`: set to `1` if TLS is terminated by a proxy/load balancer.
- `REFINER_HOST`: set to `0.0.0.0` for public bind.

Cookies default to `SameSite=None` + `Secure` when CORS is enabled. Use `REFINER_COOKIE_SAMESITE` / `REFINER_SECURE_COOKIES` to override.

On the frontend, set the API base:
- Add `<meta name="rag-api-base" content="https://api.example.com">` to the page, or
- Set `window.__RAG_API_BASE = "https://api.example.com"` before loading the scripts.

### Deployment (cloud UI + public API)
1) Run the backend behind TLS (reverse proxy or managed load balancer) and expose it on a public domain.
2) Configure the backend:
   - `REFINER_HOST=0.0.0.0`
   - `REFINER_ENFORCE_HTTPS=1`
   - `REFINER_TRUST_PROXY=1` (if TLS terminates upstream)
   - `REFINER_CORS_ORIGINS=https://your-frontend.example`
3) Point the frontend at the backend origin using `rag-api-base` or `window.__RAG_API_BASE`.
4) Bootstrap the first user with `POST /api/setup`, then log in with `POST /api/login`.

### Prometheus/Grafana metrics
Refiner exposes Prometheus-compatible metrics on `GET /metrics` by default (same port as the web server).
- Disable or change the path with `REFINER_METRICS_ENABLED` or `REFINER_METRICS_PATH`.
- The backend exports request counts/latency, in-flight requests, uptime, job counts by status, queue depth, and worker threads.
- The frontend-only server exports its own request/latency/uptime metrics with a `refiner_frontend_*` prefix.

### Container image (multi-arch)
Build the Podman/Docker image from `Containerfile` for multiple architectures:
- Podman (multi-arch with buildx-style output):
  - `podman build --platform linux/amd64,linux/arm64 -t refiner:latest -f Containerfile .`
- Docker buildx:
  - `docker buildx build --platform linux/amd64,linux/arm64 -t refiner:latest -f Containerfile .`

Run the image (choose one mode):
- Backend only: `podman run -p 5001:5001 refiner:latest backend`
- Frontend only: `podman run -p 8080:8080 -e REFINER_API_BASE=https://api.example.com refiner:latest frontend`
- Combined: `podman run -p 5001:5001 refiner:latest full`

Publish to GitHub Container Registry (GHCR) (default org: `neuralmimicry`, user: `masterkiga`):
- Log in (use a GitHub PAT with `write:packages`):
  - `GHCR_USER=masterkiga; echo "$GHCR_TOKEN" | podman login ghcr.io -u "$GHCR_USER" --password-stdin`
  - `GHCR_USER=masterkiga; echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin`
- Tag the image:
  - `podman tag refiner:latest ghcr.io/neuralmimicry/refiner:latest`
  - `docker tag refiner:latest ghcr.io/neuralmimicry/refiner:latest`
- Push:
  - `podman push ghcr.io/neuralmimicry/refiner:latest`
  - `docker push ghcr.io/neuralmimicry/refiner:latest`
- Or build + push multi-arch in one step (Docker buildx):
  - `docker buildx build --platform linux/amd64,linux/arm64 -t ghcr.io/neuralmimicry/refiner:latest -f Containerfile . --push`

The default Jira statistics workflow can (optionally) run discovery, refine your JQL, fetch issues, generate monthly CSVs and a leaderboard, and write a consolidated timelines.csv. If the refined JQL returns no results, the tool automatically retries with your base JQL to avoid empty runs due to over-filtering.


## Workflow overview
Workflow selection order (run_refiner.py):
1) --topic-research
2) --delivery
3) --project
4) --analyze-confluence
5) --analyze-jira
6) Default Jira statistics workflow (unless --disable-jira is set)

Summary of workflows:

| Workflow | Trigger | Primary outputs | Summary |
| --- | --- | --- | --- |
| Jira statistics | No workflow flags | CSVs + charts | Discovery-driven reporting across Jira instances (timelines, worklogs, leaderboard). |
| Jira quality analysis | --analyze-jira | jira_report.html | Interactive HTML report with optional LLM insights, action plan, and comment posting. |
| Confluence space analysis | --analyze-confluence --space | confluence_report.html | Interactive HTML report with optional LLM/Rovo insights, action plan, and comment posting. |
| Topic research | --topic-research | researched_document.md (+ references) | Iterative research with Jira/Confluence context and optional web search. |
| Delivery pipeline | --delivery --project | delivery_pipeline_output/pipeline_report_*.json | Staged sandbox/dev/integration/staging/uat/deploy pipeline with approvals and artifact capture. |
| Project solver | --project | project_solution.json | Requirements extraction, planning, and optional code changes/commands. |


## Workflow diagrams
### Workflow selection (run_refiner.py)
```mermaid
flowchart TD
    A[Start: parse CLI arguments] --> B[Configure logging and environment overrides]
    B --> C{--topic-research provided?}
    C -- Yes --> TR[Topic research workflow]
    C -- No --> D{--delivery set?}
    D -- Yes --> DP[Delivery pipeline workflow]
    D -- No --> E{--project provided?}
    E -- Yes --> PS[Project solver workflow]
    E -- No --> F{--analyze-confluence set?}
    F -- Yes --> CF[Confluence space analysis workflow]
    F -- No --> G{--analyze-jira set?}
    G -- Yes --> JA[Jira quality analysis workflow]
    G -- No --> H{--disable-jira set?}
    H -- Yes --> I[Exit: Jira disabled]
    H -- No --> JS[Default Jira statistics workflow]
```

### Jira statistics workflow (default)
```mermaid
flowchart TD
    A[Load config] --> B[Iterate Jira instances]
    B --> C[Get credentials]
    C --> D{Discovery enabled?}
    D -- Yes --> E[Discover projects/epics/spaces; refine JQL]
    D -- No --> F[Use base JQL]
    E --> G[Fetch issues with fallbacks and cache]
    F --> G
    G --> H[Compute timelines, worklogs, leaderboard]
    H --> I[Write CSVs and charts]
```

### Jira quality analysis workflow
```mermaid
flowchart TD
    A[Load config + LLM settings] --> B[Get Jira credentials]
    B --> C[Resolve scope: --jql or --projects]
    C --> D[Fetch issues + linked Confluence content (optional)]
    D --> E{LLM enabled and not --dry-run?}
    E -- Yes --> F[Generate LLM insights per issue]
    E -- No --> G[Baseline metrics only]
    F --> H[Render HTML report]
    G --> H
    H --> I{--post-comments?}
    I -- Yes --> J[Post comments to Jira/Confluence]
    I -- No --> K[Finish]
```

### Confluence space analysis workflow
```mermaid
flowchart TD
    A[Load config + LLM settings] --> B[Get Jira/Confluence credentials]
    B --> C[Fetch space and page hierarchy]
    C --> D{Selection manifest provided?}
    D -- Yes --> E[Scope to selected pages]
    D -- No --> F[Use default scope/tree depth]
    E --> G{--use-rovo?}
    F --> G
    G -- Yes --> H[Attempt Rovo analysis; fallback if unavailable]
    G -- No --> I[Compute baseline metrics]
    H --> J{LLM enabled and not --dry-run?}
    I --> J
    J -- Yes --> K[Run LLM analysis per branch/page]
    J -- No --> L[Skip LLM analysis]
    K --> M[Render HTML report]
    L --> M
    M --> N{--post-comments?}
    N -- Yes --> O[Post executive summary/page insights]
    N -- No --> P[Finish]
```

### Topic research workflow
```mermaid
flowchart TD
    A[Load config + LLM/search settings] --> B[Read topic + requirements]
    B --> C[Gather Jira/Confluence context (unless disabled)]
    C --> D{Google Search configured?}
    D -- Yes --> E[Search the web and fetch sources]
    D -- No --> F[Skip web search]
    E --> G[Iterative draft/critique/refine loop]
    F --> G
    G --> H[Write researched document + references]
```

### Delivery pipeline workflow
```mermaid
flowchart TD
    A[Load pipeline config + compute version] --> B[Prepare workspaces and approvals]
    B --> C[Sandbox tests]
    C --> D[Dev checks]
    D --> E[Integration tests]
    E --> F{Approval for staging?}
    F -- Yes --> G[Staging smoke tests]
    F -- No --> H[Halt for approval]
    G --> I{Approval for UAT?}
    I -- Yes --> J[UAT stage]
    I -- No --> K[Halt for approval]
    J --> L{Approval for deploy?}
    L -- Yes --> M[Deploy stage]
    L -- No --> N[Halt for approval]
    M --> O[Write pipeline report]
```

### Project solver workflow
```mermaid
flowchart TD
    A[Load config + LLM settings] --> B{--requirements provided?}
    B -- Yes --> C[Use requirements document]
    B -- No --> D[Scan project for requirement signals]
    C --> E[Derive requirements register]
    D --> E
    E --> F[Plan steps with LLM]
    F --> G{--project-run enabled?}
    G -- Yes --> H[Apply steps + run commands]
    G -- No --> I[Produce plan only]
    H --> J[Write project_solution.json + summary]
    I --> J
```

## Workflow details and key flags
### Jira statistics workflow (default)
- Trigger: no workflow flags and Jira is not disabled.
- Scope: uses discovery (if enabled) to refine the base JQL; falls back to broader queries if results are sparse.
- Outputs: CSVs and charts listed in the Outputs section.
- Key controls: discovery/search settings in config.json plus environment overrides (e.g., PREFER_CLIENT_SEARCH, RECENT_DAYS).

### Jira quality analysis workflow
- Trigger: `--analyze-jira`.
- Scope: `--jql` overrides `--projects`. If neither is provided, the analyser falls back to discovery or a safe default query.
- Optional selection: `--selection` can limit analysis using the HTML report selection manifest.
- LLM: configure with `--llm-provider`/`--llm-model` and optional fallbacks; use `--dry-run` to skip LLM calls while still producing the HTML report.
- Action plans: `--action-plan` adds an action plan section to the report (LLM-backed when available).
- Comment posting: `--post-comments`, `--post-target`, and `--dry-run-post`.

### Confluence space analysis workflow
- Trigger: `--analyze-confluence --space <KEY>`.
- Scope controls: `--tree-depth`, `--starting-depth`, and `--selection` (selection manifest).
- Rovo/LLM: `--use-rovo` attempts Rovo analysis; use `--llm-provider`/`--llm-model` for LLM analysis and `--dry-run` to skip LLM calls.
- Action plans: `--action-plan` adds an action plan section to the report (LLM-backed when available).
- Templates: `--emit-templates` and `--templates-dir` for local template output.
- Comment posting: `--post-comments`, `--post-exec-summary`, `--post-page-insights`, and `--dry-run-post`.

### Delivery pipeline workflow
- Trigger: `--delivery --project <PATH>`.
- Config: `delivery_pipeline.json` by default (override with `--delivery-config`, or use `--delivery-config default` to force the bundled config).
- Safety: commands only execute when `--delivery-run` is supplied; otherwise the pipeline is a dry-run plan.
- Approvals: create `delivery_pipeline_output/approvals/<stage>.ok` (or set `approval_file` per stage) to unblock gated stages.
- Optional solver integration: `--delivery-project-solution` overlays the solver workspace and records completion status in the report.
- Override: use `--delivery-allow-unfinished` to permit deploy stages on incomplete code for interim validation.
- Interim stages: use `--delivery-enable-interim` to enable the optional `interim_deploy` + `interim_teardown` stages without editing the config.
- Versioning: computed from git (or timestamp fallback) and optionally written to `delivery_pipeline_output/VERSION` when `versioning.write_file` is enabled.
- VCS integration: configure `vcs` in `delivery_pipeline.json` (defaults to `github.com/neuralmimicry`); supports pull/branch/commit/merge/push/tag/release actions.
- You can gate VCS actions with `vcs.requires_approval` and `vcs.approval_file` if you want a manual check before git operations run.
- Platform auto-selection: configure `platform` in `delivery_pipeline.json` to pick the lowest-cost viable tier based on detected tooling (QEMU, Podman, Docker, Kubernetes, OpenShift, GCP, AWS, Azure). The selection is exported via `PIPELINE_PLATFORM*` env vars for stage commands.
- Solver gating: `solver_gate` controls how incomplete solver output affects deploy stages (`block_all`, `block_deploy`, `warn`). Use `--delivery-allow-unfinished` or `allow_unfinished_deploy` to allow deploy stages even when the project is still incomplete.
- Stage kinds: `kind` influences solver gating (e.g., `deploy`, `staging`, `uat` are gated; `sandbox_deploy` or `test` are not), and is exposed as `PIPELINE_STAGE_KIND`.
- Optional interim deploy/teardown: `interim_deploy` and `interim_teardown` stages are included in the default config but disabled (`enabled: false`) so teams can toggle them for iterative sandbox validation.
- If any delivery flags are provided without `--delivery`, delivery mode is enabled automatically.
- Auto-recovery: set `auto_recover` and `retry_attempts` in `delivery_pipeline.json` to enable intelligent retries for common failures (missing venv, missing requirements, missing pytest, missing pip/wheel, poetry/pipenv project detection, pytest retry of last failed tests, and missing toolchains detection).
- Multi-language support: language/build-system detection is exported as `PIPELINE_LANGUAGES` and `PIPELINE_BUILD_SYSTEMS` for stage scripts, covering Python, JS/TS, Go, Rust, C/C++, Fortran, Pascal, Bash, and PowerShell.
- Solver fallback: configure `solver_fallback` to invoke `project_solver` after build/test failures and retry the stage, logging each attempt in the pipeline report.
- CLI overrides: `--delivery-solver-fallback` forces solver fallback on, `--delivery-no-solver-fallback` forces it off.
- Solver focus: fallback attempts now extract file paths, symbols, and REQ IDs from failure logs to start the solver near the failing code, with file excerpts embedded in the solver context.
- Diff-based prioritization: solver context includes recent git changes and recently touched workspace files so the solver can prioritise likely generated/modified files (without forcing a scope).

### Shared LLM and rate-limit controls
- `--llm-provider`, `--llm-model`, `--fallback-llm-provider`, `--fallback-llm-model`, `--ollama-base-url`.
- `--llm-max-tokens`, `--llm-chunk-size`, `--llm-temperature`, `--llm-timeout`, `--llm-inter-request-gap`, `--llm-reasoning-effort`.


## Configuration
This repository ships with a default config.json at the project root. You can tailor it per company or environment without changing code.
The delivery pipeline uses `delivery_pipeline.json` (stages, approvals, artifacts, and workspace settings).

Example VCS config (defaults to GitHub + NeuralMimicry owner):
```json
{
  "vcs": {
    "enabled": true,
    "owner": "neuralmimicry",
    "repo": "refiner",
    "actions": [
      {"type": "pull"},
      {"type": "branch", "name": "solver/{version}"},
      {"type": "commit", "message": "chore: solver {version}"},
      {"type": "push"},
      {"type": "tag", "name": "v{version}"}
    ]
  }
}
```

Example platform config (auto-select lowest viable tier):
```json
{
  "platform": {
    "auto": true,
    "preferred_order": ["local", "container", "k8s", "openshift", "cloud"],
    "container_preference": ["podman", "docker"],
    "cloud_preference": ["gcp", "aws", "azure"],
    "require_emulation": false
  }
}
```

Example solver gate config:
```json
{
  "solver_gate": "block_deploy",
  "allow_unfinished_deploy": false
}
```

Example solver fallback config:
```json
{
  "solver_fallback": {
    "enabled": true,
    "max_attempts": 2,
    "on_failure_types": ["test_failure", "pytest_import_error"],
    "requirements_only": true,
    "allow_run": false,
    "max_steps": 25,
    "max_iterations": 2,
    "use_workspace": true
  }
}
```

Example config.json
```json
{
  "instances": [
    {
      "name": "Instance A",
      "jira_url": "https://instance-a.atlassian.net",
      "confluence_url": "https://instance-a.atlassian.net/wiki"
    },
    {
      "name": "Instance B",
      "jira_url": "https://instance-b.atlassian.net"
    }
  ],
  "data_files": {
    "engineer_names": "engineer_names.csv",
    "leaderboard": "leaderboard.csv",
    "monthly_csv_prefix": "monthly_subtask_summary_data",
    "timelines": "timelines.csv",
    "gantt_projects": "gantt_projects.png"
  },
  "issue_types": ["Bug", "Improvement", "New Feature", "Spike", "Epic", "Story", "Task", "Sub-task"],
  "priority_ranking": {"Highest": 1, "High": 2, "Medium": 3, "Low": 4, "Lowest": 5},
  "issue_ranking": {"Epic": 1, "Bug": 2, "Spike": 3, "New Feature": 4, "Improvement": 5, "Story": 6, "Task": 7, "Sub-task": 8},
  "custom_fields": {
    "skills_field": "customfield_10900",
    "workstream_field": "customfield_10952",
    "universe_skill_name": "UniVerse"
  },
  "office_hours": {
    "start_hour": 9,
    "end_hour": 17,
    "country": "GB"
  },
  "jql_query": "ORDER BY Rank",
  "discovery": {
    "enabled": true,
    "keywords": ["CTO", "DNP", "DNT", "Digital Network Products"],
    "confluence_space_keys": [],
    "jira_project_keys": [],
    "cache_ttl_minutes": 120
  }
}
```

Notes
- instances: List of Jira/Confluence instances to query. Each instance needs `name` and `jira_url`, and optionally `confluence_url`.
- data_files.* control input/output filenames (including timelines.csv and gantt_projects.png).
- custom_fields.* let you map instance-specific field IDs once, instead of changing code.
- office_hours define the workday and holiday region. Supported codes include GB/UK, US, CA, DE, FR; unknown codes fall back to GB.
- jql_query provides a base JQL that discovery can refine at runtime.
- search: controls search behaviour. Keys (defaults shown):
  - prefer_client (bool, default: true): when true or env PREFER_CLIENT_SEARCH=1, use the python-jira client directly (most compatible with Atlassian Cloud). Set to false to prefer HTTP /search/jql.
  - page_size (int, default: 100): pagination size for HTTP/client explicit pagination.
  - fail_fast_http (bool, default: true): after first 4xx from /search/jql, immediately fall back to client search.
  - allow_alt_shapes (bool, default: true): try alternative JSON shapes for /search/jql for broader compatibility.
  - debug (bool, default: false): enable verbose diagnostics for search; can also use env DEBUG_SEARCH=1.
  - recent_days (int, default: 180): time window for bounded fallbacks when refined/base JQL yield no or minimal results.
  - min_results (int, default: 20): if a refined query returns fewer than this number (but more than zero), constraints are relaxed and retried to broaden selection.
  - force_ultra_broad (bool, default: false): when true (or env FORCE_ULTRA_BROAD=1), bypass discovery and run an ultra-broad query first: updated >= -recent_days, preserving any ORDER BY.
  - allow_extreme_broad (bool, default: true): when all refined/base and fallback queries (including ultra-broad) return 0, perform one last bounded attempt with no WHERE clause, i.e., "ORDER BY created DESC" to fetch the most recent issues available. Can be disabled via env ALLOW_EXTREME_BROAD=0.
  - enable_user_scoped_fallback (bool, default: true): when broader instance-level queries still yield no results, try a user-scoped recent activity query: (assignee = currentUser() OR reporter = currentUser()) AND updated >= -recent_days.
  - try_created_window (bool, default: true): in addition to updated-based windows, also try created >= -recent_days to catch old-but-recently-created issues where updated field may not reflect activity.
  - avoid_rank_order (bool, default: false): when true (or env AVOID_RANK_ORDER=1), replace any trailing "ORDER BY Rank" with a safer, portable sort to avoid Rank-related permission/index issues on some Jira instances.
  - rank_fallback (string, default: "created"): the field to use when replacing Rank; accepted values: "created", "updated". Sorting direction is DESC.
  - enable_cache (bool, default: true): enable lightweight JSONL caching of fetched pages to progressively build a local dataset.
  - issues_cache (string, default: ".issues_cache.jsonl"): path to the JSONL cache file.
  - prefer_cache_for_fallbacks (bool, default: true): when all remote attempts return 0, fall back to using cached issues (within cache_max_age_days) to generate reports.
  - cache_max_age_days (int, default: 7): only use cached issues fetched within the last N days.
  - iterate_per_project (bool, default: false): when true (or env ITERATE_PER_PROJECT=1), refined queries that target many projects will be executed per project (project = KEY) in small chunks and merged locally. This reduces server load and avoids overly broad project-in filters that may return zero results. ORDER BY is preserved per sub-query.
  - probe_accessible_projects (bool, default: true): when enabled (or env PROBE_ACCESSIBLE_PROJECTS=1), after discovery the tool probes each discovered project with a tiny query (max 1 result) to ensure the project actually returns at least one visible issue for the current user. Only accessible projects are kept when building the refined JQL. Prints a diagnostic summary like "Project accessibility probe: X of Y projects accessible".
  The fetch order is: client path if prefer_client=true → otherwise try `/rest/api/3/search/jql` (top-level payload) → if 4xx and fail_fast_http=true, go straight to python-jira client; otherwise retry once with explicit `fields`/`expand` in the body → then client fallback → optional batch payload retry.

Sorting configuration
- custom_fields.priority_index_field: Optional custom field id (e.g., "customfield_10104") used to sort issues alphanumerically when generating reports.
  - If absent on an issue, the tool falls back to Jira's native priority (mapped via priority_ranking) and finally to the issue key for a stable order.


## Discovery and field identification
- The discovery phase probes Confluence (via CQL) and Jira to identify related spaces/pages, candidate project keys, and epic keys based on configured keywords.
- Results are cached to .discovery_cache.json for the configured TTL to avoid repeated probing.
- Jira field metadata is inspected to identify likely candidates for:
  - Start date, End date, Due date, Updated, Created, Resolution date,
  - Progress, Status category change date, Assignee, Epic Link.
- These fields drive a consolidated timelines report even if your instance uses custom field IDs; safe fallbacks are used when fields are unavailable.


## Outputs
Jira statistics workflow:
- Leaderboard CSV → data_files.leaderboard
- Monthly summary CSV(s) → prefixed by data_files.monthly_csv_prefix
- Consolidated timelines CSV → data_files.timelines
- Programme plan Gantt chart (projects) → data_files.gantt_projects (PNG)
- Optional pie charts per month for workstream distribution
- Optional cache file accumulating fetched issues → search.issues_cache (JSON Lines)

Jira quality analysis workflow:
- Interactive HTML report → jira_report.html (or `--output`)

Confluence space analysis workflow:
- Interactive HTML report → confluence_report.html (or `--output`)

Topic research workflow:
- Researched document → researched_document.md (or `--output`)
- References file → `--references-output` (optional)

Project solver workflow:
- JSON report → project_solution.json (or `--output`)
- Solver workspace directory → project_solver_output (or `--project-output-dir`)

### Optional inputs
- engineer_names.csv: If present (path configured via data_files.engineer_names), the report uses it to determine active seniors by time window. If absent, the run proceeds without senior filtering and prints a concise warning.


## Environment variables
- JIRA_USERNAME: Default Jira username (email for Atlassian Cloud)
- JIRA_PASSWORD: Default Jira API token or password
- For multiple instances, you can use instance-specific overrides:
  - `JIRA_USERNAME_<INSTANCE_NAME>`
  - `JIRA_PASSWORD_<INSTANCE_NAME>`
  - (The instance name should be normalised: uppercase, spaces/dashes replaced by underscores, e.g., `JIRA_USERNAME_INSTANCE_A`)
- JQL_QUERY: Optional base JQL; overrides config.json:jql_query
- DISCOVERY_KEYWORDS: Optional comma-separated override for discovery.keywords
- DISCOVERY_DISABLE: If set to 1/true/yes, disables discovery regardless of config
- PREFER_CLIENT_SEARCH: If set to 1/true/yes, skip HTTP /search calls and use the python-jira client directly
- DEBUG_TRANSITIONS: If set to 0/false/no, suppresses status transition debug logging. By default, each status change is logged with its timestamp to aid troubleshooting.
- DISABLE_JIRA: If set to 1/true/yes, disables all Jira-related operations
- DISABLE_CONFLUENCE: If set to 1/true/yes, disables all Confluence-related operations
- RECENT_DAYS: Overrides search.recent_days; bounds the fallback windows (e.g., updated >= -180d)
- MIN_RESULTS: Overrides search.min_results; threshold below which the tool relaxes constraints to broaden the selection
- FORCE_ULTRA_BROAD: If set to 1/true/yes, bypass discovery and directly run a broad query: updated >= -RECENT_DAYS (ORDER BY preserved).
- ENABLE_USER_SCOPED_FALLBACK: If set to 0/false/no, disables the user-scoped recent activity fallback.
- TRY_CREATED_WINDOW: If set to 0/false/no, disables the created >= -RECENT_DAYS fallback.
- LLM_TIMEOUT_SECONDS: Override the default 180-second timeout for LLM requests (e.g. 300 for Ollama).
- GOOGLE_API_KEY: Google Search API Key.
- GOOGLE_CSE_ID: Google Search Engine ID (CX).
- OPENAI_API_KEY: OpenAI API key for OpenAI/GPT providers.
- GEMINI_API_KEY: Google Gemini API key.
- GEMINI_ACCESS_TOKEN: Google Gemini OAuth 2.0 access token.
- AVOID_RANK_ORDER: If set to 1/true/yes, replaces trailing "ORDER BY Rank" with "ORDER BY <rank_fallback> DESC" in constructed queries.
- RANK_FALLBACK: Field name to use when replacing Rank; supports "created" or "updated". Defaults to "created".
- ENABLE_CACHE: If set to 0/false/no, disables on-disk cache of fetched issues.
- PREFER_CACHE_FOR_FALLBACKS: If set to 0/false/no, disables using the cache as a last-resort data source.
- CACHE_MAX_AGE_DAYS: Override max age for using cached issues.
- ITERATE_PER_PROJECT: If set to 1/true/yes, enable per-project iteration of refined queries as described above.
- PROBE_ACCESSIBLE_PROJECTS: If set to 0/false/no, disables the post-discovery project accessibility probe described above.
- SOLVER_REPO_RAG: If set to 0/false/no, disables repo context indexing for the project solver (default: enabled).
- SOLVER_REPO_RAG_MAX_FILES: Max files to index for repo context (default: 300).
- SOLVER_REPO_RAG_MAX_BYTES: Max bytes per file for repo context (default: 200000).
- SOLVER_VERIFICATION_FIRST: If set to 0/false/no, allows continuing without replanning on verification failures.
- SOLVER_WEB_RESEARCH: Web research mode for project solver (auto/always/never). Auto enables when search credentials exist.
- SOLVER_WEB_RESEARCH_EVERY_ITERATIONS: Web research cadence by iteration (default: 4).
- SOLVER_WEB_RESEARCH_EVERY_STEPS: Web research cadence by applied steps (default: 60).
- SOLVER_WEB_RESEARCH_MAX_QUERIES: Max queries per research pass (default: 2).
- SOLVER_WEB_RESEARCH_MAX_RESULTS: Max results per query (default: 3).
- SOLVER_WEB_RESEARCH_MAX_QUERY_CHARS: Max query length in characters (default: 512).
- SOLVER_WEB_RESEARCH_FETCH_TIMEOUT: Fetch timeout in seconds (default: 20).
- SOLVER_WEB_RESEARCH_FETCH_MAX_BYTES: Max bytes fetched per URL (default: 200000).
- SOLVER_WEB_RESEARCH_CACHE_TTL_HOURS: Cache TTL in hours (default: 24).


### Jira insights (optional)
If you use the Jira quality report with LLM-backed insights, you can optionally include linked Confluence content in the analysis and tune limits/concurrency via `jira_insights` in `config.json`:

Example `jira_insights` block
```
{
  "jira_insights": {
    "include_confluence": true,
    "max_confluence_pages_per_issue": 3,
    "max_confluence_chars_per_page": 5000,
    "max_parallel_confluence_fetches": 4
  }
}
```


### Topic research
The tool can perform iterative research on a specific topic and requirements, gathering data from Jira, Confluence, LLMs, and optional web search to formulate a comprehensive document in professional British English.

```bash
refiner --topic-research topic_requirements.txt --context https://example.com/context --context local_doc.pdf --output researched_doc.md --llm-provider openai
```

- `--topic-research`: Path or URL to a file containing a topic (first line) and requirements (remaining lines). Supports `.txt`, `.docx`, `.pdf`, `.odf`, `.html`, `.jpg`, `.png`, `.svg`, `.mp3`, and `.mp4`.
- `--context`: (Optional) Additional URLs or file paths to provide context, relevance, boundaries, and focus. Supports the same formats as `--topic-research`. Can be specified multiple times.
- `--max-iterations`: (Optional) Maximum refinement loops (default: 10).
- `--llm-timeout`: (Optional) Timeout in seconds for LLM requests (can also be set via `LLM_TIMEOUT_SECONDS` environment variable).
- Uses existing Jira and Confluence connectivity settings.
- Features an agentic debate loop where LLMs act as both critic and editor to polish the final document.
- Integration with Google Search:
  - Automatically performs real web searches if credentials are provided.
  - Fetches and analyses the full content of relevant search result URLs.

### Agentic roles (multi‑agent configuration)
You can assign different LLM providers/models to distinct roles across the topic researcher and project solver.

Config example (`config.json`):
```json
{
  "agentic_roles": {
    "planner": { "provider": "OpenAIPrimary" },
    "researcher": { "provider": "OpenAIPrimary" },
    "reviewer": { "provider": "GeminiFallback" },
    "critic": { "provider": "GeminiFallback" },
    "editor": { "provider": "GeminiFallback" }
  }
}
```

CLI override (repeatable):
```bash
refiner --agent-role planner=openai:gpt-4o --agent-role reviewer=gemini:gemini-1.5-pro
```

Supported roles: `planner`, `researcher`, `reviewer`, `critic`, `editor`. Roles fall back to the main LLM provider if not configured.


### Logging and progress monitoring
The tool provides detailed status updates and debug logging to monitor progress in real-time and analyse execution afterwards.

```bash
# Standard run with real-time status updates
refiner --topic-research req.txt --output report.md

# Verbose run (includes INFO level logs)
refiner --topic-research req.txt --output report.md --verbose

# Debug run (detailed logs for all API and LLM calls)
refiner --topic-research req.txt --output report.md --debug --log-file my_research.log
```

- `--verbose` (-v): Enables INFO level status updates on the console.
- `--debug` (-d): Enables detailed DEBUG level logging, including truncated LLM payloads and API interactions.
- `--log-file`: Path to the file where all logs (up to DEBUG level) are saved (default: `refiner.log`).
- Status updates prefixed with `[*]` are shown on the console during long-running tasks like topic research.


### Project solver
The tool can scan a local project folder for requirements and use an LLM to produce and apply an action plan. If a requirements document is provided, the scan is skipped and the requirements document is used directly.

```bash
refiner --project /path/to/project --llm-provider openai --output project_solution.json
refiner --project /path/to/project --requirements req.txt --llm-provider openai --project-run
refiner --project /path/to/project --project-output-dir /tmp/solver_workspace --project-run --project-iterations 3
```

Inputs
- `--project`: Path to the project folder to scan and solve.
- `--requirements`: Optional requirements document (txt/md/pdf/docx/etc.). When supplied, project scanning is skipped.
- `--output`: JSON report path. Defaults to `project_solution.json` inside the project root.
- `project_solution.json` includes `requirement_traceability` mapping requirements → plan steps → file changes.
- `--project-run`: Allow the solver to execute `run_command` steps (disabled by default).
- `--project-max-steps`: Cap on the number of steps applied (default: 25).
- `--project-iterations`: Max plan/apply loops (default: 3).
- `--project-output-dir`: Output directory for generated code/virtual environments. Relative paths are resolved inside the project; absolute paths can be outside. Overrides the default solver workspace.

#### Heuristics
- Prioritises README/spec/plan/roadmap/docs content and issue/PR templates.
- Extracts requirement-style statements (must/should/required/etc.).
- Extracts TODO/FIXME/BUG/XXX lines from code/text files as candidate requirements.
- Captures short context excerpts to guide the LLM.
- Processes requirement sources per file to avoid merging across documents unless explicitly requested.
- Skips virtual environments and third-party package directories (even if the venv has a non-standard name).
- Ignores edits inside virtualenvs or site-packages directories when applying plans.
- If requirements do not specify an output location, the solver creates `project_solver_output` and instructs the LLM to place new environments or generated code there.
- If `--project-output-dir` is supplied, that directory is used instead (absolute paths are allowed).
- When a solver workspace is active, new files are written there by default unless the file already exists in the project or requirements explicitly specify otherwise.
- Code changes must be robust, secure, resilient, modular, and scalable, with inline documentation for non-obvious logic.
- When code is created or modified, the solver requires tests (pytest or equivalent) and a verification run_command step before proceeding.
- When creating Python code or installing Python dependencies, the plan should include a venv under the solver workspace or specified output directory unless requirements say otherwise.
- Pip installs should target the venv directly (e.g., `venv/bin/python -m pip ...`) rather than relying on shell activation across iterations.
- The solver rewrites pip commands to use the detected venv, and skips `source venv/bin/activate` commands.
- When the solver writes a `requirements.txt`, it normalises comma-separated lists and pins the latest compatible versions via PyPI or configured indexes, respecting any existing specifiers.
- If solver workspace `requirements.txt` entries are not found in configured indexes, they are treated as hallucinations, removed, and the solver replans.
- If the solver workspace exists (explicit or implied), its files are scanned as their own requirement sources and TODO/FIXME notes there are treated as requirements.
- If a previous solver output JSON exists, the solver resumes from the last incomplete requirement source instead of restarting; completed sources are skipped and prior action logs are reused.
- Failed commands trigger an automatic debug/retry cycle with a recovery plan.
- Optional web research augments planning with external guidance when search credentials are available; results are injected into prompts and recorded in the output.

#### Output
- JSON report containing the derived requirements, all LLM plans per iteration, and an action log (including command output when `--project-run` is enabled).
- The report includes the solver workspace path, whether it was explicitly set, and whether it is inside the project.
- The report includes per-source derived requirements and a requirement source count.
- The report records whether the solver workspace pre-existed or was implied by requirements.
- The report includes `run_config` and `resume` metadata for subsequent runs.
- The report includes `hallucinated_requirements` when dependencies were removed from solver workspace requirements.
- The report includes `completion_summary` with counts of completed/incomplete sources and whether more iterations are needed.
- The completion summary is also printed to stdout after the project solver finishes.
- The report includes `llm_inadequate_counts` and `opencode_fallback_sources` when OpenCode fallback is used.
- The report includes `agentic_workflow` (plan/act/verify/reflect events), `progress_tracker` (path history with dead-end markers and retrace hints), `requirement_traceability`, and `web_research_reports` when enabled.
- The report includes `repo_context_config` when repo context indexing is enabled.
- When `--project-output-dir` is outside the project, code/test files are kept in the project root; the workspace is for venvs and generated artifacts (use absolute paths to target it explicitly).
- Verification guardrails treat `FutureWarning` output as a failure and trigger recovery steps to apply the warning's suggested fix.
- Code that relies on environment variables is expected to validate missing values with clear prompts/errors and tests for the missing-value path.

#### Project solver dependency lookup
- Uses the PyPI JSON API by default; set `PIP_INDEX_URL` and/or `PIP_EXTRA_INDEX_URL` for alternate package indexes.
- Set `DISABLE_PYPI_LOOKUP=1` or `PIP_NO_INDEX=1` to skip version lookups entirely.
- `PYPI_LOOKUP_TIMEOUT` controls the per-request timeout in seconds (default: 8).
- Pinning is best-effort: it respects existing specifiers and the running Python version but does not resolve full dependency graphs.

#### OpenCode fallback
- When code is required and the LLM returns inadequate plans, the solver can query OpenCode via the CLI.
- `OPENCODE_COMMAND_TEMPLATE` is optional; if unset, the fallback uses `opencode run --format json --file <prompt>`. Supported placeholders: `{opencode_bin}`, `{opencode_model_flag}`, `{prompt}`, `{prompt_file}`, `{workspace}`, `{output_path}`.
- Example (CLI run mode): `OPENCODE_COMMAND_TEMPLATE='{opencode_bin} run --format json {opencode_model_flag} --file {prompt_file} "Use the attached file as instructions. Respond ONLY with JSON."'`.
- `OPENCODE_BIN` overrides the opencode executable name/path (default: `opencode`).
- The fallback runs a shell command, so it requires `--project-run` to be enabled.
- `OPENCODE_FALLBACK_THRESHOLD` controls how many inadequate LLM responses trigger the fallback (default: 1).
- `OPENCODE_TIMEOUT` sets the command timeout in seconds (default: 900).
- Auto-install is enabled by default when OpenCode is missing; set `OPENCODE_AUTO_INSTALL=0` to disable. You can override the install command with `OPENCODE_INSTALL_COMMAND`.
- The solver passes through existing `OPENAI_API_KEY`/`GEMINI_API_KEY` values to OpenCode when available.
- If `OPENCODE_COMMAND_TEMPLATE` is not set, the solver defaults to `opencode run --format json --file <prompt>`, and you can set `OPENCODE_MODEL` to choose a provider/model.
- `OPENCODE_MODEL` should be in `provider/model` form (e.g., `openai/gpt-4o`, `anthropic/claude-sonnet-4-5`).
- Configure providers with `opencode auth login` and verify with `opencode auth list` (see `opencode.txt` or opencode.ai/docs).

#### Settings
- `include_confluence` (bool, default: true): When true, if an issue description links to Confluence pages, their text will be fetched and appended as authoritative context for the LLM.
- `max_confluence_pages_per_issue` (int, default: 3): Upper bound on the number of linked Confluence pages to fetch per issue.
- `max_confluence_chars_per_page` (int, default: 5000): Per-page character cap after stripping HTML; longer pages are truncated for cost/latency control.
- `max_parallel_confluence_fetches` (int, default: 4): Small thread-pool size used to fetch linked pages concurrently per issue to reduce wall-clock latency while keeping load bounded.


## Testing
- Run the test suite: `pytest`
- The suite uses lightweight stubs/mocks, so it runs offline without contacting Jira/Confluence.


## Packaging and CLI
- Install in editable mode: pip install -e .
- Console entry point: refiner
- Module entry point: python -m refiner.cli
- run_refiner.py remains for convenience and defers to the same workflow.


## Project structure (high level)
- __init__.py: exposes a minimal API
- run_refiner.py: unified CLI workflow selector (Jira stats, Jira analysis, Confluence analysis, topic research, project solver)
- cli.py: console entry point that delegates to run_refiner.run()
- main.py: orchestrates configuration, discovery, fetching, processing, and outputs for the default Jira statistics workflow
- jira_analysis.py: Jira project/issue quality analysis and HTML report generation
- confluence_analysis.py: Confluence space quality analysis and HTML report generation
- topic_researcher.py: topic research (RAG), document drafting, and references output
- project_solver.py: requirement extraction, planning, and optional code application
- agentic_workflow.py: shared plan/act/verify/reflect workflow engine
- repo_context.py: lightweight repo context indexing for the solver
- web_research.py: shared web search/fetch/summarise utilities
- discover_hierarchy.py: probes Confluence/Jira to refine scope and discover fields
- analyze_issue_transitions.py, get_monthly_worklog_times.py, seconds_to_work_units.py, normalize_name.py, sorting_key.py: helpers
- tests/: pytest suite with offline mocks


## License
This project is licensed under the terms of the LICENSE file in this repository.

## Contributing
See CONTRIBUTING.md for guidelines.
