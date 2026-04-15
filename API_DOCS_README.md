# Refiner API Documentation

Refiner exposes three HTTP surfaces:
- `refiner_web.py`: the main backend and Control Room UI (`127.0.0.1:5001` by default)
- `frontend_server.py`: an optional frontend-only shell (`0.0.0.0:8080` by default)
- `nmstt`: the standalone speech-to-text and gesture-planning service (`127.0.0.1:7079` by default)

This document tracks the backend routes that are actually registered today. The matching backend OpenAPI spec lives in `openapi_refiner.yaml`, and the speech-service spec lives in `../nmstt/openapi_stt.yaml`.

## Backend docs endpoints

Starting the backend with `python refiner_web.py` registers the documentation helper routes from `api_docs.py`.

Public routes:
- `GET /api/docs` - Swagger UI shell for the backend OpenAPI spec
- `GET /api/docs/openapi.yaml` - backend OpenAPI YAML
- `GET /api/docs/openapi.json` - backend OpenAPI JSON
- `GET /health` - lightweight public health helper used by the docs module

Backend routes that are always part of the Flask app:
- `GET /api/health` - operational health summary used by the container healthcheck
- `GET /api/version` - public build/version payload
- `GET /metrics` - Prometheus metrics when enabled

Notes:
- The Swagger UI HTML is served locally, but the Swagger assets are loaded from jsDelivr at runtime.
- If you embed `refiner_web.app` in another runner instead of launching `python refiner_web.py`, call `api_docs.add_api_documentation_support(app, ...)` yourself if you want `/api/docs` and `/health`.

## Authentication model

Primary backend auth is session-cookie based:
- `POST /api/setup` creates the first local admin account when no users exist yet.
- `POST /api/login` starts a session and also returns a short-lived SSO token.
- `POST /api/logout` clears the current session.
- `GET /api/session` reports whether the caller is authenticated.
- `GET/POST /api/profile` reads or updates the current user's email plus profile-backed settings defaults for LLM, assistant, solver, and replay UI behaviour.

Optional auth paths:
- `POST /api/sso/issue` issues a one-time SSO token for the current session.
- `GET /sso` exchanges that token into a first-party session.
- `POST /api/oidc/exchange` supports OIDC code or `id_token` exchange when OIDC is enabled.

Voice and STT routes support additional auth patterns:
- `GET/POST /api/voice/tokens` issues or lists per-user voice tokens.
- `POST /api/voice/stt` accepts a logged-in session, or an STT token when `REFINER_STT_TOKEN` is configured, or public access when `REFINER_STT_PUBLIC=1`.
- Voice capture routes such as `/api/voice/capture`, `/api/voice/siri`, `/api/voice/alexa`, and `/api/voice/google` resolve the caller from voice tokens or provider-specific user mappings.

## Current route inventory

### System and admin
- `GET /api/health` - backend health summary with workers, queue depth, scheduler, SSO store state, and STT learning status
- `GET /api/version` - public version payload
- `GET /api/capabilities` - current capability inventory for authenticated users
- `GET /api/admin/stats` - admin-only usage and worker summary, including recent `llm_request_telemetry` rollups when the shared Postgres control-plane store is enabled
- `GET /api/workers/telemetry` - worker/autoscaler telemetry for the Control Room
- `GET /api/audit` - admin-only audit trail

### Assistant and planning
- `POST /api/assistant/requirements` - requirements chat/drafting assistant; supports `mode`, `prompt`, `requirements_text`, `messages`, provider/model overrides, and gesture metadata
- `POST /api/assistant/form-fill` - returns JSON suggestions for structured form fields
- `POST /api/assistant/rag-mcp` - combines LLM prompting with optional RAG matches and an optional MCP tool call
- `POST /api/playground/plan` - returns a child-friendly build plan plus a ready-to-submit `job_payload`

### Voice and speech
- `GET/POST /api/voice/tokens` - issue/list voice tokens
- `DELETE /api/voice/tokens/<token_id>` - revoke a voice token
- `GET/POST /api/voice/capture` - convert a short text utterance into a deferred TODO item
- `GET/POST /api/voice/siri` - Siri/Shortcuts capture path
- `POST /api/voice/alexa` - Alexa capture path
- `POST /api/voice/google` - Google Assistant/Dialogflow capture path
- `POST /api/voice/stt` - transcribe audio and optionally return BSL/gesture/avatar motion data

