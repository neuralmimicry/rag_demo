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
