from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_containerfile_builds_managed_stt_runtime() -> None:
    containerfile = (PROJECT_ROOT / "Containerfile").read_text(encoding="utf-8")
    assert "FROM ${RUST_BASE_IMAGE} AS stt-builder" in containerfile
    assert "FROM ${BASE_IMAGE} AS source-metadata" in containerfile
    assert "cargo build --locked --release" in containerfile
    assert "stt_rust/target/release/refiner-stt" in containerfile
    assert ".refiner-build.json" in containerfile
    assert "git rev-list --count HEAD" in containerfile


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
    assert "./scripts/start_refiner_stack.sh [--once] [--no-build] [--help]" in combined
