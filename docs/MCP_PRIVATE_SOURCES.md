# Approved private MCP sources

Refiner denies private-network URLs by default. Operators can approve exact MCP
endpoint URLs with the comma-separated `REFINER_MCP_PRIVATE_URLS` setting, for
example `http://192.168.1.2:18180/mcp` for the temporary read-only platform
acceptance adapter. This setting does not grant subnet, host-wide, different-port
or different-path access and does not change research/download URL policy.
Existing global URL host allowlists and blocklists still apply. Embedded URL
credentials and fragments are rejected.

Both registration and every execution resolve the current policy. Removing an
endpoint from this setting revokes an existing adapter without rewriting its
owner-scoped registration. Authentication and admin-only MCP access remain in
force. Store connector credentials using existing secret references.

The SwarmHPC `business_suite_platform_uat_site.yml` workflow creates a temporary
adapter under the existing Customers identity, reads live platform health through
Relay and Refiner, approves an exact read-only operation through Conductor, and
verifies the durable receipt and lack of duplicate dispatch. It removes the
registration and stops its unprivileged, time-bounded probe on completion.
