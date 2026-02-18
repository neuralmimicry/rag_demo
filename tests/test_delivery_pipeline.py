import json

from delivery_pipeline import run_delivery_pipeline


def _write_config(path, config):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle)


def test_delivery_pipeline_blocks_without_approval(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "README.md").write_text("demo", encoding="utf-8")

    config = {
        "output_dir": "delivery_pipeline_output",
        "stages": [
            {
                "name": "staging",
                "workspace_mode": "copy",
                "requires_approval": True,
                "commands": []
            }
        ]
    }
    config_path = project_root / "pipeline.json"
    _write_config(config_path, config)

    report_path = tmp_path / "report.json"
    exit_code = run_delivery_pipeline(
        str(project_root),
        config_path=str(config_path),
        output_path=str(report_path),
        allow_run=True,
    )

    assert exit_code == 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["stages"][0]["status"] == "blocked"


def test_delivery_pipeline_dry_run(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "README.md").write_text("demo", encoding="utf-8")

    config = {
        "output_dir": "delivery_pipeline_output",
        "stages": [
            {
                "name": "sandbox",
                "workspace_mode": "copy",
                "commands": ["echo test"]
            }
        ]
    }
    config_path = project_root / "pipeline.json"
    _write_config(config_path, config)

    report_path = tmp_path / "report.json"
    exit_code = run_delivery_pipeline(
        str(project_root),
        config_path=str(config_path),
        output_path=str(report_path),
        allow_run=False,
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "planned"
    assert report["stages"][0]["status"] == "planned"
