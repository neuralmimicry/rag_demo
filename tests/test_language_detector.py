from language_detector import detect_languages


def test_language_detector_python_and_go(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module demo\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")

    info = detect_languages(str(tmp_path))
    assert "python" in info["languages"]
    assert "go" in info["languages"]
    assert "go" in info["build_systems"]
