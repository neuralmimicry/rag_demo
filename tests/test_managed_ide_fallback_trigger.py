from types import SimpleNamespace

import pytest

from refiner import refiner_web


SOURCE_SHA = "b" * 40


class _SolverProcess:
    pid = 12345

    def __init__(self, exit_code):
        self.stdout = ()
        self._exit_code = exit_code

    def wait(self):
        return self._exit_code


def _run_solver_job(
    monkeypatch,
    tmp_path,
    *,
    workflow,
    exit_code,
    stop_requested=False,
    incomplete=False,
    resume_success=False,
):
    manager = object.__new__(refiner_web.JobManager)
    finalise_calls = []
    handoffs = []

    manager._compute_wait_seconds = lambda _job: 0
    manager._prepare_repo = lambda _job: None
    manager._guardrail_check = lambda _job: None
    manager._build_command = lambda _job: ["refiner-project-solver-test"]
    manager._compute_runtime_seconds = lambda _job: 1
    manager._finalize_repo = lambda _job, *, verify_builds=True: finalise_calls.append(verify_builds)
    manager._git_run = lambda _command, _cwd, _job, token=None: SimpleNamespace(stdout=SOURCE_SHA)
    manager._git_has_changes = lambda _workspace, _job: False
    manager._settle_tokens = lambda _job: None
    manager._maybe_notify = lambda _job: None
    manager.resume_job = lambda _job_id: resume_success

    job = refiner_web.Job(
        job_id="managed-ide-trigger-test",
        payload={
            "workflow": workflow,
            "requirements_text": "Complete every requested acceptance criterion.",
            "solver_command_policy_mode": "disabled",
        },
        repo_info={
            "owner": "neuralmimicry",
            "repo": "rag_demo",
            "branch": "refiner/managed-ide-trigger-test",
            "workspace": str(tmp_path),
        },
    )
    job.stop_requested = stop_requested
    job.persist = lambda force=False: None

    monkeypatch.setenv("REFINER_MANAGED_IDE_ENABLED", "true")
    monkeypatch.setenv("REFINER_MANAGED_IDE_URL", "http://agentd.example.test/internal/refiner/handoffs")
    monkeypatch.setenv("REFINER_MANAGED_IDE_SERVICE_TOKEN", "s" * 40)
    monkeypatch.setattr(refiner_web, "_user_settings", lambda _owner: {})
    monkeypatch.setattr(refiner_web, "_user_role", lambda _owner: None)
    monkeypatch.setattr(refiner_web, "_build_job_runtime_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(refiner_web, "_user_can_use_shared_llm_credentials", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(refiner_web.subprocess, "Popen", lambda *_args, **_kwargs: _SolverProcess(exit_code))
    monkeypatch.setattr(
        refiner_web,
        "_completion_summary_from_output",
        lambda _path: {"needs_more_iterations": incomplete, "requirements_met": False},
    )
    monkeypatch.setattr(
        refiner_web,
        "_completion_reason_from_summary",
        lambda _summary: "Acceptance criteria remain incomplete" if exit_code else None,
    )

    def fake_submit_handoff(payload, **_kwargs):
        handoffs.append(payload)
        return {"status": "queued", "conversationId": "conversation-test", "sourceSha": payload["sourceSha"]}

    monkeypatch.setattr(refiner_web, "submit_handoff", fake_submit_handoff)
    manager._run_job(job)
    return manager, job, finalise_calls, handoffs


@pytest.mark.parametrize(
    ("workflow", "exit_code", "stop_requested", "expected_status", "expect_handoff"),
    [
        ("project_solver", 1, False, "failed", True),
        ("project_solver", 0, False, "completed", False),
        ("research", 1, False, "failed", False),
        ("project_solver", 1, True, "stopped", False),
    ],
)
def test_managed_ide_is_offered_only_after_an_unstopped_project_solver_failure(
    monkeypatch, tmp_path, workflow, exit_code, stop_requested, expected_status, expect_handoff
):
    _manager, job, finalise_calls, handoffs = _run_solver_job(
        monkeypatch,
        tmp_path,
        workflow=workflow,
        exit_code=exit_code,
        stop_requested=stop_requested,
    )

    assert job.status == expected_status
    assert len(handoffs) == int(expect_handoff)
    if expect_handoff:
        assert handoffs[0]["repository"] == "neuralmimicry/rag_demo"
        assert handoffs[0]["sourceRef"] == "refiner/managed-ide-trigger-test"
        assert handoffs[0]["sourceSha"] == SOURCE_SHA
        assert "Acceptance criteria remain incomplete" in handoffs[0]["prompt"]
        assert job.repo_info["managed_ide_handoff"]["status"] == "queued"
        assert job.repo_info["managed_ide_handoff"]["sourceSha"] == SOURCE_SHA
        assert finalise_calls == [False]
    elif workflow == "project_solver" and exit_code == 0:
        assert finalise_calls == [True]
    else:
        assert finalise_calls == []


def test_incomplete_solver_is_resumed_before_managed_ide_handoff(monkeypatch, tmp_path):
    monkeypatch.setattr(refiner_web, "SOLVER_AUTO_RESUME_MAX", 1)
    _manager, job, _finalise_calls, handoffs = _run_solver_job(
        monkeypatch,
        tmp_path,
        workflow="project_solver",
        exit_code=2,
        incomplete=True,
        resume_success=True,
    )

    assert job.payload["solver_auto_resume_count"] == 1
    assert handoffs == []
    assert job.status == "running"
