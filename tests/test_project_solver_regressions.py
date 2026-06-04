from refiner import project_solver


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_select_verification_steps_prefers_py_compile_without_tests(tmp_path):
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    lang_info = {"languages": ["python"], "build_systems": []}

    steps = project_solver._select_verification_steps(str(tmp_path), lang_info)

    assert steps
    assert steps[0]["command"].startswith("python -m py_compile ")
    assert "pytest" not in steps[0]["command"]


def test_execute_shell_command_treats_pytest_no_tests_as_informational(monkeypatch, tmp_path):
    def _fake_run(*_args, **_kwargs):
        return _FakeCompletedProcess(
            5,
            stdout="collected 0 items\n\n================ no tests ran in 0.01s ================\n",
            stderr="",
        )

    monkeypatch.setattr(project_solver.subprocess, "run", _fake_run)
    actions_log = []
    failure_log = []

    ok = project_solver._execute_shell_command(
        "python -m pytest",
        workdir=str(tmp_path),
        timeout=10,
        actions_log=actions_log,
        failure_log=failure_log,
        dataset_summary=None,
        eval_info=None,
    )

    assert ok is True
    assert failure_log == []
    assert any("treating this verification output as informational" in item.lower() for item in actions_log)


def test_execute_shell_command_keeps_pytest_no_tests_as_failure_when_tests_exist(monkeypatch, tmp_path):
    (tmp_path / "tests").mkdir()

    def _fake_run(*_args, **_kwargs):
        return _FakeCompletedProcess(
            5,
            stdout="collected 0 items\n\n================ no tests ran in 0.01s ================\n",
            stderr="",
        )

    monkeypatch.setattr(project_solver.subprocess, "run", _fake_run)
    actions_log = []
    failure_log = []

    ok = project_solver._execute_shell_command(
        "python -m pytest",
        workdir=str(tmp_path),
        timeout=10,
        actions_log=actions_log,
        failure_log=failure_log,
        dataset_summary=None,
        eval_info=None,
    )

    assert ok is False
    assert failure_log
    assert failure_log[0]["verification_issue"] == "no tests ran"


def test_apply_step_normalizes_parent_workdir(monkeypatch, tmp_path):
    project_root = tmp_path / "sample-project"
    project_root.mkdir()

    captured = {}

    def _fake_execute(command, **kwargs):
        captured["command"] = command
        captured["workdir"] = kwargs.get("workdir")
        return True

    monkeypatch.setattr(project_solver, "_execute_shell_command", _fake_execute)
    actions_log = []
    step = {
        "type": "run_command",
        "command": "ls -R sample-project",
        "workdir": str(tmp_path),
    }

    project_solver._apply_step(
        str(project_root),
        step,
        allow_run=True,
        actions_log=actions_log,
    )

    assert captured["workdir"] == str(project_root)
    assert captured["command"] == "ls -R ."
    assert any("normalized unsafe workdir to project root" in item.lower() for item in actions_log)


def test_is_workspace_project_mirror_source_detection(tmp_path):
    project_root = tmp_path / "MyProj"
    project_root.mkdir()

    assert project_solver._is_workspace_project_mirror_source(
        "project_solver_output/project_root/MyProj/README.md",
        str(project_root),
    )
    assert not project_solver._is_workspace_project_mirror_source(
        "project_solver_output/notes/README.md",
        str(project_root),
    )


def test_drop_blocked_mutating_vcs_steps():
    plan_steps = [
        {"type": "run_command", "command": "git init"},
        {"type": "write_file", "path": "README.md", "content": "ok\n"},
    ]
    actions = []

    filtered, dropped = project_solver._drop_blocked_mutating_vcs_steps(
        plan_steps,
        actions_log=actions,
    )

    assert dropped == 1
    assert len(filtered) == 1
    assert filtered[0]["type"] == "write_file"
    assert any("Dropped blocked mutating VCS command" in item for item in actions)


def test_apply_step_skips_placeholder_command_literal(tmp_path):
    project_root = tmp_path / "sample-project"
    project_root.mkdir()
    actions_log = []
    failure_log = []
    step = {
        "type": "run_command",
        "command": "shell command",
        "workdir": ".",
    }

    project_solver._apply_step(
        str(project_root),
        step,
        allow_run=True,
        actions_log=actions_log,
        failure_log=failure_log,
    )

    assert any("placeholder command literal" in item.lower() for item in actions_log)
    assert failure_log
    assert failure_log[0]["verification_issue"] == "placeholder command literal"


