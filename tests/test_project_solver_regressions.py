import json

from refiner import project_solver
from refiner.llm_providers import LLMError


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeServerProcess:
    pid = 12345


def test_foreground_http_server_is_bounded_and_cleaned_up(monkeypatch, tmp_path):
    process = _FakeServerProcess()
    terminated = []

    monkeypatch.setattr(project_solver, "_is_port_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(project_solver, "_wait_for_port", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        project_solver.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        project_solver,
        "terminate_process_tree",
        lambda value: terminated.append(value),
    )

    actions_log = []
    failure_log = []
    executed_commands = []
    ok = project_solver._execute_shell_command(
        "python -m http.server 8000",
        workdir=str(tmp_path),
        timeout=600,
        actions_log=actions_log,
        failure_log=failure_log,
        dataset_summary=None,
        eval_info=None,
        executed_commands=executed_commands,
    )

    assert ok is True
    assert failure_log == []
    assert terminated == [process]
    assert executed_commands == ["python -m http.server 8000"]
    assert any("bounded foreground http server smoke check succeeded" in item.lower() for item in actions_log)


def test_node_http_server_entrypoint_is_recognised_for_bounded_smoke_check(tmp_path):
    entrypoint = tmp_path / "server.js"
    entrypoint.write_text(
        "const http = require('http');\n"
        "http.createServer((_req, res) => res.end('ok')).listen(3000);\n",
        encoding="utf-8",
    )

    assert project_solver._parse_foreground_server_port(
        ["node", "server.js"], workdir=str(tmp_path)
    ) == 3000


def test_node_syntax_check_is_not_treated_as_foreground_server(tmp_path):
    entrypoint = tmp_path / "server.js"
    entrypoint.write_text(
        "require('http').createServer((_req, res) => res.end('ok')).listen(3000);\n",
        encoding="utf-8",
    )

    assert project_solver._parse_foreground_server_port(
        ["node", "--check", "server.js"], workdir=str(tmp_path)
    ) is None


def test_node_server_chain_keeps_server_alive_for_localhost_probe(monkeypatch, tmp_path):
    entrypoint = tmp_path / "server.js"
    entrypoint.write_text(
        "require('http').createServer((_req, res) => res.end('ok')).listen(3000);\n",
        encoding="utf-8",
    )
    process = _FakeServerProcess()
    terminated = []
    commands = []

    monkeypatch.setattr(project_solver, "_is_port_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(project_solver, "_wait_for_port", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(project_solver.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        project_solver.subprocess,
        "run",
        lambda argv, **_kwargs: (
            commands.append(list(argv)) or _FakeCompletedProcess(0, "ok\n", "")
        ),
    )
    monkeypatch.setattr(
        project_solver,
        "terminate_process_tree",
        lambda value: terminated.append(value),
    )

    actions_log = []
    failure_log = []
    assert project_solver._execute_shell_command(
        "node server.js && curl http://localhost:3000/",
        workdir=str(tmp_path),
        timeout=30,
        actions_log=actions_log,
        failure_log=failure_log,
        dataset_summary=None,
        eval_info=None,
    ) is True
    assert commands == [["curl", "http://localhost:3000/"]]
    assert terminated == [process]
    assert failure_log == []


def test_select_verification_steps_prefers_py_compile_without_tests(tmp_path):
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    lang_info = {"languages": ["python"], "build_systems": []}

    steps = project_solver._select_verification_steps(str(tmp_path), lang_info)

    assert steps
    assert steps[0]["command"].startswith("python -m py_compile ")
    assert "pytest" not in steps[0]["command"]


def test_fresh_project_has_no_test_surface(tmp_path):
    assert not project_solver._project_has_test_surface(str(tmp_path))

    (tmp_path / "server.test.js").write_text("describe('server', () => {});\n", encoding="utf-8")
    assert project_solver._project_has_test_surface(str(tmp_path))


def test_plan_verification_uses_pending_source_files_for_fresh_workspace():
    steps = project_solver._select_plan_verification_steps(
        [
            {"type": "write_file", "path": "server.js", "content": ""},
            {"type": "write_file", "path": "index.html", "content": ""},
            {"type": "write_file", "path": "app.py", "content": ""},
        ]
    )

    commands = [step["command"] for step in steps]
    assert commands == [
        "node --check server.js",
        "python -m py_compile app.py",
    ]
    assert all("pytest" not in command for command in commands)


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


