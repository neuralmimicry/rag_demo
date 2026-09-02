Overview: Improve Refiner through the Conductor execution loop.

Delivery Context:
- Current stage: development
- Validated stages: none
- Rollout strategy: canary

Requirements Register:
- REQ-001: Inspect and record current repository, runtime, or job evidence before selecting an operation.
- REQ-002: Implement only the scoped change, job update, or progress-monitoring action supported by that evidence.
- REQ-003: Preserve secure, resilient behaviour and avoid destructive commands.
- REQ-004: Update or add tests covering the changed path, or provide the relevant live operational check.
- REQ-005: Run verification commands and report the outcome.
- REQ-006: Leave unrelated files untouched.
- REQ-007: Record rollback/recovery steps and the acceptance signal proving the gap is closed.
- REQ-008: Preserve staged progression and rollout governance metadata.
- REQ-009: Capture a fresh protected-target readiness baseline before any change.
- REQ-010: Use the selected canary or red-green rollout strategy and verify the post-rollout health window.
- REQ-011: Automatically revert the exact produced commit without rewriting history if health or verification degrades.
- REQ-012: Verify rollback readiness and recovery before finalising the delivery.
- REQ-013: When runtime rollout or restart work is needed, use the available Ansible automation context: {"ansible_root":"/srv/swarmhpc/ansible","config_path":"/srv/swarmhpc/ansible/ansible.cfg","host_targets":["rk1"],"hosts":["spirit"],"inventory_path":"/srv/swarmhpc/ansible/inventory/hosts.ini","playbooks":["continuum_tenant_nmchain_site.yml","continuum_tenant_refiner_site.yml"],"repo_root":"/srv/swarmhpc","roles_path":"/srv/swarmhpc/ansible/roles","secrets_root":"/srv/swarmhpc/ansible/.secrets"}.

Work Item Summary:
Refiner is currently degraded. Restore its control-plane visibility, probe path, or runtime health before deeper optimisation.

Authoritative delivery constraints (mandatory; implement and verify these, do not merely describe them):
- No structured delivery constraints were supplied; follow the work-item summary exactly.

Plan JSON:
{"action":"stabilize_service","finding_id":"cece18c9-5e6c-4dfc-a85d-c5c78f69fdec","finding_key":"service_health:refiner","service":"refiner"}

Planner guidance (advisory; it must not weaken or contradict the authoritative work-item requirements):
This plan outlines the mandatory verification procedures for stabilising the Refiner service within the governed delivery pipeline. The objective is to ensure code integrity, establish a protected-target readiness baseline, and validate the canary rollout strategy without destructive changes.

Requirements Register:
- REQ-001: Inspect repository state at /srv/neuralmimicry/rag_demo to confirm branch integrity and file structure.
- REQ-002: Create docs/refiner-supported-languages.md documenting supported languages and verification requirements.
- REQ-003: Execute `cargo fmt --check` to validate code formatting compliance without modifying source files.
- REQ-004: Run `cargo check` to validate compilation success and identify latent errors in Rust components.
- REQ-005: Execute `cargo test` to verify the native test suite passes without regressions.
- REQ-006: Deploy updated configuration to the 'spirit' host using the canary strategy via Ansible.
- REQ-007: Monitor probe path health and control-plane visibility metrics during the canary readiness window.
- REQ-008: Execute automatic rollback procedures if health checks fail or degradation is observed.
- REQ-009: Verify rollback readiness and recovery procedures are functional before finalising the change.
- REQ-010: Ensure no unrelated files are touched and the implementation remains non-destructive throughout.
- REQ-011: Confirm that the acceptance signal proves the gap is closed and the service is stable.
- REQ-012: Document all verification steps and outcomes in the project-native verification logs.


Protected rollout contract (mandatory): capture a fresh readiness baseline before any change; use the selected canary or red_green strategy; verify health throughout the post-rollout window; if health or verification degrades, automatically revert the exact produced commit without rewriting history, rerun tests and GitHub Actions, and verify recovery.