def test_apply_step_normalizes_placeholder_workdir_literal(monkeypatch, tmp_path):
    project_root = tmp_path / "sample-project"
    tests_dir = project_root / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    captured = {}

    def _fake_execute(command, **kwargs):
        captured["command"] = command
        captured["workdir"] = kwargs.get("workdir")
        return True

    monkeypatch.setattr(project_solver, "_execute_shell_command", _fake_execute)
    actions_log = []
    step = {
        "type": "run_command",
        "command": "python -m pytest tests/test_sample.py",
        "workdir": "relative dir (project root) or absolute dir under solver workspace if outside project root",
    }

    project_solver._apply_step(
        str(project_root),
        step,
        allow_run=True,
        actions_log=actions_log,
    )

    assert captured["workdir"] == str(project_root)
    assert any("normalized placeholder workdir literal" in item.lower() for item in actions_log)


def test_rewrite_requirements_path_in_command_normalizes_pytest_target_from_deep_workdir(tmp_path):
    project_root = tmp_path / "sample-project"
    tests_dir = project_root / "project_solver_output" / "src" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_badge_system.py").write_text("def test_badge():\n    assert True\n", encoding="utf-8")
    actions_log = []

    rewritten = project_solver._rewrite_requirements_path_in_command(
        "pytest project_solver_output/src/tests/test_badge_system.py",
        abs_workdir=str(tests_dir),
        project_root=str(project_root),
        workspace_root=str(project_root / "project_solver_output"),
        actions_log=actions_log,
    )

    assert rewritten == "pytest test_badge_system.py"
    assert any("rewrote command paths" in item.lower() for item in actions_log)


def test_plan_local_recovery_generates_pytest_file_not_found_fix(tmp_path):
    workspace = tmp_path / "sample-project"
    tests_dir = workspace / "project_solver_output" / "src" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_badge_system.py").write_text("def test_badge():\n    assert True\n", encoding="utf-8")

    recovery = project_solver._plan_local_recovery(
        command="pytest project_solver_output/src/tests/test_badge_system.py",
        result={
            "workdir": str(tests_dir),
            "stdout": "",
            "stderr": "ERROR: file or directory not found: project_solver_output/src/tests/test_badge_system.py",
        },
        workspace=str(workspace),
        venv_path=None,
    )

    assert recovery
    assert "pytest target path missing" in (recovery.get("reason") or "")
    commands = recovery.get("commands") or []
    assert any(
        isinstance(item, dict)
        and item.get("workdir") == str(tests_dir)
        and "pytest test_badge_system.py" in str(item.get("command"))
        for item in commands
    )
    assert any(
        isinstance(item, dict) and str(item.get("command")).startswith("python -m pytest")
        for item in commands
    )


def test_plan_local_recovery_handles_pytest_import_issue_with_pythonpath(tmp_path):
    workspace = tmp_path / "sample-project"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8")

    recovery = project_solver._plan_local_recovery(
        command="pytest tests/test_app.py",
        result={
            "workdir": str(workspace),
            "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'src'",
        },
        workspace=str(workspace),
        venv_path=None,
    )

    assert recovery
    assert "pytest import path or dependency issue" in (recovery.get("reason") or "")
    commands = recovery.get("commands") or []
    assert any(
        isinstance(item, dict)
        and str(item.get("command")).startswith("PYTHONPATH=. python -m pytest")
        for item in commands
    )


def test_plan_local_recovery_steps_preserves_structured_recovery_workdir(tmp_path):
    project_root = tmp_path / "sample-project"
    tests_dir = project_root / "project_solver_output" / "src" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_badge_system.py").write_text("def test_badge():\n    assert True\n", encoding="utf-8")
    actions_log = []

    steps = project_solver._plan_local_recovery_steps(
        [
            {
                "command": "pytest project_solver_output/src/tests/test_badge_system.py",
                "workdir": str(tests_dir),
                "stdout": "",
                "stderr": "ERROR: file or directory not found: project_solver_output/src/tests/test_badge_system.py",
            }
        ],
        project_root=str(project_root),
        venv_path=None,
        workspace=str(project_root),
        actions_log=actions_log,
    )

    assert steps
    assert any(
        step.get("type") == "run_command"
        and step.get("workdir") == str(tests_dir)
        and "pytest test_badge_system.py" in str(step.get("command"))
        for step in steps
    )


def test_suppress_repeated_failures_breaks_recovery_loop():
    seen_counts = {}
    actions_log = []
    failures = [
        {
            "command": "pytest tests/test_app.py",
            "workdir": ".",
            "exit_code": 4,
            "stderr": "ERROR: file or directory not found: tests/test_app.py",
            "stdout": "",
        }
    ]

    first_pass = project_solver._suppress_repeated_failures(
        failures,
        seen_counts=seen_counts,
        repeat_limit=1,
        actions_log=actions_log,
        scope_label="unit test",
    )
    second_pass = project_solver._suppress_repeated_failures(
        failures,
        seen_counts=seen_counts,
        repeat_limit=1,
        actions_log=actions_log,
        scope_label="unit test",
    )

    assert len(first_pass) == 1
    assert second_pass == []
    assert any("loop breaker" in item.lower() for item in actions_log)