def test_apply_step_normalizes_redundant_project_workdir(monkeypatch, tmp_path):
    project_root = tmp_path / "Homework-Habit-Hero"
    project_root.mkdir()
    (project_root / project_root.name).mkdir()

    captured = {}

    def _fake_execute(command, **kwargs):
        captured["command"] = command
        captured["workdir"] = kwargs.get("workdir")
        return True

    monkeypatch.setattr(project_solver, "_execute_shell_command", _fake_execute)
    actions_log = []
    step = {
        "type": "run_command",
        "command": "node server.js",
        "workdir": project_root.name,
    }

    project_solver._apply_step(
        str(project_root),
        step,
        allow_run=True,
        actions_log=actions_log,
    )

    assert captured["workdir"] == str(project_root)
    assert captured["command"] == "node server.js"
    assert any("normalized redundant project workdir" in item.lower() for item in actions_log)


def test_rewrite_workspace_command_paths_for_node_script(tmp_path):
    project_root = tmp_path / "sample-project"
    workspace_root = project_root / "project_solver_output"
    workspace_root.mkdir(parents=True)
    (workspace_root / "server.js").write_text("console.log('ok')\n", encoding="utf-8")
    actions_log = []

    rewritten = project_solver._rewrite_workspace_command_paths(
        "node server.js",
        abs_workdir=str(project_root),
        project_root=str(project_root),
        workspace_root=str(workspace_root),
        actions_log=actions_log,
    )

    assert rewritten == "node project_solver_output/server.js"
    assert actions_log


def test_rewrite_workspace_command_paths_for_embedded_python_check(tmp_path):
    project_root = tmp_path / "sample-project"
    workspace_root = project_root / "project_solver_output"
    workspace_root.mkdir(parents=True)
    (workspace_root / "probe.txt").write_text("READY", encoding="utf-8")
    actions_log = []

    rewritten = project_solver._rewrite_workspace_command_paths(
        "python -c \"from pathlib import Path; assert Path('probe.txt').read_text() == 'READY'\"",
        abs_workdir=str(project_root),
        project_root=str(project_root),
        workspace_root=str(workspace_root),
        actions_log=actions_log,
    )

    assert "project_solver_output/probe.txt" in rewritten
    assert actions_log


def test_requirements_explicit_repository_root_disables_solver_workspace():
    assert project_solver._requirements_specify_output_dir(
        "Create PROBE_STATUS.md in the repository root."
    )
    assert project_solver._requirements_specify_output_dir(
        "Keep the generated manifest in the project root."
    )
    assert not project_solver._requirements_specify_output_dir(
        "Create the generated report without specifying its location."
    )


def test_contract_validation_intent_builds_required_artifacts_and_command(tmp_path):
    source = project_solver.RequirementSource(
        path="requirements.md",
        requirements_text=(
            "Validate the contract in the isolated starter workspace. "
            "Create validation_report.md and test_contract.py, then run "
            "python -m pytest -q."
        ),
        requirement_lines=[],
        todo_lines=[],
        context_excerpt="",
    )

    intent = project_solver._classify_local_intent(source, str(tmp_path))
    assert intent["intent"] == "contract_validation"

    payload = project_solver._build_local_plan_from_intent(
        intent,
        source,
        str(tmp_path),
        {"languages": ["python"], "build_systems": []},
        allow_run=True,
        required_ids=set(),
    )

    assert payload is not None
    assert payload["done"] is True
    steps = payload["plan"]
    assert {step["path"] for step in steps if step["type"] == "write_file"} == {
        "validation_report.md",
        "test_contract.py",
    }
    assert any(
        step["type"] == "run_command" and step["command"] == "python -m pytest -q"
        for step in steps
    )


def test_command_should_use_workspace_for_generated_node_project(tmp_path):
    project_root = tmp_path / "sample-project"
    workspace_root = project_root / "project_solver_output"
    workspace_root.mkdir(parents=True)
    (workspace_root / "server.js").write_text("console.log('ok')\n", encoding="utf-8")
    (workspace_root / "data").mkdir()
    (workspace_root / "data" / "science-facts.json").write_text("[]\n", encoding="utf-8")

    assert project_solver._command_should_use_workspace(
        "node server.js",
        abs_workdir=str(project_root),
        project_root=str(project_root),
        workspace_root=str(workspace_root),
    )
    (project_root / "server.js").write_text("console.log('project')\n", encoding="utf-8")
    assert not project_solver._command_should_use_workspace(
        "node server.js",
        abs_workdir=str(project_root),
        project_root=str(project_root),
        workspace_root=str(workspace_root),
    )