### Jobs, workspaces, and Control Room
- `GET/POST /api/jobs` - list visible jobs or submit a new one
- `POST /api/jobs/estimate` - estimate token cost before submission
- `GET/DELETE /api/jobs/<job_id>` - inspect or delete a job
- `GET/POST /api/jobs/<job_id>/workspace` - inspect, create, or refresh a workspace
- `POST /api/jobs/<job_id>/workspace/open` - open a workspace target in an IDE/browser integration
- `GET /api/jobs/<job_id>/tasks` - background workspace action tasks
- `GET /api/jobs/<job_id>/tasks/<task_id>` - task detail
- `POST /api/jobs/<job_id>/tasks/<task_id>/cancel` - cancel a task
- `GET /api/jobs/<job_id>/editor/roots` - file browser roots for the editor
- `GET /api/jobs/<job_id>/editor/list` - directory listing for the editor
- `GET/PUT /api/jobs/<job_id>/editor/file` - read or update a file in the job workspace
- `POST /api/jobs/<job_id>/editor/ops` - higher-level editor operations
- `GET /api/jobs/<job_id>/requirements/progress` - requirements progress snapshot
- `GET /api/jobs/<job_id>/requirements/summary` - summarized requirements/register data
- `GET /api/jobs/<job_id>/logs` - recent logs
- `GET /api/jobs/<job_id>/logs/stream` - server-sent event log stream
- `POST /api/jobs/<job_id>/actions` - queue job-scoped actions
- `POST /api/jobs/<job_id>/transfer` - transfer a job to another queue/owner context
- `POST /api/jobs/<job_id>/archive` - archive a job
- `POST /api/jobs/bulk-delete` - delete queued/archive jobs in bulk

Typical `POST /api/jobs` payload fields:
- `workflow`: one of the UI-driven workflows such as `project_solver`, `delivery_pipeline`, `topic_research`, `jira_quality`, or `confluence_analysis`
- `project_root`, `requirements_text`, `requirements_path`, `project_run`
- `topic_source`, `topic_output`
- `projects`, `jql`, `space`, `use_rovo`
- `delivery_config`, `delivery_run`, `delivery_allow_unfinished`
- `llm_provider`, `llm_model`, `llm_temperature`, `llm_max_tokens`
- `project_id`, `team_id`, `token_scope`, `job_secrets`
- GitHub-oriented project solver/delivery fields such as `repo_url`, `repo_branch`, `work_branch`, `repo_subdir`, `requirements_relpath`, `fork_org`, and commit author metadata

### TODO inbox, schedules, and subtasks
- `GET/POST /api/todos` - list or create TODO items
- `GET /api/todos/next` - peek/claim the next TODO item, with optional idle gating
- `PATCH/DELETE /api/todos/<todo_id>` - update or delete an item
- `POST /api/todos/<todo_id>/route` - ask Refiner to suggest the best workflow for that TODO
- `GET/POST /api/todos/<todo_id>/schedule` - inspect or create deferred execution schedules
- `GET /api/schedules` - list schedules
- `GET /api/schedules/<schedule_id>` - schedule detail
- `POST /api/schedules/<schedule_id>/cancel` - cancel a schedule
- `GET/POST /api/subtasks` - list or create generic background subtasks
- `GET /api/subtasks/<task_id>` - subtask detail
- `POST /api/subtasks/<task_id>/cancel` - cancel a subtask

Routed TODOs can currently submit jobs or invoke assistant workflows such as `/api/assistant/requirements` and `/api/playground/plan`.

### Access control and collaboration
- `GET/POST /api/projects` - list/create projects
- `PATCH/DELETE /api/projects/<project_id>` - update/delete projects
- `GET/POST /api/teams` - list/create teams
- `PATCH/DELETE /api/teams/<team_id>` - update/delete teams
- `GET/POST /api/teams/<team_id>/tokens` - inspect or administer team token balances
- `GET /api/access/tree` - current user's project/team access tree
- `POST /api/sessions` - create or join a collaboration session for a job
- `GET /api/sessions/<session_id>` - session detail
- `POST /api/sessions/<session_id>/leave` - leave a session
- `GET /api/sessions/<session_id>/stream` - presence and job-status SSE stream
- `GET /api/sessions/<session_id>/history` - persisted room history
- `GET /api/sessions/history` - admin-only room history index

### Requirements import/export, RAG, and MCP
- `POST /api/requirements/import` - import a requirements register from `.csv`, `.xls`, `.xlsx`, or `.ods`
- `POST /api/requirements/export` - export a normalized register in the same formats
- `GET /api/rag/indexes` - list the caller's RAG indexes
- `POST /api/rag/index` - create a RAG index from `sources` or allowed local `paths`
- `DELETE /api/rag/index/<name>` - delete an index
- `POST /api/rag/query` - search an index and return matches plus a rendered context block
- `GET/POST /api/mcp/servers` - list/register MCP servers (admin-only)
- `DELETE /api/mcp/servers/<name>` - delete an MCP server (admin-only)
- `GET /api/mcp/servers/<name>/tools` - list tools
- `POST /api/mcp/servers/<name>/call` - invoke a tool
- `GET /api/mcp/servers/<name>/resources` - list resources
- `POST /api/mcp/servers/<name>/resource` - fetch one resource by URI

