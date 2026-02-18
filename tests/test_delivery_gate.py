import json
from delivery_pipeline import run_delivery_pipeline


def test_delivery_gate_blocks_deploy_stage(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "README.md").write_text("demo", encoding="utf-8")

    pipeline_cfg = {
        "solver_gate": "block_deploy",
        "stages": [
            {"name": "sandbox", "kind": "test", "commands": []},
            {"name": "deploy", "kind": "deploy", "commands": []},
        ],
    }
    config_path = project_root / "pipeline.json"
    config_path.write_text(json.dumps(pipeline_cfg), encoding="utf-8")

    solver_report = {
        "completion_summary": {"needs_more_iterations": True},
        "solver_workspace": "project_solver_output"
    }
    solver_path = project_root / "project_solution.json"
    solver_path.write_text(json.dumps(solver_report), encoding="utf-8")

    report_path = tmp_path / "report.json"
    exit_code = run_delivery_pipeline(
        str(project_root),
        config_path=str(config_path),
        output_path=str(report_path),
        allow_run=True,
        project_solution_path=str(solver_path),
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["status"] == "blocked"
    assert report["stages"][0]["status"] in {"ok", "no_op", "planned"}
    assert report["stages"][1]["status"] == "blocked"
