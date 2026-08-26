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


def test_deterministic_rollback_reverts_exact_commit_without_rewriting_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (repo / "value.txt").write_text("known-good\n")
    git("add", "value.txt")
    git("commit", "-qm", "known good")
    (repo / "value.txt").write_text("degraded\n")
    git("add", "value.txt")
    git("commit", "-qm", "degraded rollout")
    degraded_sha = git("rev-parse", "HEAD").stdout.strip()

    manager = object.__new__(refiner_web.JobManager)
    manager._git_revert_commit(str(repo), degraded_sha, _Job())

    assert (repo / "value.txt").read_text() == "known-good\n"
    assert git("rev-parse", "HEAD^1").stdout.strip() == degraded_sha
    assert "Revert" in git("log", "-1", "--format=%s").stdout


def test_deterministic_rollback_rejects_non_commit_input(tmp_path):
    manager = object.__new__(refiner_web.JobManager)
    try:
        manager._git_revert_commit(str(tmp_path), "git status", _Job())
    except ValueError as exc:
        assert "hexadecimal git commit id" in str(exc)
    else:
        raise AssertionError("rollback accepted a shell expression")
