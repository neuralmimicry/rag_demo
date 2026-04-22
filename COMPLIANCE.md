## Compliance posture (ISO 27001 / SOC 2)

This project provides security controls that support ISO 27001 and SOC 2 alignment. It does not, by itself, confer certification. Compliance depends on your operating environment, policies, and evidence collection.

### Scope
- Web control plane: `refiner/refiner_web.py` (auth, secrets, jobs, RAG/MCP).
- CLI workflows: `refiner/run_refiner.py`, `refiner/project_solver.py`, `refiner/topic_researcher.py`.
- Data at rest: job metadata, logs, secrets store, RAG indexes.

### Implemented control highlights
- Access control: user auth with role checks, secure session cookies, admin-only actions.
- Optional SSO: OIDC login mode with issuer discovery, JWT validation, and optional SPA exchange endpoint.
- OIDC exchange controls: redirect URI allowlist for SPA code exchange.
- Credential protection: secret store with optional encryption, masked payload persistence, log redaction.
- Audit logging: authentication, secret updates, and token ledger events logged to `audit.log`.
- Brute-force protection: login throttling with configurable limits.
- Network security: HTTPS enforcement options, origin checks for state-changing requests.
- Data retention: optional job retention cleanup.
- SSRF prevention: URL allow/deny policy for external fetches.
- Secure storage permissions: 0700 directories, 0600 files for sensitive data.

### Configuration checklist
- Set `REFINER_SECRET_KEY` and `REFINER_REQUIRE_SECRET_KEY=1` for stable, secure sessions.
- Set `REFINER_SECRET_STORE_KEY` and `REFINER_SECRET_STORE_REQUIRE_ENCRYPTION=1` to encrypt secrets at rest.
- Enable HTTPS enforcement: `REFINER_ENFORCE_HTTPS=1` and `REFINER_SECURE_COOKIES=1`.
- Keep CSRF checks enabled: `REFINER_CSRF_ORIGIN_CHECK=1`.
- Configure data retention: `REFINER_JOB_RETENTION_DAYS=30` (or policy-specific).
- Configure login throttling: `REFINER_LOGIN_MAX_ATTEMPTS=10` and `REFINER_LOGIN_WINDOW_SEC=300`.
- Configure OIDC SSO where required: set `REFINER_OIDC_ENABLED=1` and `REFINER_AUTH_MODE=oidc`.
- For SPA OIDC flows, enable `REFINER_OIDC_EXCHANGE_ENABLED=1`; `/api/oidc/exchange` supports PKCE code exchange to keep tokens server-side. Allow the frontend origin in `REFINER_CORS_ORIGINS` and set `REFINER_OIDC_ALLOWED_REDIRECT_URIS` to the frontend callback URL.
- Use `REFINER_URL_ALLOWLIST` to restrict external fetch domains.
- Store audit logs securely and ship to your SIEM.
 - Compatibility aliases: `NM_AUTH_MODE` and `NM_OIDC_*` are accepted to align SSO configuration across NeuralMimicry services.

### ISO 27001 (2022) alignment notes
- Access control, identity, and authentication: user store, role checks, session security.
- Cryptography: optional secret-store encryption and TLS enforcement.
- Logging and monitoring: audit log and operational logs.
- Secure development: input validation, SSRF protections, secret redaction.
- Asset management and retention: job retention cleanup, secure file permissions.

### SOC 2 trust services alignment notes
- Security: auth, session, login throttling, audit logs, SSRF protections.
- Availability: job queue monitoring, metrics, error handling.
- Confidentiality: secret encryption, payload redaction, secure file permissions.
- Processing integrity: verification steps, explicit requirements tracking.
- Privacy: redact sensitive inputs; control data retention and access.

### Evidence collection suggestions
- Retain audit logs and configuration snapshots.
- Maintain change logs (`CHANGELOG` and git history).
- Capture access reviews and operational procedures outside the codebase.