def test_project_root_prefixed_commands_are_relative_to_actual_root_after_retry(tmp_path):
    project_root = tmp_path / "Maths-Practice-Game-job"
    workspace_root = tmp_path / "project_solver_output"
    project_root.mkdir()
    workspace_root.mkdir()

    rewritten = project_solver._rewrite_project_root_prefixed_command_paths(
        "mkdir -p project_root/views && python -c \"from pathlib import Path; Path('project_root/views/index.ejs').write_text('ok')\"",
        abs_workdir=str(workspace_root),
        project_root=str(project_root),
    )

    assert "../Maths-Practice-Game-job/views" in rewritten
    assert "project_solver_output/project_root" not in rewritten

    target, note = project_solver._resolve_file_target(
        "project_root/views/index.ejs",
        project_root=str(project_root),
        workspace_root=str(workspace_root),
        step_type="write_file",
        prefer_workspace_new_files=True,
    )
    assert target == "views/index.ejs"
    assert note and "project-root alias" in note


def test_plan_drops_exact_directory_file_collision_but_keeps_valid_parent(tmp_path):
    actions = []
    steps = [
        {"type": "create_dir", "path": "views"},
        {"type": "write_file", "path": "views", "content": "legacy"},
        {"type": "create_dir", "path": "assets"},
        {"type": "write_file", "path": "assets/index.ejs", "content": "ok"},
    ]

    filtered, dropped = project_solver._drop_exact_directory_file_conflicts(
        steps,
        actions_log=actions,
    )

    assert dropped == 1
    assert [step["path"] for step in filtered] == ["views", "assets", "assets/index.ejs"]
    assert any("exact path is also a file target" in item for item in actions)


def test_planner_timeout_writes_incomplete_project_solution(monkeypatch, tmp_path):
    project_root = tmp_path / "sample-project"
    project_root.mkdir()
    requirements_path = project_root / "requirements.md"
    requirements_path.write_text(
        "Build a small web application with a server.js entrypoint.\n",
        encoding="utf-8",
    )
    output_path = project_root / "project_solution.json"

    class _FailingPlanner:
        name = "test-planner"
        model = "test-model"

        def predict(self, *_args, **_kwargs):
            raise LLMError("HTTP POST failed: Read timed out")

        def cleanup(self):
            return None

    provider = _FailingPlanner()
    monkeypatch.setattr(project_solver, "build_workflow_provider", lambda **_kwargs: provider)
    monkeypatch.setattr(project_solver, "describe_provider", lambda _provider: {"name": "test-planner"})
    monkeypatch.setattr(project_solver, "provider_log_summary", lambda _provider: "test-planner")
    monkeypatch.setenv("SOLVER_TRY_OLLAMA_FIRST", "0")

    exit_code = project_solver.run_project_solver(
        str(project_root),
        requirements_path=str(requirements_path),
        output_path=str(output_path),
        llm_provider="openai",
        llm_model="test-model",
        ollama_base_url=None,
        llm_max_tokens=128,
        llm_temperature=0.2,
        llm_timeout=5,
        llm_reasoning_effort=None,
        llm_api_key=None,
        max_steps=4,
        max_iterations=1,
    )

    assert exit_code == 2
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "incomplete"
    assert report["completion_summary"]["status"] == "incomplete"
    assert report["completion_summary"]["needs_more_iterations"] is True
    assert report["planner_failure"]["type"] == "planner_timeout"


