# NeuralMimicry IDE fallback for project-solver jobs

Refiner can hand a failed `project_solver` request to NeuralMimicry IDE after
the configured solver resume attempts have ended. This is a recovery path for
requests Refiner could not finish; it does not convert a failed Refiner result
into a successful result or automatically merge follow-up work.

Refiner finalises its partial changes on the job's existing `refiner/*` or
`conductor/*` branch and records the full commit SHA. The IDE bridge accepts one
configured repository, verifies the requested branch and SHA, and creates a
separate durable worktree and conversation. The prompt includes bounded
acceptance criteria and the final Refiner outcome. The IDE may inspect and
repair the isolated worktree and use its configured remote runner; it is
explicitly told not to push or merge to Refiner. The owner reviews any resulting
patch. Repeated submissions of the same job, branch and source SHA reuse the
same handoff and initial prompt. A blocked handoff can be retried from the job
detail view without replaying a queued prompt.

The feature is disabled by default. Configure the Refiner role variables in
`swarmhpc/ansible`:

```yaml
continuum_tenant_refiner_managed_ide_enabled: true
continuum_tenant_refiner_managed_ide_url: http://agentd.neuralmimicry-ide.svc.cluster.local:8080/internal/refiner/handoffs
```

Store a separately generated random token (at least 32 characters) in
`swarmhpc/ansible/.secrets/refiner/<inventory-host>/nmide_service_token` with
file mode `0600` and parent directory mode `0700`. Set the same value in the
encrypted NMIDE variables as `nmide_refiner_service_token`, set
`nmide_refiner_handoffs_enabled: true`, and set
`nmide_refiner_repository: neuralmimicry/rag_demo`. The NMIDE Ansible role puts
the token in the `nmide-refiner-bridge` Secret. The Refiner role injects it as
`REFINER_MANAGED_IDE_SERVICE_TOKEN` and its feature flag. Never use a browser
password, GitHub credential or agent-to-broker service token for this bridge.

The NMIDE task-broker GitHub credential is separate and must be able to read
the Refiner source repository and, when remote tasks are enabled, publish only
the configured NMIDE task refs and dispatch/read the trusted SwarmHPC workflow.
Do not enable the bridge until that credential and the immutable runner workflow
are available. The Refiner token authenticates the handoff request; it does not
grant GitHub access.

To validate an enabled deployment, use a disposable project-solver job and
confirm all of the following: the job retains the source SHA and handoff state;
the IDE browser link opens the matching conversation; the worktree starts at
the same commit; duplicate delivery creates no second initial prompt; a runner
result is attached to the source snapshot; and the Refiner branch remains
unchanged by IDE-side actions. Record the Refiner job ID, source SHA, IDE
conversation ID, runner run ID and review outcome without recording either
service token. A queued handoff alone is not acceptance evidence.
