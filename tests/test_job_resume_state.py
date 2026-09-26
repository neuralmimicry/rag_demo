import queue
import threading

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Job resume tests require real Flask runtime")
def test_resume_clears_terminal_metadata_before_requeue(monkeypatch, tmp_path):
    monkeypatch.setattr(refiner_web, "JOB_ROOT", str(tmp_path))
    monkeypatch.setattr(refiner_web, "_central_job_store", lambda: None)
    monkeypatch.setattr(refiner_web, "_notify_continuum_autoscaler", lambda: None)

    job = refiner_web.Job(job_id="resume-terminal-state", payload={})
    job.status = "failed"
    job.finished_at = "2026-09-26T06:24:00Z"
    job.exit_code = 2
    persisted_states = []
    monkeypatch.setattr(
        job,
        "persist",
        lambda force=False: persisted_states.append((job.status, job.finished_at, job.exit_code)),
    )

    manager = refiner_web.JobManager.__new__(refiner_web.JobManager)
    manager.jobs = {job.job_id: job}
    manager.lock = threading.Lock()
    manager.queue = queue.Queue()

    assert manager.resume_job(job.job_id)

    assert job.status == "queued"
    assert job.finished_at is None
    assert job.exit_code is None
    assert persisted_states == [("queued", None, None)]
    assert manager.queue.get_nowait() == job.job_id
