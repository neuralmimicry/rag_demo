from refiner.language_detector import detect_languages


def test_language_detector_python_and_go(tmp_path):
    (tmp_path / "refiner.main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module demo\n", encoding="utf-8")
    (tmp_path / "refiner.main.go").write_text("package main\n", encoding="utf-8")

    info = detect_languages(str(tmp_path))
    assert "python" in info["languages"]
    assert "go" in info["languages"]
    assert "go" in info["build_systems"]


def test_language_detector_kotlin_gradle_and_maven_markers(tmp_path):
    (tmp_path / "Main.kt").write_text("fun main() = println(\"ok\")\n", encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text(
        "plugins { kotlin(\"jvm\") version \"2.0.0\" }\n", encoding="utf-8"
    )
    info = detect_languages(str(tmp_path))
    assert "kotlin" in info["languages"]
    assert "gradle" in info["build_systems"]

    (tmp_path / "build.gradle.kts").unlink()
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    info = detect_languages(str(tmp_path))
    assert "maven" in info["build_systems"]
