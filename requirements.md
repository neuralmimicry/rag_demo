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
Route concrete code-change proposals into Refiner's project-solver/job APIs so Conductor can stage self-improvement work with verification and audit trails.

Authoritative delivery constraints (mandatory; implement and verify these, do not merely describe them):
- No structured delivery constraints were supplied; follow the work-item summary exactly.

Plan JSON:
{"action":"integrate_refiner_jobs","finding_id":"0cf19634-460c-4d03-a325-7b35122d6c84","finding_key":"refiner_controlled_executor","paths":["/api/jobs","/api/execution/plan"]}

Planner guidance (advisory; it must not weaken or contradict the authoritative work-item requirements):
Overview: This work item establishes Refiner as the controlled code-improvement executor, ensuring all self-improvement efforts are staged, verified, and audited before full deployment. The integration focuses on updating Conductor's execution plan endpoints to handle Refiner job routing securely.

Requirements Register:
- REQ-001: Inspect repository state at /srv/neuralmimicry/rag_demo to gather evidence of existing job execution flows.
- REQ-002: Identify gaps in Conductor execution plan regarding Refiner job routing and audit trail generation.
- REQ-003: Formulate hypotheses for integration requirements focusing on non-destructive API updates.
- REQ-004: Apply targeted code patches to route concrete code-change proposals into Refiner's APIs.
- REQ-005: Deploy updated code to canary target 'spirit' using Ansible playbook continuum_tenant_refiner_site.yml.
- REQ-006: Run project-native verification commands against canary instance to validate job routing.
- REQ-007: Monitor post-rollout readiness health window for any degradation or errors.
- REQ-008: Execute automatic rollback procedures if verification checks fail or service degradation is detected.
- REQ-009: Ensure all changes are scoped, resilient, and secure without bypassing staged delivery gates.
- REQ-010: Leave unrelated files untouched and avoid destructive commands during implementation.
- REQ-011: Maintain audit trails for all self-improvement efforts routed through Refiner.
- REQ-012: Confirm acceptance signal that proves the gap is closed before marking work complete.


Protected rollout contract (mandatory): capture a fresh readiness baseline before any change; use the selected canary or red_green strategy; verify health throughout the post-rollout window; if health or verification degrades, automatically revert the exact produced commit without rewriting history, rerun tests and GitHub Actions, and verify recovery.