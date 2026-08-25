import sys

from refiner.solver_command_policy import evaluate_command_policy
from refiner.workflows.solver import project_solver
from refiner.workflows.solver.reliability import canonical_project_target, plan_fingerprint
from refiner.workflows.solver.starter_templates import build_starter_template_context


def test_structured_argv_is_shell_free_and_preserves_literal_arguments():
    decision = evaluate_command_policy(["node", "script.js", "value && not shell syntax"])

    assert decision.allowed is True
    assert decision.argv == ["node", "script.js", "value && not shell syntax"]
    assert decision.command.startswith("node script.js")


def test_canonical_project_target_keeps_generated_node_files_at_project_root(tmp_path):
    project_root = tmp_path / "project"
    workspace = project_root / "project_solver_output"
    workspace.mkdir(parents=True)

    target, note = canonical_project_target(
        "server.js",
        project_root=str(project_root),
        workspace_root=str(workspace),
    )

    assert target == "server.js"
    assert note and "canonical project-root" in note.lower()
    resolved, _ = project_solver._resolve_file_target(
        "server.js",
        project_root=str(project_root),
        workspace_root=str(workspace),
        step_type="write_file",
        prefer_workspace_new_files=True,
    )
    assert resolved == "server.js"


def test_canonical_project_target_does_not_hide_path_traversal():
    target, note = canonical_project_target(
        "../outside.js",
        project_root="/tmp/project",
        workspace_root="/tmp/project/project_solver_output",
    )

    assert target == "../outside.js"
    assert note is None


def test_node_project_without_test_script_gets_syntax_acceptance_checks(tmp_path):
    (tmp_path / "server.js").write_text("console.log('ok');\n", encoding="utf-8")

    steps = project_solver._select_verification_steps(
        str(tmp_path),
        {"languages": ["node"], "build_systems": []},
    )

    assert steps
    assert steps[0]["command"] == "node --check server.js"


def test_replace_in_file_rejects_unexpected_match_count(tmp_path):
    path = tmp_path / "sample.js"
    path.write_text("const value = 1;\nconst value = 1;\n", encoding="utf-8")
    failures = []
    actions = []

    project_solver._apply_step(
        str(tmp_path),
        {
            "type": "replace_in_file",
            "path": "sample.js",
            "find": "const value = 1;",
            "replace": "const value = 2;",
            "expected_count": 1,
        },
        allow_run=False,
        actions_log=actions,
        replace_failures=failures,
    )

    assert path.read_text(encoding="utf-8").count("value = 1") == 2
    assert failures[0]["issue"] == "unexpected_match_count"


def test_structured_argv_routes_workspace_file_and_preserves_execution(tmp_path):
    project_root = tmp_path / "project"
    workspace = project_root / "project_solver_output"
    workspace.mkdir(parents=True)
    (workspace / "check.py").write_text("print('ok')\n", encoding="utf-8")
    actions = []
    failures = []
    executed = []

    project_solver._apply_step(
        str(project_root),
        {
            "type": "run_command",
            "argv": [sys.executable, "project_solver_output/check.py"],
            "workdir": ".",
        },
        allow_run=True,
        actions_log=actions,
        allowed_roots=[str(workspace)],
        failure_log=failures,
        workspace_root=str(workspace),
        executed_commands=executed,
    )

    assert failures == []
    assert executed
    assert any("check.py" in command for command in executed)


def test_starter_template_context_is_only_emitted_for_incomplete_shape(tmp_path):
    (tmp_path / "server.js").write_text("", encoding="utf-8")

    context = build_starter_template_context(
        str(tmp_path), {"languages": ["node"], "build_systems": []}
    )

    assert "node-web" in context


def test_plan_fingerprint_is_stable_for_equivalent_steps():
    first = [{"type": "run_command", "argv": ["node", "--check", "server.js"]}]
    second = [{"argv": ["node", "--check", "server.js"], "type": "run_command"}]

    assert plan_fingerprint(first) == plan_fingerprint(second)
