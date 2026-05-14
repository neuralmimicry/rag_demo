import json

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_completion_reason_from_summary_prioritizes_steps():
    summary = {
        "needs_more_iterations": True,
        "max_steps_reached": True,
        "iterations_exhausted_sources": [],
    }
    assert refiner_web._completion_reason_from_summary(summary) == "steps"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_completion_reason_from_output_flags_incomplete_without_step_or_iteration_limit(tmp_path):
    output_path = tmp_path / "solver_output.json"
    output_path.write_text(
        json.dumps(
            {
                "completion_summary": {
                    "needs_more_iterations": True,
                    "max_steps_reached": False,
                    "iterations_exhausted_sources": [],
                }
            }
        ),
        encoding="utf-8",
    )

    assert refiner_web._completion_reason_from_output(str(output_path)) == "incomplete"
