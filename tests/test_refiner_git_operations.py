import subprocess
from types import SimpleNamespace

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


def test_repo_input_accepts_conductor_ssh_and_github_shorthand():
    parse = refiner_web.JobManager._parse_repo_input

    assert parse("git@github.com:neuralmimicry/conductor.git") == ("neuralmimicry", "conductor")
    assert parse("github.com/neuralmimicry/conductor") == ("neuralmimicry", "conductor")
    assert parse("https://github.com/neuralmimicry/conductor.git/") == ("neuralmimicry", "conductor")


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


def test_rollback_expected_head_rejects_a_concurrent_branch_update(monkeypatch):
    manager = object.__new__(refiner_web.JobManager)
    job = _Job()
    job.payload = {
        "rollback_commit": "a" * 40,
        "rollback_expected_head": "b" * 40,
    }
    calls = []

    def fake_git_run(command, cwd, job):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="c" * 40 + "\n", stderr="")

    monkeypatch.setattr(manager, "_git_run", fake_git_run)
    try:
        # Exercise the same guard through the small helper used by repo prep.
        manager._assert_rollback_head("/tmp", job)
    except ValueError as exc:
        assert "rollback branch changed" in str(exc)
    else:
        raise AssertionError("stale rollback branch was accepted")
    assert calls == [["git", "rev-parse", "HEAD"]]


def test_repo_finalization_uses_git_prefix_for_post_push_sha_lookup(monkeypatch):
    manager = object.__new__(refiner_web.JobManager)
    job = SimpleNamespace(
        job_id="12345678-job",
        log_path="/tmp/refiner-git-test/job.log",
        payload={"project_run": True},
        repo_info={
            "owner": "source-owner",
            "repo": "source-repo",
            "fork_org": "neuralmimicry",
            "fork_repo": "fork-repo",
            "workspace": "/tmp/refiner-git-test/repo",
            "branch": "refiner/test",
        },
        logs=[],
    )
    job.append_log = job.logs.append
    commands = []

    def fake_git_run(command, cwd, git_job, token=None):
        commands.append(command)
        assert command[0] == "git"
        stdout = "base-sha\n" if len(commands) == 1 else "commit-sha\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(manager, "_git_has_changes", lambda workspace, git_job: True)
    monkeypatch.setattr(manager, "_git_run", fake_git_run)
    monkeypatch.setattr(manager, "_git_config", lambda *args: None)
    monkeypatch.setattr(manager, "_git_add_all", lambda *args: None)
    monkeypatch.setattr(manager, "_git_commit", lambda *args: None)
    monkeypatch.setattr(manager, "_git_push", lambda *args: None)
    monkeypatch.setattr(manager, "_get_github_token", lambda git_job: "test-token")
    monkeypatch.setattr(
        refiner_web,
        "verify_repository_builds",
        lambda **kwargs: {"enabled": False},
    )

    manager._finalize_repo(job)

    assert commands == [
        ["git", "rev-parse", "HEAD"],
        ["git", "rev-parse", "HEAD"],
    ]
    assert job.repo_info["base_commit_sha"] == "base-sha"
    assert job.repo_info["commit_sha"] == "commit-sha"


def test_git_push_reuses_deterministic_restart_branch_with_lease(monkeypatch):
    manager = object.__new__(refiner_web.JobManager)
    job = _Job()
    commands = []

    def fake_git_run(command, cwd, git_job=None, token=None, job=None):
        commands.append(command)
        if command[1:3] == ["ls-remote", "origin"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="remote-sha\trefs/heads/refiner/restarted\n",
                stderr="",
            )
        if command[1:3] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="local-sha\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(manager, "_git_run", fake_git_run)
    manager._git_push("/tmp/refiner-git-test/repo", "refiner/restarted", "test-token", job)

    assert commands == [
        ["git", "ls-remote", "origin", "refs/heads/refiner/restarted"],
        ["git", "rev-parse", "HEAD"],
        [
            "git",
            "push",
            "--force-with-lease=refs/heads/refiner/restarted:remote-sha",
            "origin",
            "HEAD:refiner/restarted",
        ],
    ]
