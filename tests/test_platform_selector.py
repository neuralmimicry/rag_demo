from refiner.platform_selector import select_platform


def test_platform_selector_defaults_to_local(tmp_path, monkeypatch):
    def fake_tools():
        return {
            "qemu": False,
            "podman": False,
            "docker": False,
            "kubectl": False,
            "oc": False,
            "gcloud": False,
            "aws": False,
            "az": False,
        }

    monkeypatch.setattr("platform_selector._detect_tools", fake_tools)
    selection = select_platform(str(tmp_path), {})
    assert selection.tier == "local"
    assert selection.available is True


def test_platform_selector_prefers_container_when_dockerfile(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")

    def fake_tools():
        return {
            "qemu": False,
            "podman": True,
            "docker": False,
            "kubectl": False,
            "oc": False,
            "gcloud": False,
            "aws": False,
            "az": False,
        }

    monkeypatch.setattr("platform_selector._detect_tools", fake_tools)
    selection = select_platform(str(tmp_path), {})
    assert selection.tier == "container"
    assert selection.engine == "podman"
    assert selection.available is True
