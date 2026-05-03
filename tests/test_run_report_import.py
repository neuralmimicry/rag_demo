import importlib
import sys
import types


def test_run_refiner_import_and_attr():
    # Ensure importing CLI module does not execute workflow and exposes run
    from refiner import run_refiner
    assert hasattr(run_refiner, "run")


def test_run_topic_research_uses_config_loader_not_main(monkeypatch, tmp_path):
    from refiner import config_loader
    from refiner import credentials
    source_file = tmp_path / "topic.txt"
    source_file.write_text("Topic: Test", encoding="utf-8")
    output_file = tmp_path / "output.md"

    calls = {}

    fake_topic_researcher = types.ModuleType("refiner.topic_researcher")

    class DummyTopicResearcher:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def run(self, source, output, max_iterations=10, context_sources=None, references_path=None):
            calls["run"] = {
                "source": source,
                "output": output,
                "max_iterations": max_iterations,
                "context_sources": context_sources,
                "references_path": references_path,
            }
            with open(output, "w", encoding="utf-8") as handle:
                handle.write("# Stub Research\n")

    fake_topic_researcher.TopicResearcher = DummyTopicResearcher
    monkeypatch.setitem(sys.modules, "refiner.topic_researcher", fake_topic_researcher)

    broken_main = types.ModuleType("main")

    def _unexpected_main_access(name):
        raise AssertionError(f"unexpected main import access: {name}")

    broken_main.__getattr__ = _unexpected_main_access
    monkeypatch.setitem(sys.modules, "main", broken_main)

    sys.modules.pop("refiner.run_refiner", None)
    run_refiner = importlib.import_module("refiner.run_refiner")

    monkeypatch.setattr(
        config_loader,
        "load_config",
        lambda path="config.json": {
            "instances": [{"jira_url": "https://example.atlassian.net", "name": "Example Co"}],
            "llm_providers": [
                {"name": "OpenAIPrimary", "type": "openai", "model": "gpt-4o-mini"},
                {"name": "GeminiFallback", "type": "gemini", "model": "gemini-2.5-flash"},
            ],
            "search_engines": [],
        },
    )
    def fake_get_credentials(instance_name=None):
        calls["instance_name"] = instance_name
        return ("user", "token")

    monkeypatch.setattr(credentials, "get_credentials", fake_get_credentials)
    monkeypatch.setattr(
        credentials,
        "get_llm_credentials",
        lambda name=None, provider_type="openai": f"{provider_type}:{name or 'default'}",
    )

    rc = run_refiner._run_topic_research(str(source_file), str(output_file), max_iterations=1)

    assert rc == 0
    assert output_file.read_text(encoding="utf-8") == "# Stub Research\n"
    assert calls["init"]["jira_base_url"] == "https://example.atlassian.net"
    assert calls["init"]["jira_auth"] == ("user", "token")
    assert calls["init"]["company_name"] == "Example Co"
    assert calls["init"]["llm_provider"] == "openai"
    assert calls["init"]["llm_model"] == "gpt-4o-mini"
    assert calls["init"]["llm_api_key"] == "openai:OpenAIPrimary"
    assert calls["init"]["fallback_llm_provider"] == "gemini"
    assert calls["init"]["fallback_llm_model"] == "gemini-2.5-flash"
    assert calls["init"]["fallback_llm_api_key"] == "gemini:GeminiFallback"
    assert calls["instance_name"] == "Example Co"
    assert calls["run"]["source"] == str(source_file)
    assert calls["run"]["output"] == str(output_file)
    assert calls["run"]["max_iterations"] == 1