def test_code_source_without_configured_agent_uses_automatic_coding_recovery(monkeypatch, tmp_path):
    project_root = tmp_path / "sample-project"
    project_root.mkdir()
    requirements_path = project_root / "requirements.md"
    requirements_path.write_text(
        "Create a server.js file for the application.\n",
        encoding="utf-8",
    )
    output_path = project_root / "project_solution.json"

    class _Planner:
        name = "test-planner"
        model = "test-model"

        def predict(self, *_args, **_kwargs):
            return type("Response", (), {"text": '{"done": true, "plan": []}'})()

        def cleanup(self):
            return None

    recovery_agents = []

    def _fake_codingagent_plan(**kwargs):
        recovery_agents.append(kwargs["agent"])
        return {"done": True, "plan": [{"type": "note", "step": "recovery response"}]}

    provider = _Planner()
    monkeypatch.setattr(project_solver, "build_workflow_provider", lambda **_kwargs: provider)
    monkeypatch.setattr(project_solver, "describe_provider", lambda _provider: {"name": "test-planner"})
    monkeypatch.setattr(project_solver, "provider_log_summary", lambda _provider: "test-planner")
    monkeypatch.setattr(project_solver, "_query_codingagent_plan", _fake_codingagent_plan)
    monkeypatch.setenv("SOLVER_TRY_OLLAMA_FIRST", "0")
    monkeypatch.setenv("SOLVER_AUTO_CODINGAGENT", "1")

    project_solver.run_project_solver(
        str(project_root),
        requirements_path=str(requirements_path),
        output_path=str(output_path),
        llm_provider="openai",
        llm_model="test-model",
        ollama_base_url=None,
        llm_max_tokens=128,
        llm_temperature=0.2,
        llm_timeout=5,
        llm_reasoning_effort=None,
        llm_api_key=None,
        max_steps=4,
        max_iterations=1,
    )

    assert recovery_agents
    assert set(recovery_agents) == {"codex"}


def test_requirements_only_skips_project_derived_requirement_enrichment(monkeypatch, tmp_path):
    project_root = tmp_path / "sample-project"
    project_root.mkdir()
    requirements_path = project_root / "requirements.md"
    requirements_path.write_text("Restore the service health endpoint.\n", encoding="utf-8")
    output_path = project_root / "project_solution.json"

    def _unexpected_project_scan(*_args, **_kwargs):
        raise AssertionError("project-derived sequence enrichment must be disabled")

    monkeypatch.setattr(project_solver, "_detect_sequence_gaps", _unexpected_project_scan)
    monkeypatch.setenv("SOLVER_TRY_OLLAMA_FIRST", "0")
    monkeypatch.setenv("SOLVER_REPO_RAG", "0")

    class _FailingPlanner:
        name = "test-planner"
        model = "test-model"

        def predict(self, *_args, **_kwargs):
            raise LLMError("HTTP POST failed: Read timed out")

        def cleanup(self):
            return None

    provider = _FailingPlanner()
    monkeypatch.setattr(project_solver, "build_workflow_provider", lambda **_kwargs: provider)
    monkeypatch.setattr(project_solver, "describe_provider", lambda _provider: {"name": "test-planner"})
    monkeypatch.setattr(project_solver, "provider_log_summary", lambda _provider: "test-planner")

    exit_code = project_solver.run_project_solver(
        str(project_root),
        requirements_path=str(requirements_path),
        requirements_only=True,
        output_path=str(output_path),
        llm_provider="openai",
        llm_model="test-model",
        ollama_base_url=None,
        llm_max_tokens=128,
        llm_temperature=0.2,
        llm_timeout=5,
        llm_reasoning_effort=None,
        llm_api_key=None,
        max_steps=4,
        max_iterations=1,
    )

    assert exit_code == 2
    report = json.loads(output_path.read_text(encoding="utf-8"))
    register = report["requirements_register"]["requirements"]
    assert not any("sequence completeness" in item["description"].lower() for item in register)


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


def test_plan_has_test_changes_accepts_explicit_validation_scripts():
    assert project_solver._plan_has_test_changes(
        [
            {
                "type": "write_file",
                "path": "check_validation.sh",
                "content": "#!/usr/bin/env bash\nset -e\n",
            }
        ]
    )
    assert project_solver._plan_has_test_changes(
        [
            {
                "type": "write_file",
                "path": "acceptance/verify_contract.py",
                "content": "assert True\n",
            }
        ]
    )


def test_plan_has_test_changes_does_not_accept_unrelated_artifacts():
    assert not project_solver._plan_has_test_changes(
        [
            {
                "type": "write_file",
                "path": "validation_artifact.txt",
                "content": "created\n",
            }
        ]
    )


def test_requirement_coverage_requires_matching_requirement_id():
    source = project_solver.RequirementSource(
        path="requirements.md",
        requirements_text="Implement the application.",
        requirement_lines=[],
        todo_lines=[],
        context_excerpt="",
    )
    register = {
        "requirements": [
            {
                "id": "REQ-001",
                "title": "Counter",
                "description": "Implement the counter behavior.",
                "source": ["requirements.md"],
            },
            {
                "id": "REQ-002",
                "title": "Reset",
                "description": "Implement the reset behavior.",
                "source": ["requirements.md"],
            },
        ]
    }
    coverage, missing = project_solver._build_requirement_coverage(
        [source],
        {
            "requirements.md": [
                {
                    "path": "app.js",
                    "is_code": True,
                    "requirement_ids": ["REQ-001"],
                }
            ]
        },
        "/tmp/project",
        requirements_register=register,
    )

    requirements = coverage["requirements.md"]["requirements"]
    assert requirements[0]["status"] == "covered"
    assert requirements[1]["status"] == "missing"
    assert "REQ-002: Reset" in coverage["requirements.md"]["missing_requirements"]
    assert missing == ["requirements.md"]


