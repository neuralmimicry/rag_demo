import json

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_job_detail_includes_solver_replay_analysis(monkeypatch, tmp_path):
    output_path = tmp_path / "solver_output.json"
    output_path.write_text(
        json.dumps(
            {
                "completion_summary": {
                    "needs_more_iterations": True,
                    "steps_applied": 12,
                    "max_steps_reached": False,
                },
                "solver_replay_analysis": {
                    "window": {"episodes_analyzed": 5, "sources_analyzed": 2, "limit": 60},
                    "outcomes": {"failure": 2, "success": 3},
                    "recommendations": ["Review req/a.md first."],
                    "sources_needing_attention": [
                        {
                            "source_path": "req/a.md",
                            "recent_non_successes": 2,
                            "last_outcome": "failure",
                            "last_iteration": 3,
                            "last_summary": "Verification kept failing.",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    job = refiner_web.Job(
        job_id="job-replay-1",
        payload={"workflow": "project_solver"},
        project_name="Replay Demo",
        owner="integration_tester",
        status="completed",
        output_paths={"primary": str(output_path)},
    )

    monkeypatch.setattr(refiner_web, "_current_user", lambda: "integration_tester")
    monkeypatch.setattr(refiner_web, "_can_view_job", lambda user, current_job: True)
    monkeypatch.setattr(refiner_web, "_is_admin_user", lambda user: True)
    monkeypatch.setattr(refiner_web, "_redact_log_entries", lambda logs, is_admin: logs)
    monkeypatch.setattr(refiner_web, "_augment_job_dict_for_user", lambda data, user, current_job: data)
    monkeypatch.setattr(refiner_web.manager, "get_job", lambda job_id: job)

    with refiner_web.app.test_client() as client:
        response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data["completion_summary"]["needs_more_iterations"] is True
    assert data["solver_replay_analysis"]["window"]["episodes_analyzed"] == 5
    assert data["solver_replay_analysis"]["recommendations"] == ["Review req/a.md first."]
