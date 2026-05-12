from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_containerfile_uses_external_nmstt_runtime() -> None:
    containerfile = (PROJECT_ROOT / "Containerfile").read_text(encoding="utf-8")
    assert "FROM ${BASE_IMAGE} AS builder" in containerfile
    assert "FROM ${BASE_IMAGE} AS runtime" in containerfile
    assert ".refiner-build.json" in containerfile
    assert "ARG BUILD_NUMBER=0" in containerfile
    assert "ARG GIT_COMMIT=unknown" in containerfile
    assert "git rev-list --count HEAD" not in containerfile
    assert "rm -rf /src/.git" not in containerfile
    assert "RUST_BASE_IMAGE" not in containerfile
    assert "stt_rust" not in containerfile
    assert "refiner-stt" not in containerfile


def test_dockerignore_excludes_git_metadata() -> None:
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "\n.git\n" in f"\n{dockerignore}\n"


def test_entrypoint_full_mode_delegates_to_stack_launcher_help() -> None:
    entrypoint = PROJECT_ROOT / "container" / "entrypoint.sh"
    env = os.environ.copy()
    env["REFINER_JOB_DIR"] = str(PROJECT_ROOT / "job_data" / "test-entrypoint")
    completed = subprocess.run(
        [str(entrypoint), "full", "--help"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    combined = completed.stdout + completed.stderr
    assert "./scripts/start_refiner_stack.sh [--once] [--no-build] [--start-nmstt] [--help]" in combined
