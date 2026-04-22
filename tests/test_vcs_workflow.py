from refiner.vcs_workflow import run_vcs_workflow


def test_vcs_workflow_skips_non_repo(tmp_path):
    result = run_vcs_workflow(
        str(tmp_path),
        config={"enabled": True},
        version="v0",
        allow_run=False,
    )
    assert result.status == "skipped"
    assert result.details.get("reason") == "not a git repo"