def test_requirement_ids_in_source_comments_do_not_create_traceability():
    step = {
        "type": "write_file",
        "path": "app.js",
        "content": "// REQ-001\nconsole.log('implementation');\n",
    }

    assert project_solver._extract_requirement_refs_from_step(step) == []
    assert project_solver._extract_requirement_refs_from_plan([step], []) == set()


def test_behavior_test_classification_excludes_syntax_checks():
    assert not project_solver._is_behavior_test_command("node --check app.js")
    assert not project_solver._is_behavior_test_command("python -m py_compile app.py")
    assert project_solver._is_behavior_test_command("node smoke_test.js")
    assert project_solver._is_behavior_test_command("python -m pytest tests/test_app.py")


def test_plan_behavior_test_is_added_for_smoke_test():
    steps = [
        {
            "type": "write_file",
            "path": "app.js",
            "content": "console.log('ok');\n",
        },
        {
            "type": "write_file",
            "path": "smoke_test.js",
            "content": "require('node:assert').equal(1, 1);\n",
        },
    ]

    checks = project_solver._select_plan_behavior_test_steps(steps)
    assert [check["command"] for check in checks] == ["node smoke_test.js"]


def test_test_artifact_quality_rejects_placeholder(tmp_path):
    path = tmp_path / "smoke_test.js"
    path.write_text("console.log('smoke');\n", encoding="utf-8")
    assert "assertion" in project_solver._test_artifact_quality(str(path))
    path.write_text("require('node:assert').equal(1, 1);\n", encoding="utf-8")
    assert project_solver._test_artifact_quality(str(path)) is None


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


def test_rewrite_requirements_path_in_command_normalizes_python_entrypoint_from_project_root(tmp_path):
    project_root = tmp_path / "sample-project"
    workspace_root = project_root / "project_solver_output"
    app_path = workspace_root / "flashcard_app" / "app.py"
    app_path.parent.mkdir(parents=True)
    app_path.write_text("print('ok')\n", encoding="utf-8")
    actions_log = []

    rewritten = project_solver._rewrite_requirements_path_in_command(
        "python flashcard_app/app.py",
        abs_workdir=str(project_root),
        project_root=str(project_root),
        workspace_root=str(workspace_root),
        actions_log=actions_log,
    )

    assert rewritten == "python project_solver_output/flashcard_app/app.py"
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
    assert any(
        isinstance(item, dict) and item.get("workdir") == str(workspace)
        for item in commands
    )


