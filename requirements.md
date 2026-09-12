Overview: Improve Refiner through the Conductor execution loop.

Delivery Context:
- Current stage: production
- Validated stages: development, testing, integration, integration_testing, uat
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
Run a controlled Refiner solver task against the existing rag_demo repository. Requirements Register:
- REQ-001: Inspect the existing project-solver completion and requirement coverage tests before changing files.
- REQ-002: Add or update one focused regression test proving that an explicit hard REQ remains incomplete and requests another iteration until its acceptance evidence is present.
- REQ-003: Keep the change scoped to the Refiner completion and requirement coverage test path; preserve unrelated files.
- REQ-004: Run the focused pytest test and report the exact command and result.
- REQ-005: Record the implementation, verification evidence, and rollback or recovery signal in the final output.

Authoritative delivery constraints (mandatory; implement and verify these, do not merely describe them):
- Required file: tests/test_project_solver_completion_semantics.py (must exist in the delivered repository).

Plan JSON:
{"acceptance":"All explicit REQ IDs are covered, no hard REQ is missing, needs_more_iterations is false, and the focused test passes.","action":"repository_delivery","repository":"neuralmimicry/rag_demo","required_files":["tests/test_project_solver_completion_semantics.py"],"verification_commands":["python -m pytest tests/test_project_solver_completion_semantics.py -q"]}

Planner guidance (advisory; it must not weaken or contradict the authoritative work-item requirements):
Overview: This work item verifies that the Refiner solver correctly handles explicit requirement completion semantics, ensuring hard requirements remain incomplete until acceptance evidence is present. The implementation focuses on the test suite to validate iteration logic without disrupting unrelated code paths.

Requirements Register:
- REQ-001: Inspect the existing project-solver completion and requirement coverage tests before changing files.
- REQ-002: Add or update one focused regression test proving that an explicit hard REQ remains incomplete and requests another iteration until its acceptance evidence is present.
- REQ-003: Keep the change scoped to the Refiner completion and requirement coverage test path; preserve unrelated files.
- REQ-004: Run the focused pytest test and report the exact command and result.
- REQ-005: Record the implementation, verification evidence, and rollback or recovery signal in the final output.
- REQ-006: Ensure the canary rollout strategy is applied safely to the production environment.


Protected rollout contract (mandatory): capture a fresh readiness baseline before any change; use the selected canary or red_green strategy; verify health throughout the post-rollout window; if health or verification degrades, automatically revert the exact produced commit without rewriting history, rerun tests and GitHub Actions, and verify recovery.