# Refiner — Wiki Home

**Refiner** is the LLM workflow engine and public API gateway at the heart of the NeuralMimicry platform. It orchestrates agentic delivery pipelines, Jira/Confluence analysis, RAG, MCP integrations, project solver, and multi-provider AI orchestration — all from a single deployable Python service.

> ☕ [Support NeuralMimicry on Crowdfunder](https://www.crowdfunder.co.uk/p/qr/aWggxwPW?utm_campaign=sharemodal&utm_medium=referral&utm_source=shortlink) — we are an independent open-source project and rely on community backing.

---

## Quick navigation

| Page | Description |
|---|---|
| [Getting Started](Getting-Started) | Install, configure, and run Refiner locally |
| [API Reference](API-Reference) | Full JSON API surface (auth, jobs, assistant, voice, billing) |
| [Architecture](Architecture) | Package layout, request path, and service delegation |
| [Configuration](Configuration) | `config.json`, environment variables, and deployment options |
| [Workflows](Workflows) | Jira stats, analysis, Confluence, topic research, delivery, project solver |
| [AI Orchestration](AI-Orchestration) | Multi-provider fan-out, scoring, routing profiles, AARNN bridge |
| [Deployment](Deployment) | Container build, Kubernetes, Continuum integration |
| [Contributing](Contributing) | How to raise issues, submit PRs, and run the test suite |

---

## What Refiner does

Refiner is the single public API origin for the NeuralMimicry platform. All browser and API traffic arrives here; Refiner then delegates to specialist services:

- **Auth / identity** → [Customers](https://github.com/neuralmimicry/customers) (when `REFINER_CUSTOMERS_API_BASE` is set)
- **Token accounting** → [Billing](https://github.com/neuralmimicry/billing) (when `REFINER_BILLING_API_BASE` is set)
- **Speech-to-text** → [nmstt](https://github.com/neuralmimicry/nmstt) (when `REFINER_STT_BACKEND=server`)
- **LLM completions** → [Gail](https://github.com/neuralmimicry/gail) (when `REFINER_GAIL_ENABLED=1`)
- **Token ledger** → [nmchain](https://github.com/neuralmimicry/nmchain) (when `REFINER_CHAIN_API_BASE` is set)

## Getting started (local)

```bash
cd rag_demo/
pip install -r requirements.txt
pip install -e .                        # installs the 'refiner' CLI

python -m refiner.refiner_web           # API + web UI on http://127.0.0.1:5001
```

Key local URLs after start:
- **Swagger UI**: http://127.0.0.1:5001/api/docs
- **Health**: http://127.0.0.1:5001/api/health
- **Version**: http://127.0.0.1:5001/api/version

## Running tests

```bash
pytest                                  # full offline suite
pytest tests/test_foo.py::test_bar      # single test
pytest -k "keyword"                     # filter by name
```

## Package layout

```
refiner/runtime/       Flask runtime, API wiring, config, env helpers
refiner/workflows/     Jira, Confluence, research, delivery, inbox, solver
refiner/integrations/  Atlassian, MCP, platform, search, STT, VCS adapters
refiner/ai/            Provider adapters, orchestration, retrieval, model inventory
```

## Get involved

- 🐛 [Report a bug or request a feature](https://github.com/neuralmimicry/rag_demo/issues)
- 💬 [Join the discussion](https://github.com/neuralmimicry/rag_demo/discussions)
- 🌐 [neuralmimicry.ai](https://neuralmimicry.ai) — commercial site and architecture sessions
- 📧 Direct support from the founder: [info@neuralmimicry.ai](mailto:info@neuralmimicry.ai) · **£1,000/day + VAT**