def test_resolve_file_target_normalizes_nested_workspace_prefix(tmp_path):
    project_root = tmp_path / "sample-project"
    workspace_root = project_root / "project_solver_output"
    workspace_root.mkdir(parents=True)
    (workspace_root / "index.html").write_text("<html></html>\n", encoding="utf-8")

    target, note = project_solver._resolve_file_target(
        "project_solver_output/project_solver_output/index.html",
        project_root=str(project_root),
        workspace_root=str(workspace_root),
        step_type="write_file",
        prefer_workspace_new_files=True,
    )

    assert target == str(workspace_root / "index.html")
    assert note and "normalized workspace-prefixed path" in note.lower()


def test_select_verification_steps_targets_workspace_tests_when_project_has_none(tmp_path):
    project_root = tmp_path / "sample-project"
    project_root.mkdir()
    workspace_root = project_root / "project_solver_output"
    tests_dir = workspace_root / "src" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_badge.py").write_text("def test_badge():\n    assert True\n", encoding="utf-8")
    lang_info = {"languages": ["python"], "build_systems": []}

    steps = project_solver._select_verification_steps(
        str(project_root),
        lang_info,
        workspace_root=str(workspace_root),
    )

    assert steps
    first = steps[0]
    assert first["workdir"] == "."
    assert "python -m pytest" in first["command"]
    assert "project_solver_output/src/tests" in first["command"]


def test_plan_local_recovery_steps_skips_retry_for_deterministic_pytest_failures(tmp_path):
    project_root = tmp_path / "sample-project"
    tests_dir = project_root / "project_solver_output" / "src" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_badge_system.py").write_text("def test_badge():\n    assert True\n", encoding="utf-8")
    retry_fingerprints = set()

    steps = project_solver._plan_local_recovery_steps(
        [
            {
                "command": "pytest project_solver_output/src/tests/test_badge_system.py",
                "workdir": str(tests_dir),
                "stdout": "",
                "stderr": "ERROR: file or directory not found: project_solver_output/src/tests/test_badge_system.py",
            }
        ],
        project_root=str(project_root),
        venv_path=None,
        workspace=str(project_root),
        actions_log=[],
        retry_seen_fingerprints=retry_fingerprints,
    )

    assert steps
    assert any("pytest test_badge_system.py" in str(step.get("command")) for step in steps)
    assert all(
        not str(step.get("step", "")).startswith("Retry command after recovery:")
        for step in steps
    )


def test_plan_local_recovery_steps_gates_retries_by_failure_fingerprint(tmp_path):
    project_root = tmp_path / "sample-project"
    project_root.mkdir()
    failure = {
        "command": "pytest tests/test_app.py",
        "workdir": ".",
        "stdout": "FAILED tests/test_app.py::test_main - AssertionError\n",
        "stderr": "",
    }
    retry_fingerprints = set()

    first = project_solver._plan_local_recovery_steps(
        [failure],
        project_root=str(project_root),
        venv_path=None,
        workspace=str(project_root),
        actions_log=[],
        retry_seen_fingerprints=retry_fingerprints,
    )
    second = project_solver._plan_local_recovery_steps(
        [failure],
        project_root=str(project_root),
        venv_path=None,
        workspace=str(project_root),
        actions_log=[],
        retry_seen_fingerprints=retry_fingerprints,
    )

    assert any(
        str(step.get("step", "")).startswith("Retry command after recovery:")
        for step in first
    )
    assert all(
        not str(step.get("step", "")).startswith("Retry command after recovery:")
        for step in second
    )


def test_should_replan_verification_failures_respects_loop_breaker():
    failures = [
        {"command": "python -m pytest", "verification_issue": "no tests ran"},
    ]

    assert project_solver._should_replan_verification_failures(
        failures,
        verification_first=True,
        allow_run=True,
        repeated_failures_exhausted=False,
    )
    assert not project_solver._should_replan_verification_failures(
        failures,
        verification_first=True,
        allow_run=True,
        repeated_failures_exhausted=True,
    )


def test_ensure_global_requirements_remain_global_scope():
    register = {"requirements": []}
    sources = [
        project_solver.RequirementSource(
            path="requirements.md",
            requirements_text="Build a demo app",
            requirement_lines=[],
            todo_lines=[],
            context_excerpt="",
        )
    ]

    result = project_solver._ensure_global_requirements(
        register,
        requirement_sources=sources,
    )

    globals_only = [
        req for req in result.get("requirements", [])
        if isinstance(req, dict) and "global" in (req.get("source") or [])
    ]
    assert globals_only
    assert all(req.get("source") == ["global"] for req in globals_only)
