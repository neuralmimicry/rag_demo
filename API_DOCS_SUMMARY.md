# API Documentation Summary

## What changed

The backend API documentation has been realigned with the routes that `refiner_web.py` actually registers today.

Corrected mismatches:
- removed stale references to `/api/assistant/chat`
- removed stale references to `/api/stt/transcribe`
- removed stale references to `/api/rag/documents`
- replaced `/auth/login` examples with `/api/login`
- corrected the default backend port to `5001`
- documented the current route families for jobs, workspaces, TODO scheduling, collaboration sessions, projects/teams, secrets, refunds, and admin telemetry
- documented the live voice/STT surface: `/api/voice/tokens`, `/api/voice/capture`, `/api/voice/siri`, `/api/voice/alexa`, `/api/voice/google`, `/api/voice/stt`
- added the missing Rust STT `/gesture-plan` endpoint to the docs set

## Files updated

- `README.md`
- `API_DOCS_README.md`
- `API_DOCS_SUMMARY.md`
- `openapi_refiner.yaml`
- `api_docs.py`
- `stt_rust/README.md`

## Current backend documentation entry points

When the backend is started with `python refiner_web.py`:
- Swagger UI: `GET /api/docs`
- OpenAPI JSON: `GET /api/docs/openapi.json`
- OpenAPI YAML: `GET /api/docs/openapi.yaml`
- Public docs health helper: `GET /health`
- Operational health: `GET /api/health`
- Version: `GET /api/version`

## Route groups now covered

- auth, session, profile, SSO, and OIDC exchange
- assistant, requirements drafting, form-fill, RAG+MCP, and playground planning
- voice capture, Siri/Alexa/Google adapters, voice tokens, and STT
- jobs, token estimation, workspaces, editor APIs, logs, actions, transfer, and archive
- TODO inbox, routed TODOs, schedules, and generic subtasks
- projects, teams, access tree, collaboration sessions, and SSE streams
- requirements import/export, RAG indexing/query, and MCP server management
- tokens, refunds, secrets, GitHub tree inspection, and admin telemetry

## Notes

- `openapi_refiner.yaml` now tracks the current backend surface instead of the older assistant/chat and document-centric API.
- `api_docs.py` documentation comments now match reality: the Swagger shell is local, but the Swagger UI assets are loaded from jsDelivr, and the default backend URL is `http://127.0.0.1:5001`.
