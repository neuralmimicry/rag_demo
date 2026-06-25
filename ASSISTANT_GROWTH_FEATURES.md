# Assistant Growth Features (Aone-Aligned)

This document summarises the assistant upgrades added to Refiner for faster go-live, broader channel support, and stronger operational analytics.

## Implemented Features

1. Omni-channel context support
- Assistant routes now accept `channel` and/or `channel_context`.
- Supported channel labels now include `web`, `mobile`, `api`, `whatsapp`, `telegram`, `messenger`, `instagram`, `linkedin`, `sms`, `siri`, `alexa`, `google_home`, and `google_assistant`.
- Channel metadata is captured in trace/conversation records and used to shape prompt guidance.

2. Persona and tone customisation
- Assistant routes now support `assistant_profile` presets (`requirements`, `marketing`, `support`, `sales`, `onboarding`, `technical`).
- Optional `assistant_profile_config` allows controlled overrides (tone, style, goal, constraints).
- Persona guidance is injected into requirements and RAG+MCP prompt generation.

3. Structured data ingestion for RAG
- RAG source coercion now accepts:
  - `documents` (inline strings or document objects),
  - `records`,
  - `crm_records`,
  - `crm_export.records`.
- Structured records are converted into RAG documents with deterministic metadata.
- URL/path source de-duplication is now applied during source coercion.
- Record conversion uses a thread pool for scalable ingestion on larger structured payloads.

4. Launch-in-minutes onboarding endpoint
- New route: `POST /api/assistant/onboarding/plan`.
- Returns a four-step launch plan, readiness state, and starter payload templates for:
  - `/api/rag/index`,
  - `/api/assistant/requirements`,
  - `/api/assistant/rag-mcp`.

5. Assistant analytics endpoint
- New route: `GET /api/admin/assistant/analytics`.
- Aggregates assistant trace KPIs for dashboard use:
  - success rate,
  - cache-hit rate,
  - handoff rate,
  - conversion rate,
  - average duration,
  - route/channel/profile/sentiment/provider/error breakdowns.
- Uses the Postgres trace store aggregation path when available, with a safe fallback in handlers.
- Admin Console now includes a lightweight Assistant Analytics panel on `/admin` with:
  - owner/route/channel/profile/hour/limit filters,
  - KPI cards,
  - route/channel/profile/sentiment/provider/error breakdown lists.

6. Engagement markers in assistant responses
- `assistant_requirements` and `assistant_rag_mcp` now enrich responses with:
  - `assistant_profile`,
  - `channel`,
  - `sentiment`,
  - `handoff_requested`,
  - `conversion_completed`.
- Trace response metadata now carries the same markers for analytics.

7. Aaron omni-channel interface
- New route: `POST /api/assistant/aaron/respond`.
- Provides a single interaction envelope for web, mobile, messaging, and voice-assistant channels.
- Supports wake-name handling (`Aaron`) and channel/workflow routing into existing assistant flows.
- Voice capture routes now recognise `Aaron` wake-prefixed requests and execute assistant responses directly for:
  - `/api/voice/siri`,
  - `/api/voice/alexa`,
  - `/api/voice/google`,
  while preserving existing todo capture behaviour for non-wake utterances.
- Added inbound webhook adapters for chat channels:
  - `POST /api/assistant/channels/telegram/webhook`
  - `GET|POST /api/assistant/channels/whatsapp/webhook`
- WhatsApp verification is handled on `GET` using `hub.mode`, `hub.verify_token`, `hub.challenge`.
- Webhook adapters return provider-ready response payload wrappers (`telegram_response`, `whatsapp_response`) for bridge workers.

## New/Updated API Routes

- `POST /api/assistant/onboarding/plan`
- `POST /api/assistant/aaron/respond`
- `POST /api/assistant/channels/telegram/webhook`
- `GET|POST /api/assistant/channels/whatsapp/webhook`
- `GET /api/admin/assistant/analytics`
- Existing assistant routes now accept channel/persona fields:
  - `POST /api/assistant/requirements`
  - `POST /api/assistant/rag-mcp`
  - `POST /api/assistant/form-fill`
  - `POST /api/playground/plan`
  - `POST /api/execution/plan`

## Ansible Deployment Notes

For Continuum/Ansible deployments (`${SWARMHPC_ROOT}/swarmhpc/ansible`):
- the Refiner tenant role now exposes explicit defaults:
  - `continuum_tenant_refiner_assistant_default_channel` (default `web`)
  - `continuum_tenant_refiner_assistant_default_profile` (default `requirements`)
- these are injected into the container as:
  - `REFINER_ASSISTANT_DEFAULT_CHANNEL`
  - `REFINER_ASSISTANT_DEFAULT_PROFILE`
- runtime routes now use these values as fallback defaults whenever request payloads omit channel/profile fields.
- no schema migration playbook changes are required because assistant trace/conversation tables are created/maintained by Refiner central-store schema initialisation.

## Validation Scope

Added or updated tests cover:
- assistant experience helpers (channel/persona/engagement markers),
- assistant onboarding plan behaviour,
- assistant route registration (new onboarding + analytics routes),
- assistant trace analytics aggregation,
- admin analytics handler response path,
- structured records/documents RAG source coercion and document construction.
