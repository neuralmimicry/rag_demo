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
rag_demo is linked to live services but no obvious test capability was discovered in the repository inventory. Establish at least a minimal regression or smoke-test baseline before deeper autonomous changes.

Authoritative delivery constraints (mandatory; implement and verify these, do not merely describe them):
- No structured delivery constraints were supplied; follow the work-item summary exactly.

Plan JSON:
{"action":"establish_repository_test_baseline","finding_id":"32f41920-aa6d-4f86-8707-184c4a427695","finding_key":"repository_test_baseline:neuralmimicry/rag_demo","linked_services":["refiner"],"repository":"neuralmimicry/rag_demo"}

Planner guidance (advisory; it must not weaken or contradict the authoritative work-item requirements):
Overview: This work item establishes a test baseline for the rag_demo repository to enable safe autonomous changes. The baseline ensures that live services linked to the repository remain stable during development. A minimal regression suite is created to catch regressions early without impacting production logic.

Requirements Register:
- REQ-001: Inspect repository state at /srv/neuralmimicry/rag_demo to identify existing test coverage and runtime configurations.
- REQ-002: Document observed evidence regarding test capability gaps and uncertainty levels in the repository inventory.
- REQ-003: Create a minimal smoke test file in the tests directory to verify core service health without modifying production logic.
- REQ-004: Execute the new smoke test using python -m pytest to confirm the baseline is stable and non-destructive.
- REQ-005: Perform a canary staged rollout to the target host 'spirit' via Ansible to validate operational readiness.
- REQ-006: Monitor the post-rollout readiness health window for any degradation or service disruption.
- REQ-007: Verify automatic rollback mechanisms are functional and test recovery procedures if degradation is detected.
- REQ-008: Confirm the acceptance signal that proves the test baseline gap is closed and the system is ready for further development.


Protected rollout contract (mandatory): capture a fresh readiness baseline before any change; use the selected canary or red_green strategy; verify health throughout the post-rollout window; if health or verification degrades, automatically revert the exact produced commit without rewriting history, rerun tests and GitHub Actions, and verify recovery.