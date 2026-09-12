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
Refiner is currently unreachable. Restore its control-plane visibility, probe path, or runtime health before deeper optimisation.

Authoritative delivery constraints (mandatory; implement and verify these, do not merely describe them):
- No structured delivery constraints were supplied; follow the work-item summary exactly.

Plan JSON:
{"action":"stabilize_service","finding_id":"a38c9424-e035-44d4-84dd-7d678297cc68","finding_key":"service_health:refiner","service":"refiner"}

Planner guidance (advisory; it must not weaken or contradict the authoritative work-item requirements):
Overview: This work item addresses the degraded state of the Refiner service by restoring control-plane visibility and runtime health. The approach prioritises minimal, non-destructive changes to an existing codebase, ensuring operational continuity before deeper optimisation.

Requirements Register:
- REQ-001: Inspect repository and runtime state to gather evidence of Refiner degradation.
- REQ-002: Identify specific failure points in control-plane visibility and probe paths.
- REQ-003: Formulate a hypothesis for the degradation based on collected evidence.
- REQ-004: Execute the smallest safe operation to restore service health.
- REQ-005: Verify the fix using python -m pytest to ensure no regressions.
- REQ-006: Perform a canary staged rollout on host 'rk1' using Ansible playbooks.
- REQ-007: Monitor post-rollout readiness health window for service stability.
- REQ-008: Execute automatic rollback procedures if degradation is detected.
- REQ-009: Ensure all changes are scoped strictly to the Refiner service path.
- REQ-010: Leave unrelated files untouched to maintain system integrity.
- REQ-011: Validate operational notes and delivery gates are not bypassed.
- REQ-012: Confirm acceptance signal that proves the gap is closed.


Protected rollout contract (mandatory): capture a fresh readiness baseline before any change; use the selected canary or red_green strategy; verify health throughout the post-rollout window; if health or verification degrades, automatically revert the exact produced commit without rewriting history, rerun tests and GitHub Actions, and verify recovery.