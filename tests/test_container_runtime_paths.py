import json

from refiner import capabilities
import flask
import pytest


HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


def test_load_analysis_uses_bundled_report_when_runtime_source_is_absent(monkeypatch, tmp_path):
    bundled_report = {
        "generated_at": "2026-04-21 12:00:00",
        "root": "refiner",
        "files_scanned": 42,
        "api": {"total_routes": 3, "routes": [{"path": "/api/version", "methods": ["GET"]}]},
        "features": [{"id": "web_ui", "name": "Web control room"}],
    }
    report_path = tmp_path / "capabilities_report.json"
    report_path.write_text(json.dumps(bundled_report), encoding="utf-8")

    monkeypatch.setattr(
        capabilities,
        "analyse_repo",
        lambda root: {"generated_at": "runtime", "files_scanned": 0, "api": {"total_routes": 0, "routes": []}},
    )
    monkeypatch.setattr(capabilities, "_CAPABILITY_CACHE", {"ts": 0.0, "report": None})
    monkeypatch.setenv("REFINER_CAPABILITIES_REPORT_PATH", str(report_path))

    report = capabilities._load_analysis(force_refresh=True)

    assert report == bundled_report


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_resolve_python_script_path_prefers_bytecode_when_source_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(refiner_web, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(refiner_web, "PACKAGE_DIR", str(tmp_path))
    bytecode_path = tmp_path / "run_refiner.pyc"
    bytecode_path.write_bytes(b"")

    resolved = refiner_web._resolve_python_script_path("run_refiner.py")

    assert resolved == str(bytecode_path)
