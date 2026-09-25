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
{"action":"establish_repository_test_baseline","finding_id":"6c334d10-d90f-473d-b727-cdb6c96eb33d","finding_key":"repository_test_baseline:rag_demo","linked_services":["refiner"],"repository":"rag_demo"}

Planner guidance (advisory; it must not weaken or contradict the authoritative work-item requirements):
Overview: This work item establishes a test baseline for the rag_demo service, which is linked to live services but currently lacks executable project-native validation. The baseline will enable safe autonomous changes by confirming repository accessibility, cluster health, and test coverage before deeper delivery actions.

Requirements Register:
- REQ-001: Inspect the rag_demo repository at /srv/neuralmimicry/rag_demo to confirm path accessibility, current branch state, and existing test coverage gaps.
- REQ-002: Query the local K3s cluster status using kubectl get nodes to verify control-plane connectivity and worker node probes.
- REQ-003: Review service health logs and metrics on host 'spirit' to identify specific degradation causes or operational context before proceeding.
- REQ-004: Execute ansible-playbook /srv/swarmhpc/ansible/playbooks/continuum_tenant_refiner_site.yml --check to validate Ansible syntax and inventory without applying changes.
- REQ-005: Run python -m pytest --collect-only to discover available tests and report coverage gaps.
- REQ-006: Execute a subset of high-confidence tests using python -m pytest -x to verify current runtime stability.
- REQ-007: Document any blocked tests or infrastructure gaps in the requirements register.
- REQ-008: Create a protected-target readiness baseline entry reflecting current evidence and uncertainty.

Rollout Strategy: Canary
Verification: Successful execution of the above steps, positive pytest results, and stable cluster metrics.


Protected rollout contract (mandatory): capture a fresh readiness baseline before any change; use the selected canary or red_green strategy; verify health throughout the post-rollout window; if health or verification degrades, automatically revert the exact produced commit without rewriting history, rerun tests and GitHub Actions, and verify recovery.