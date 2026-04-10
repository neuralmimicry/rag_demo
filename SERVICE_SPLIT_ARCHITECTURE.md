# NeuralMimicry Service Split Architecture

This document defines the service boundaries introduced when Refiner was decomposed into distinct operational projects.

## Canonical external entry points

Public commercial site:

- `https://neuralmimicry.ai`

Public API host consumed by the frontend:

- `https://api.neuralmimicry.ai`

Internal edge routing path:

1. Internet traffic enters through `vega.neuralmimicry.ai`
2. Requests are routed onward to `spirit.neuralmimicry.ai`
3. Spirit forwards requests into the Continuum tenant workloads and service mesh / Kubernetes services

The frontend should continue to treat `https://api.neuralmimicry.ai` as the single backend origin.
The service split is deliberately hidden behind that stable API host.

## Repositories and responsibilities

### Refiner

Repository:

- `/home/pbisaacs/Developer/neuralmimicry/rag_demo`

Owns:

- job orchestration and execution
- assistant, RAG, MCP, planning, and workspace APIs
- public API host behavior and compatibility routes
- voice capture endpoints and STT request orchestration
- proxying auth/profile/voice-token routes to Customers
- proxying user token routes to Billing

Persistence defaults:

- job/workspace data stored on an NFS-backed PVC using the `continuum-shared` storage class
- no default dependency on embedded local-auth/token file state once Customers and Billing are configured

### Customers

Repository:

- `/home/pbisaacs/Developer/neuralmimicry/customers`

Owns:

- user registration
- password verification
- login/logout/session cookies
- SSO and OIDC exchange
- profile management
- voice token lifecycle

Persistence defaults:

- relational identity/session/token records stored in the Continuum Postgres tenant service
- fallback file state stored on an NFS-backed PVC using the `continuum-shared` storage class

### Billing

Repository:

- `/home/pbisaacs/Developer/neuralmimicry/billing`

Owns:

- personal and team account snapshots
- token ledger reads
- payment capture
- token mutation events
- internal accounting APIs consumed by Refiner

Persistence defaults:

- stateless application pods with no default local durable store
- immutable financial/accounting ledger persisted in nmchain
- identity-linked relational records persisted in Customers on the Continuum Postgres tenant service

### nmstt

Repository:

- `/home/pbisaacs/Developer/neuralmimicry/nmstt`

Owns:

- speech-to-text inference
- gesture planning
- audio preprocessing and response shaping for Refiner-compatible voice flows

Persistence defaults:

- Whisper model assets stored on an NFS-backed PVC using the `continuum-shared` storage class
- model PVC seeded from the container image on first deployment when empty

### nmchain

Repository:

- `/home/pbisaacs/Developer/neuralmimicry/nmchain`

Owns:

- immutable audit/event ledger
- identity, login, payment, and token event storage
- auditable account snapshots and ledger history

Persistence defaults:

- blockchain data stored on an NFS-backed PVC using the `continuum-shared` storage class

## Runtime interaction model

### Browser login

1. Browser calls `https://api.neuralmimicry.ai/api/login`
2. Refiner proxies the route to Customers
3. Customers authenticates the user and issues the session cookie
4. Refiner resolves future sessions against Customers `/api/session`
5. Customers optionally writes login/identity events to nmchain

### Token review and ledger history

1. Browser calls `https://api.neuralmimicry.ai/api/tokens`
2. Refiner proxies the request to Billing
3. Billing resolves the browser session through Customers `/api/session`
4. Billing reads the account state from nmchain and returns the normalized payload expected by the existing UI

### Billing dashboards

1. Browser calls `https://api.neuralmimicry.ai/billing` or `https://api.neuralmimicry.ai/billing/admin`
2. Refiner proxies the HTML, asset, and JSON dashboard routes to Billing
3. Billing resolves the browser session through Customers `/api/session`
4. Billing assembles customer balance views from nmchain account snapshots and ledger entries
5. Billing assembles admin portfolio views from nmchain chain status, recent block history, and observed account snapshots
6. The browser still sees only the public Refiner/API origin even though Billing renders the dashboard surface

### Refiner internal accounting

1. Refiner estimates work, reserves tokens, debits on execution, and releases unused reservations
2. For those non-browser operations Refiner calls Billing internal endpoints with an app token
3. Billing writes the mutation to nmchain
4. nmchain keeps the authoritative ledger trail for later audit

### Voice capture and STT

1. Browser or device sends audio to Refiner voice routes
2. Refiner validates the session or voice token through Customers
3. Refiner forwards the STT workload to nmstt
4. nmstt returns transcript and gesture payloads
5. Refiner records usage and any related billing effects through Billing and nmchain

## Resilience and compatibility rules

- The public frontend must not need a second backend hostname because of the split
- Refiner remains the compatibility gateway for the existing website/API contract
- Billing dashboard HTML, assets, and JSON must remain proxyable through Refiner so the commercial site never needs a direct Billing origin
- Internal service-to-service calls use app tokens where mutation or privileged lookup is involved
- Customers remains the truth for identity
- Billing remains the truth for balance mutation orchestration
- nmchain remains the audit ledger of record
- nmstt remains isolated from browser-auth concerns and only serves STT/gesture work

## Default internal service URLs

- Customers: `http://customers.customers.svc.cluster.local:5010`
- Billing: `http://billing.billing.svc.cluster.local:5020`
- nmstt: `http://nmstt.nmstt.svc.cluster.local:7079`
- nmchain: `http://nmchain.nmchain.svc.cluster.local:9080`
- Postgres: `postgres.postgres.svc.cluster.local:5432`

## Continuum deployment entry points

- Refiner: `/home/pbisaacs/Developer/swarmhpc/swarmhpc/ansible/continuum_tenant_refiner_site.yml`
- Customers: `/home/pbisaacs/Developer/swarmhpc/swarmhpc/ansible/continuum_tenant_customers_site.yml`
- Billing: `/home/pbisaacs/Developer/swarmhpc/swarmhpc/ansible/continuum_tenant_billing_site.yml`
- nmstt: `/home/pbisaacs/Developer/swarmhpc/swarmhpc/ansible/continuum_tenant_nmstt_site.yml`
- Combined nmchain-backed stack: `/home/pbisaacs/Developer/swarmhpc/swarmhpc/ansible/continuum_tenant_nmchain_site.yml`
