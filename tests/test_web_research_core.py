import web_research as wr


class _Response:
    def __init__(self, status_code=200, *, reason="OK", text="", content=b"", headers=None, encoding="utf-8"):
        self.status_code = status_code
        self.reason = reason
        self.text = text
        self.content = content
        self.headers = headers or {}
        self.encoding = encoding

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_fetch_url_uses_advice_and_preserves_source_headers():
    calls = []

    class _Session:
        def __init__(self):
            self._responses = [
                _Response(status_code=403, reason="Forbidden"),
                _Response(status_code=403, reason="Forbidden"),
                _Response(status_code=200, text="ok", headers={"content-type": "text/plain"}),
            ]

        def get(self, url, **kwargs):
            calls.append(kwargs)
            return self._responses.pop(0)

    headers_list = [
        {"User-Agent": "UA-1"},
        {"User-Agent": "UA-2"},
    ]

    resp = wr.fetch_url(
        "https://example.com",
        timeout=5,
        session=_Session(),
        headers_list=headers_list,
        get_fetch_advice=lambda _u, _err: {"headers": {"User-Agent": "Special UA"}, "cookies": {}, "params": {}},
    )
    assert resp.status_code == 200
    assert calls[-1]["headers"]["User-Agent"] == "Special UA"
    # Ensure caller-provided header templates were not mutated in-place.
    assert "Connection" not in headers_list[0]


def test_fetch_url_content_binary_without_converter_returns_empty(monkeypatch):
    monkeypatch.setattr(
        wr,
        "fetch_url",
        lambda *a, **k: _Response(
            status_code=200,
            content=b"%PDF-1.7 ...",
            headers={"content-type": "application/pdf"},
        ),
    )
    content = wr.fetch_url_content(
        "https://example.com/file.pdf",
        timeout=5,
        max_bytes=1024,
        file_converter=None,
    )
    assert content == ""


def test_search_web_deduplicates_and_uses_cache(tmp_path):
    calls = {"count": 0}

    class _Engine(wr.SearchEngine):
        def search(self, query):
            calls["count"] += 1
            return [
                {"title": "A", "snippet": "x", "url": "https://example.com/a"},
                {"title": "B", "snippet": "x", "url": "https://example.com/a"},
            ]

    cache = wr.WebResearchCache(str(tmp_path), namespace="test")
    result1 = wr.search_web([_Engine()], "  query  ", max_results=10, cache=cache, cache_ttl_hours=24)
    result2 = wr.search_web([_Engine()], "query", max_results=10, cache=cache, cache_ttl_hours=24)
    assert len(result1) == 1
    assert result2 == result1
    assert calls["count"] == 1
