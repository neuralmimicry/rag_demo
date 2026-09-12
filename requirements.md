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
Prometheus reports 1/1 scrape target(s) down for Refiner. Restore exporter coverage or scrape reachability so Conductor can weigh live runtime evidence when prioritising improvement work.

Authoritative delivery constraints (mandatory; implement and verify these, do not merely describe them):
- No structured delivery constraints were supplied; follow the work-item summary exactly.

Plan JSON:
{"action":"restore_observability_coverage","down_targets":1,"finding_id":"fc7491be-d458-41ed-b6f7-3d169e03b668","finding_key":"prometheus_target_health:refiner","service":"refiner","total_targets":1}

Planner guidance (advisory; it must not weaken or contradict the authoritative work-item requirements):
Overview: This work item addresses the degraded observability state of the Refiner service by restoring Prometheus scrape coverage. The approach prioritises minimal regression, secure execution, and non-destructive changes to the existing codebase.

Requirements Register:
- REQ-001: Inspect repository state at /srv/neuralmimicry/rag_demo to gather evidence of scrape target failure.
- REQ-002: Identify specific failure points in Prometheus configuration, exporter binary availability, or network reachability.
- REQ-003: Formulate a hypothesis for the degradation based on collected evidence and uncertainty analysis.
- REQ-004: Execute the smallest safe operation (code patch, Ansible job update, or configuration adjustment).
- REQ-005: Run python -m pytest to validate the fix does not introduce regressions in the test suite.
- REQ-006: Perform a fresh protected-target readiness baseline check to confirm exporter accessibility.
- REQ-007: Execute the canary staged rollout via Ansible playbooks to apply the fix to the 'spirit' host.
- REQ-008: Monitor the post-rollout readiness health window to ensure Prometheus scrape targets return to healthy status.
- REQ-009: Implement automatic rollback on degradation if health metrics do not improve within the observation window.
- REQ-010: Verify rollback readiness and recovery procedures are functional before finalising the change.


Protected rollout contract (mandatory): capture a fresh readiness baseline before any change; use the selected canary or red_green strategy; verify health throughout the post-rollout window; if health or verification degrades, automatically revert the exact produced commit without rewriting history, rerun tests and GitHub Actions, and verify recovery.