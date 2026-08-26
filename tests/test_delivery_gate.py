import json
import refiner.delivery_pipeline as delivery_pipeline
import refiner.workflows.delivery.github_actions as github_actions
from refiner.delivery_pipeline import run_delivery_pipeline


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


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


def test_delivery_gate_blocks_production_rollout_without_successful_github_actions(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "README.md").write_text("demo", encoding="utf-8")

    pipeline_cfg = {
        "github_actions": {
            "enabled": True,
            "require_success": True,
            "owner": "neuralmimicry",
            "repo": "demo",
            "branch": "main",
            "workflow_file": "ci.yml",
        },
        "stages": [
            {"name": "deploy", "kind": "deploy", "commands": []},
        ],
    }
    config_path = project_root / "pipeline.json"
    config_path.write_text(json.dumps(pipeline_cfg), encoding="utf-8")

    monkeypatch.setattr(
        delivery_pipeline,
        "_github_actions_report",
        lambda *args, **kwargs: {
            "enabled": True,
            "required": True,
            "checked": True,
            "workflow_file": "ci.yml",
            "owner": "neuralmimicry",
            "repo": "demo",
            "branch": "main",
            "succeeded": False,
            "reason": "GitHub Actions workflow ci.yml concluded with failure for neuralmimicry/demo on branch main",
            "run": {"conclusion": "failure"},
        },
    )

    report_path = tmp_path / "report.json"
    exit_code = run_delivery_pipeline(
        str(project_root),
        config_path=str(config_path),
        output_path=str(report_path),
        allow_run=True,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["status"] == "blocked"
    assert report["github_actions"]["succeeded"] is False
    assert report["stages"][0]["status"] == "blocked"


def test_delivery_gate_allows_production_rollout_with_successful_github_actions(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "README.md").write_text("demo", encoding="utf-8")

    pipeline_cfg = {
        "github_actions": {
            "enabled": True,
            "require_success": True,
            "owner": "neuralmimicry",
            "repo": "demo",
            "branch": "main",
            "workflow_file": "ci.yml",
        },
        "stages": [
            {"name": "deploy", "kind": "deploy", "commands": []},
        ],
    }
    config_path = project_root / "pipeline.json"
    config_path.write_text(json.dumps(pipeline_cfg), encoding="utf-8")

    monkeypatch.setattr(
        delivery_pipeline,
        "_github_actions_report",
        lambda *args, **kwargs: {
            "enabled": True,
            "required": True,
            "checked": True,
            "workflow_file": "ci.yml",
            "owner": "neuralmimicry",
            "repo": "demo",
            "branch": "main",
            "succeeded": True,
            "reason": "GitHub Actions workflow ci.yml succeeded for neuralmimicry/demo on branch main",
            "run": {"conclusion": "success"},
        },
    )

    report_path = tmp_path / "report.json"
    exit_code = run_delivery_pipeline(
        str(project_root),
        config_path=str(config_path),
        output_path=str(report_path),
        allow_run=True,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["status"] == "success"
    assert report["github_actions"]["succeeded"] is True
    assert report["stages"][0]["status"] == "no_op"


def test_github_actions_gate_matches_the_uploaded_commit(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "README.md").write_text("demo", encoding="utf-8")
    expected_sha = "a" * 40

    monkeypatch.setattr(
        delivery_pipeline.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(
            {
                "workflow_runs": [
                    {"status": "completed", "conclusion": "success", "head_sha": "b" * 40},
                    {"status": "completed", "conclusion": "success", "head_sha": expected_sha},
                ]
            }
        ),
    )

    report = delivery_pipeline._github_actions_report(
        str(project_root),
        {
            "enabled": True,
            "require_success": True,
            "owner": "neuralmimicry",
            "repo": "demo",
            "branch": "main",
            "workflow_file": "ci.yml",
            "wait_timeout_sec": 0,
        },
        True,
        expected_sha=expected_sha,
    )

    assert report["succeeded"] is True
    assert report["expected_sha"] == expected_sha
    assert report["run"]["head_sha"] == expected_sha


def test_repository_build_gate_discovers_and_requires_matching_workflow(tmp_path, monkeypatch):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build-and-release.yml").write_text("name: build\n", encoding="utf-8")
    expected_sha = "c" * 40

    monkeypatch.setattr(
        github_actions.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(
            {"workflow_runs": [{"status": "completed", "conclusion": "success", "head_sha": expected_sha}]}
        ),
    )

    report = github_actions.verify_repository_builds(
        workspace=str(tmp_path),
        owner="neuralmimicry",
        repo="demo",
        branch="refiner/test",
        commit_sha=expected_sha,
        token="test-token",
        timeout_sec=0,
    )

    assert report["enabled"] is True
    assert report["succeeded"] is True
    assert report["workflows"][0]["run"]["head_sha"] == expected_sha