def test_plan_local_recovery_handles_pytest_import_issue_prefers_solver_workspace_workdir(tmp_path):
    project_root = tmp_path / "sample-project"
    project_root.mkdir()
    solver_workspace = project_root / "project_solver_output"
    pkg_dir = solver_workspace / "flashcard_app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    recovery = project_solver._plan_local_recovery(
        command="python -m pytest project_solver_output/tests/test_flashcards.py",
        result={
            "workdir": str(project_root),
            "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'flashcard_app.app'",
        },
        workspace=str(solver_workspace),
        venv_path=None,
    )

    assert recovery
    commands = recovery.get("commands") or []
    assert any(
        isinstance(item, dict)
        and item.get("workdir") == str(solver_workspace)
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

    assert target == "index.html"
    assert note and "canonical project-root" in note.lower()


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


def test_repair_source_requirement_registration_preserves_explicit_ids_and_drops_heading():
    source = project_solver.RequirementSource(
        path="requirements.md",
        requirements_text=(
            "Overview: build a flashcard app.\n\n"
            "Requirements Register:\n"
            "- REQ-001: Use Node.js for the backend.\n"
            "- REQ-002: Export the Express app for tests.\n"
        ),
        requirement_lines=[],
        todo_lines=[],
        context_excerpt="",
    )
    register = {
        "requirements": [
            {"id": "REQ-001", "title": "Overview", "description": "Overview"},
            {"id": "REQ-002", "title": "Requirements Register", "description": "Requirements Register"},
            {"id": "GLOBAL-REQ-001", "title": "Clean code", "description": "Keep code maintainable", "source": ["global"]},
        ]
    }

    repaired = project_solver._repair_source_requirement_registration(register, [source])
    by_id = {item["id"]: item for item in repaired["requirements"]}

    assert set(by_id) == {"REQ-001", "REQ-002", "GLOBAL-REQ-001"}
    assert by_id["REQ-001"]["description"] == "Use Node.js for the backend."
    assert by_id["REQ-002"]["description"] == "Export the Express app for tests."
    assert all(item["title"] != "Requirements Register" for item in repaired["requirements"])


def test_requirement_coverage_accepts_operational_implement_language_on_documentation(tmp_path):
    source = project_solver.RequirementSource(
        path="requirements.md",
        requirements_text="REQ-002: Implement only the scoped job update supported by evidence.",
        requirement_lines=[],
        todo_lines=[],
        context_excerpt="",
    )
    register = {
        "requirements": [
            {
                "id": "REQ-002",
                "title": "Scoped job update",
                "description": "Implement only the scoped job update supported by evidence.",
                "type": "unspecified",
                "source": ["requirements.md"],
            }
        ]
    }

    coverage, missing = project_solver._build_requirement_coverage(
        [source],
        {
            "requirements.md": [
                {"path": "docs/rollout.md", "is_code": False, "note": "documented evidence"}
            ]
        },
        str(tmp_path),
        requirements_register=register,
    )

    assert missing == []
    assert coverage["requirements.md"]["requirements"][0]["status"] == "covered"


def test_requirement_coverage_keeps_code_artifact_requirements_strict(tmp_path):
    source = project_solver.RequirementSource(
        path="requirements.md",
        requirements_text="REQ-001: Implement the parser function.",
        requirement_lines=[],
        todo_lines=[],
        context_excerpt="",
    )
    register = {
        "requirements": [
            {
                "id": "REQ-001",
                "title": "Parser function",
                "description": "Implement the parser function.",
                "type": "functional",
                "source": ["requirements.md"],
            }
        ]
    }

    coverage, missing = project_solver._build_requirement_coverage(
        [source],
        {
            "requirements.md": [
                {"path": "docs/rollout.md", "is_code": False, "note": "documentation only"}
            ]
        },
        str(tmp_path),
        requirements_register=register,
    )

    assert missing == ["requirements.md"]
    assert coverage["requirements.md"]["requirements"][0]["status"] == "missing"


def test_explicit_source_requirement_ids_are_added_to_plan_references():
    plan = [{"type": "write_file", "path": "docs/rollout.md", "step": "Record the rollout."}]

    changed = project_solver._ensure_plan_requirement_refs(
        plan,
        payload_requirements=["REQ-001"],
        required_ids={"REQ-001", "REQ-002", "REQ-003"},
        global_ids=set(),
        sequence_ids=set(),
        all_ids={"REQ-001", "REQ-002", "REQ-003"},
        strict=True,
    )

    assert changed is True
    assert project_solver._extract_requirement_refs_from_plan(plan, ["REQ-001"]) >= {
        "REQ-001",
        "REQ-002",
        "REQ-003",
    }


def test_global_requirement_refs_are_advisory_unless_strict_mode_is_enabled():
    assert not project_solver._global_requirement_refs_required(False, False)
    # Strict source traceability must not promote generated global guidance to
    # a hard requirement; only the dedicated strict-global switch does that.
    assert not project_solver._global_requirement_refs_required(True, False)
    assert project_solver._global_requirement_refs_required(False, True)


def test_compiled_language_verification_keeps_native_build_phases(tmp_path):
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.15)\nproject(sample C)\nadd_executable(sample main.c)\n",
        encoding="utf-8",
    )
    steps = project_solver._select_verification_steps(
        str(tmp_path), {"languages": ["c"], "build_systems": ["cmake"]}, max_steps=2
    )
    commands = [step["command"] for step in steps]
    assert "cmake -S . -B build" in commands
    assert "cmake --build build" in commands


def test_kotlin_verification_uses_project_build_tool(tmp_path):
    (tmp_path / "Main.kt").write_text("fun main() = println(\"ok\")\n", encoding="utf-8")
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    steps = project_solver._select_verification_steps(
        str(tmp_path), {"languages": ["kotlin"], "build_systems": ["maven"]}, max_steps=2
    )
    assert any(step["command"] == "mvn test" for step in steps)
