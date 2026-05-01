import json
from unittest.mock import MagicMock, patch

from refiner import project_solver
from refiner import web_research as wr
from refiner.topic_researcher import (
    BraveSearchEngine,
    DuckDuckGoSearchEngine,
    TavilySearchEngine,
    TopicResearcher,
)


class _Response:
    def __init__(self, status_code=200, *, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_duckduckgo_search_engine_parses_html_results():
    html = """
    <div class="result">
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Example Result</a>
      <a class="result__snippet">Snippet text</a>
    </div>
    """
    engine = wr.DuckDuckGoSearchEngine(timeout=5, max_results=5)

    with patch("refiner.web_research.requests.post", return_value=_Response(status_code=200, text=html)):
        results = engine.search("example query")

    assert results == [
        {
            "title": "Example Result",
            "snippet": "Snippet text",
            "url": "https://example.com/a",
        }
    ]


def test_brave_search_engine_normalizes_results():
    engine = wr.BraveSearchEngine("brave-key", timeout=5, max_results=5)
    payload = {
        "web": {
            "results": [
                {"title": "Brave Result", "description": "Brave snippet", "url": "https://example.com/brave"}
            ]
        }
    }

    with patch("refiner.web_research.requests.get", return_value=_Response(status_code=200, payload=payload)):
        results = engine.search("example query")

    assert results == [
        {
            "title": "Brave Result",
            "snippet": "Brave snippet",
            "url": "https://example.com/brave",
        }
    ]


def test_tavily_search_engine_normalizes_results():
    engine = wr.TavilySearchEngine("tavily-key", timeout=5, max_results=5)
    payload = {
        "results": [
            {"title": "Tavily Result", "content": "Tavily snippet", "url": "https://example.com/tavily"}
        ]
    }

    with patch("refiner.web_research.requests.post", return_value=_Response(status_code=200, payload=payload)):
        results = engine.search("example query")

    assert results == [
        {
            "title": "Tavily Result",
            "snippet": "Tavily snippet",
            "url": "https://example.com/tavily",
        }
    ]


def test_search_web_cache_key_respects_provider_mix(tmp_path):
    calls = {"alpha": 0, "beta": 0}

    class _Alpha(wr.SearchEngine):
        def provider_id(self):
            return "alpha"

        def search(self, query):
            calls["alpha"] += 1
            return [{"title": "A", "snippet": query, "url": "https://example.com/a"}]

    class _Beta(wr.SearchEngine):
        def provider_id(self):
            return "beta"

        def search(self, query):
            calls["beta"] += 1
            return [{"title": "B", "snippet": query, "url": "https://example.com/b"}]

    cache = wr.WebResearchCache(str(tmp_path), namespace="providers")
    first = wr.search_web([_Alpha()], "query", max_results=5, cache=cache, cache_ttl_hours=24)
    second = wr.search_web([_Alpha()], "query", max_results=5, cache=cache, cache_ttl_hours=24)
    third = wr.search_web([_Beta()], "query", max_results=5, cache=cache, cache_ttl_hours=24)

    assert first == second
    assert third != first
    assert calls["alpha"] == 1
    assert calls["beta"] == 1


def test_topic_researcher_supports_multi_provider_search_configs():
    mock_llm = MagicMock()
    mock_llm.get_context_window.return_value = 8192
    mock_llm.predict.return_value.text = ""

    with patch("refiner.topic_researcher.get_provider", return_value=mock_llm):
        researcher = TopicResearcher(
            jira_base_url="https://test.atlassian.net",
            jira_auth=("user", "token"),
            llm_provider="openai",
            search_configs=[
                {"type": "duckduckgo"},
                {"type": "brave", "api_key": "brave-key"},
                {"type": "tavily", "api_key": "tavily-key"},
            ],
        )

    assert any(isinstance(engine, DuckDuckGoSearchEngine) for engine in researcher.search_engines)
    assert any(isinstance(engine, BraveSearchEngine) for engine in researcher.search_engines)
    assert any(isinstance(engine, TavilySearchEngine) for engine in researcher.search_engines)


def test_project_solver_load_search_engines_supports_multi_provider(tmp_path, monkeypatch):
    config = {
        "search_engines": [
            {"type": "duckduckgo"},
            {"type": "brave", "api_key": "brave-key"},
        ]
    }
    config_path = tmp_path / "solver-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("SOLVER_CONFIG_PATH", str(config_path))

    engines = project_solver._load_search_engines(str(tmp_path), timeout=10, cache_ttl_hours=24)

    provider_ids = {engine.provider_id() for engine in engines}
    assert "duckduckgo" in provider_ids
    assert "brave" in provider_ids
