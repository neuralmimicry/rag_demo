from refiner import project_solver


def test_solver_completion_exit_code_is_zero_when_complete():
    assert project_solver._solver_completion_exit_code({"needs_more_iterations": False}) == 0


def test_solver_completion_exit_code_is_nonzero_when_incomplete():
    assert project_solver._solver_completion_exit_code({"needs_more_iterations": True}) == 2


def test_solver_completion_exit_code_defaults_to_zero_without_summary():
    assert project_solver._solver_completion_exit_code(None) == 0


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


def test_missing_pytest_target_is_informational_without_project_tests(tmp_path):
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
