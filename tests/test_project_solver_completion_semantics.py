from refiner import project_solver


def test_solver_completion_exit_code_is_zero_when_complete():
    assert project_solver._solver_completion_exit_code({"needs_more_iterations": False}) == 0


def test_solver_completion_exit_code_is_nonzero_when_incomplete():
    assert project_solver._solver_completion_exit_code({"needs_more_iterations": True}) == 2


def test_solver_completion_exit_code_defaults_to_zero_without_summary():
    assert project_solver._solver_completion_exit_code(None) == 0


def test_successful_acceptance_verification_can_close_source():
    assert project_solver._verification_proves_source_complete(
        verification_steps_executed=1,
        replan_due_to_hallucination=False,
        replan_due_to_verification=False,
        replan_due_to_replace=False,
        defer_source=False,
        unresolved_failures=[],
        source_path="requirements.md",
    ) is True


def test_unresolved_source_failure_blocks_acceptance_completion():
    assert project_solver._verification_proves_source_complete(
        verification_steps_executed=1,
        replan_due_to_hallucination=False,
        replan_due_to_verification=False,
        replan_due_to_replace=False,
        defer_source=False,
        unresolved_failures=[{"source": "requirements.md", "command": "pytest"}],
        source_path="requirements.md",
    ) is False


def test_code_source_requires_behavior_test_for_acceptance_completion():
    assert project_solver._verification_proves_source_complete(
        verification_steps_executed=1,
        behavior_test_steps_executed=0,
        requires_behavior_test=True,
        replan_due_to_hallucination=False,
        replan_due_to_verification=False,
        replan_due_to_replace=False,
        defer_source=False,
        unresolved_failures=[],
        source_path="requirements.md",
    ) is False
    assert project_solver._verification_proves_source_complete(
        verification_steps_executed=1,
        behavior_test_steps_executed=1,
        requires_behavior_test=True,
        replan_due_to_hallucination=False,
        replan_due_to_verification=False,
        replan_due_to_replace=False,
        defer_source=False,
        unresolved_failures=[],
        source_path="requirements.md",
    ) is True


def test_completion_blockers_include_hard_acceptance_gaps_but_not_advisory_globals():
    blockers = project_solver._completion_blockers(
        planner_failure=False,
        incomplete_sources=[],
        test_quality_missing_sources=[],
        coverage_missing_sources=["requirements.md"],
        requirements_missing_hard_ids=["REQ-001"],
        requirements_missing_advisory_ids=["GLOBAL-REQ-001"],
        requirements_sanity_strict_global=False,
        unresolved_verification_failures=[],
        module_registry={"missing_tests": [], "missing_examples": []},
    )
    assert blockers == ["coverage_missing_sources", "requirements_missing_hard_ids"]


def test_completion_blockers_include_missing_module_evidence():
    blockers = project_solver._completion_blockers(
        planner_failure=False,
        incomplete_sources=[],
        test_quality_missing_sources=[],
        coverage_missing_sources=[],
        requirements_missing_hard_ids=[],
        requirements_missing_advisory_ids=[],
        requirements_sanity_strict_global=False,
        unresolved_verification_failures=[],
        module_registry={"missing_tests": [{"path": "app.js"}], "missing_examples": [{"path": "app.js"}]},
    )
    assert blockers == ["module_missing_tests", "module_missing_examples"]


def test_successful_reverification_clears_matching_historical_failure():
    failures = [
        {
            "source": "requirements.md",
            "command": "python  smoke_test.py",
            "verification_issue": "error output detected",
        },
        {
            "source": "requirements.md",
            "command": "python other_check.py",
            "verification_issue": "error output detected",
        },
    ]
    actions = []

    project_solver._resolve_successful_verification_failures(
        failures,
        [{"command": "python smoke_test.py", "success": True}],
        source_path="requirements.md",
        actions_log=actions,
    )

    assert [failure["command"] for failure in failures] == ["python other_check.py"]
    assert actions == [
        "Resolved 1 historical verification failure(s) after a matching command passed."
    ]


def test_missing_pytest_target_is_informational_without_project_tests(monkeypatch, tmp_path):
    class CompletedProcess:
        returncode = 4
        stdout = "ERROR: test target is unavailable\n"
        stderr = ""

    monkeypatch.setattr(
        project_solver.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(),
    )
    failures = []
    results = []

    success = project_solver._execute_shell_command(
        "python -m pytest missing_test.py",
        workdir=str(tmp_path),
        timeout=30,
        actions_log=[],
        failure_log=failures,
        dataset_summary=None,
        eval_info=None,
        command_results=results,
    )

    assert success is True
    assert failures == []
    assert results[-1]["success"] is True
    assert results[-1]["verification_issue"] is None
