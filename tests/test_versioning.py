from __future__ import annotations

import versioning


def _clear_cache() -> None:
    versioning._cached_version_info.cache_clear()


def test_version_info_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("REFINER_VERSION_MAJOR", "3")
    monkeypatch.setenv("REFINER_VERSION_MINOR", "7")
    monkeypatch.setenv("REFINER_BUILD_NUMBER", "42")
    monkeypatch.setenv("GIT_COMMIT", "abcdef1234567890")
    monkeypatch.delenv("REFINER_VERSION", raising=False)
    monkeypatch.setattr(versioning, "_read_pyproject_version", lambda: "0.1.0")
    monkeypatch.setattr(versioning, "_read_build_info_file", lambda: {})
    monkeypatch.setattr(versioning, "_git_output", lambda *args: "")

    _clear_cache()
    info = versioning.get_version_info(refresh=True)

    assert info["version"] == "3.7.0042"
    assert info["build"] == "0042"
    assert info["build_number"] == 42
    assert info["commit_short"] == "abcdef12"
    assert info["source"] == "env"


def test_version_info_falls_back_to_git_commit_count(monkeypatch) -> None:
    monkeypatch.delenv("REFINER_VERSION_MAJOR", raising=False)
    monkeypatch.delenv("REFINER_VERSION_MINOR", raising=False)
    monkeypatch.delenv("REFINER_BUILD_NUMBER", raising=False)
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("REFINER_VERSION", raising=False)
    monkeypatch.setattr(versioning, "_read_pyproject_version", lambda: "1.4.0")
    monkeypatch.setattr(versioning, "_read_build_info_file", lambda: {})

    def fake_git(*args: str) -> str:
        if args == ("rev-list", "--count", "HEAD"):
            return "321"
        if args == ("rev-parse", "HEAD"):
            return "1234567890abcdef1234567890abcdef12345678"
        return ""

    monkeypatch.setattr(versioning, "_git_output", fake_git)

    _clear_cache()
    info = versioning.get_version_info(refresh=True)

    assert info["version"] == "1.4.0321"
    assert info["build"] == "0321"
    assert info["build_number"] == 321
    assert info["commit_short"] == "12345678"
    assert info["source"] == "git"


def test_version_info_uses_baked_build_file_when_git_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("REFINER_VERSION_MAJOR", raising=False)
    monkeypatch.delenv("REFINER_VERSION_MINOR", raising=False)
    monkeypatch.delenv("REFINER_BUILD_NUMBER", raising=False)
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("REFINER_VERSION", raising=False)
    monkeypatch.setattr(versioning, "_read_pyproject_version", lambda: "2.1.0")
    monkeypatch.setattr(
        versioning,
        "_read_build_info_file",
        lambda: {"build_number": 88, "commit": "feedfacecafebeef1234"},
    )
    monkeypatch.setattr(versioning, "_git_output", lambda *args: "")

    _clear_cache()
    payload = versioning.get_public_version_info(refresh=True)

    assert payload["version"] == "2.1.0088"
    assert payload["build"] == "0088"
    assert payload["build_number"] == 88
    assert payload["commit"] == "feedface"
    assert payload["source"] == "build_file"
