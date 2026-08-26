import subprocess

from refiner import refiner_web


class _Job:
    log_path = "/tmp/refiner-git-test/job.log"

    def __init__(self):
        self.logs = []

    def append_log(self, message):
        self.logs.append(message)


def test_git_commands_have_a_finite_configurable_timeout(monkeypatch):
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr="")

    monkeypatch.delenv("REFINER_GIT_COMMAND_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(refiner_web.subprocess, "run", fake_run)

    result = refiner_web.JobManager._git_run(
        object.__new__(refiner_web.JobManager),
        ["git", "status"],
        "/tmp",
        _Job(),
    )

    assert result.returncode == 0
    assert captured["timeout"] == 180.0


def test_git_timeout_is_reported_as_a_failed_command(monkeypatch):
    job = _Job()

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output=b"partial", stderr=b"nfs stall")

    monkeypatch.setenv("REFINER_GIT_COMMAND_TIMEOUT_SECONDS", "12")
    monkeypatch.setattr(refiner_web.subprocess, "run", fake_run)

    result = refiner_web.JobManager._git_run(
        object.__new__(refiner_web.JobManager),
        ["git", "clone"],
        "/tmp",
        job,
    )

    assert result.returncode == 124
    assert "timed out after 12.0s" in result.stderr
    assert any("timed out after 12.0s" in message for message in job.logs)
