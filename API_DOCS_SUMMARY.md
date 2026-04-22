# API Documentation Summary

## What changed

The backend API documentation has been realigned with the routes that the `refiner.refiner_web` backend module actually registers today.

Corrected mismatches:
- removed stale references to `/api/assistant/chat`
- removed stale references to `/api/stt/transcribe`
- removed stale references to `/api/rag/documents`
- replaced `/auth/login` examples with `/api/login`
- corrected the default backend port to `5001`
- documented the current route families for jobs, workspaces, TODO scheduling, collaboration sessions, projects/teams, secrets, refunds, and admin telemetry
- documented the live voice/STT surface: `/api/voice/tokens`, `/api/voice/capture`, `/api/voice/siri`, `/api/voice/alexa`, `/api/voice/google`, `/api/voice/stt`
- documented the `POST /api/assistant/rag-mcp` extension for explicit Jira/Confluence write actions alongside RAG and MCP
- aligned the speech-service references with the extracted `nmstt` project, including the `/gesture-plan` endpoint

## Files updated

- `README.md`
- `API_DOCS_README.md`
- `API_DOCS_SUMMARY.md`
- `openapi_refiner.yaml`
- `refiner/api_docs.py`
- `../nmstt/README.md`

## Current backend documentation entry points

When the backend is started with `python -m refiner.refiner_web`:
- Swagger UI: `GET /api/docs`
- OpenAPI JSON: `GET /api/docs/openapi.json`
- OpenAPI YAML: `GET /api/docs/openapi.yaml`
- Public docs health helper: `GET /health`
- Operational health: `GET /api/health`
- Version: `GET /api/version`

## Route groups now covered

- auth, session, profile, SSO, and OIDC exchange
- assistant, requirements drafting, form-fill, RAG+MCP, Jira/Confluence write actions, and playground planning
- voice capture, Siri/Alexa/Google adapters, voice tokens, and `nmstt`-backed STT
- jobs, token estimation, workspaces, editor APIs, logs, actions, transfer, and archive
- TODO inbox, routed TODOs, schedules, and generic subtasks
- projects, teams, access tree, collaboration sessions, and SSE streams
- requirements import/export, RAG indexing/query, and MCP server management
- tokens, refunds, secrets, GitHub tree inspection, and admin telemetry

## Notes

- `openapi_refiner.yaml` now tracks the current backend surface instead of the older assistant/chat and document-centric API.
- `refiner/api_docs.py` documentation comments now match reality: the Swagger shell is local, but the Swagger UI assets are loaded from jsDelivr, and the default backend URL is `http://127.0.0.1:5001`.
- Refiner remains the public compatibility gateway for `/api/voice/stt`, while the actual speech workload now lives in the standalone `nmstt` repository.