### Tokens, refunds, secrets, and GitHub helpers
- `GET/POST /api/tokens` - inspect or mutate a user's token balance (`review`, `add`, `cashout`, `grant`, `sync`)
- `GET /api/tokens/ledger` - ledger history
- `POST /api/jobs/<job_id>/refunds` - submit a refund request with screenshots
- `GET /api/refunds` - admin refund queue
- `POST /api/refunds/<job_id>/<request_id>/screen` - LLM-assisted refund screening
- `POST /api/refunds/<job_id>/<request_id>/decision` - admin refund decision
- `GET /api/refunds/<job_id>/<request_id>/file/<filename>` - refund screenshot download
- `GET/POST /api/secrets` - list or store user-scoped secrets
- `DELETE /api/secrets/<name>` - delete a secret
- `POST /api/github/tree` - fetch a repository tree through the GitHub API using the configured GitHub token when available

## Example requests

### First-user bootstrap

```bash
curl -X POST http://127.0.0.1:5001/api/setup \
  -H 'Content-Type: application/json' \
  -d '{
    "username": "admin",
    "password": "replace-me",
    "confirm": "replace-me",
    "email": "admin@example.com"
  }'
```

### Log in and keep the session cookie

```bash
curl -X POST http://127.0.0.1:5001/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"replace-me"}' \
  -c cookies.txt
```

### Ask the requirements assistant

```bash
curl -X POST http://127.0.0.1:5001/api/assistant/requirements \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{
    "mode": "ask",
    "prompt": "Turn these notes into testable requirements",
    "requirements_text": "Need a dashboard, a login, and CSV export support.",
    "provider": "openai"
  }'
```

### Generate a playground build plan

```bash
curl -X POST http://127.0.0.1:5001/api/playground/plan \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{"prompt":"Build a small revision quiz app for pupils."}'
```

The response includes `summary`, `steps`, `requirements_text`, and a ready-to-submit `job_payload` for `POST /api/jobs`.

### Transcribe browser audio with gesture metadata

```bash
curl -X POST http://127.0.0.1:5001/api/voice/stt \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_STT_TOKEN' \
  -d '{
    "audio_base64": "BASE64_AUDIO_HERE",
    "lang": "en-GB",
    "motionStyle": "BSL (British Sign Language)",
    "avatarMode": "office",
    "collaborationMode": true
  }'
```

Common STT request fields:
- `audio_base64` or other supported audio inputs accepted by `_extract_audio_bytes`
- `lang`
- `motionStyle` / `gesture_mode`
- `avatarMode` / `avatar_mode`
- `collaborationMode` / `collaboration_mode`
- optional context fields such as `prompt`, `message`, or `query`

### Index ad-hoc text for RAG

```bash
curl -X POST http://127.0.0.1:5001/api/rag/index \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{
    "name": "notes",
    "sources": [
      {
        "id": "note-001",
        "title": "meeting-notes",
        "text": "Refiner now supports TODO routing, job workspaces, and MCP integrations."
      }
    ]
  }'
```

```bash
curl -X POST http://127.0.0.1:5001/api/rag/query \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{"name":"notes","query":"What can Refiner route from the inbox?","top_k":5}'
```

### Submit a project-solver job directly

```bash
curl -X POST http://127.0.0.1:5001/api/jobs \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{
    "workflow": "project_solver",
    "project_name": "Docs Sync Demo",
    "requirements_text": "- REQ-001: Build a tiny demo\n- REQ-002: Add tests\n- REQ-003: Keep the UI responsive",
    "project_run": true,
    "llm_provider": "openai",
    "token_scope": "personal"
  }'
```

### Requirements register import/export

Import a spreadsheet:

```bash
curl -X POST http://127.0.0.1:5001/api/requirements/import \
  -b cookies.txt \
  -F 'file=@requirements.xlsx'
```

Export normalized rows back to CSV:

```bash
curl -X POST http://127.0.0.1:5001/api/requirements/export \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{"format":"csv","items":[{"id":"REQ-001","title":"Ship docs"}]}' \
  -o requirements_register.csv
```

## nmstt quick reference

The standalone speech service is documented separately in `../nmstt/README.md` and `../nmstt/openapi_stt.yaml`.

Current `nmstt` routes:
- `GET /health`
- `POST /transcribe`
- `POST /gesture-plan`

The backend uses that service when `REFINER_STT_SERVER_URL` is configured. `REFINER_STT_BACKEND=server` remains supported, but Refiner now infers server mode automatically when a speech-service URL is present.

## Verification

Useful checks after updating backend routes or docs:

```bash
python run_refiner.py --help
python refiner_web.py
curl http://127.0.0.1:5001/api/docs/openapi.json
curl http://127.0.0.1:5001/api/health
```

Relevant tests:
- `tests/test_api_docs.py`
- `tests/test_route_registration.py`
- `tests/test_stt_server_resilience.py`
- `tests/test_assistant_bsl_integration.py`

## Scope note

`openapi_refiner.yaml` now describes the current backend route families and representative request/response shapes. The Markdown inventory above is the authoritative quick reference for the full route surface exposed by `refiner_routes/` and `refiner_web.py`.
