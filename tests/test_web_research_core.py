import json

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

    def json(self):
        return json.loads(self.text or self.content.decode(self.encoding or "utf-8"))


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
    assert "Connection" not in headers_list[0]


def test_extract_youtube_video_id_supports_common_url_shapes():
    assert wr.extract_youtube_video_id("https://www.youtube.com/watch?v=VTtC8tAzsOo") == "VTtC8tAzsOo"
    assert wr.extract_youtube_video_id("https://youtu.be/VTtC8tAzsOo?t=12") == "VTtC8tAzsOo"
    assert wr.extract_youtube_video_id("https://www.youtube.com/shorts/VTtC8tAzsOo") == "VTtC8tAzsOo"
    assert wr.extract_youtube_video_id("https://example.com/watch?v=VTtC8tAzsOo") == ""


def test_parse_youtube_json3_transcript_flattens_segments():
    payload = {
        "events": [
            {"segs": [{"utf8": "Hello"}, {"utf8": " world"}]},
            {"segs": [{"utf8": "Another"}, {"utf8": " line"}]},
        ]
    }
    assert wr.parse_youtube_json3_transcript(payload) == "Hello world\nAnother line"


def test_fetch_youtube_transcript_parses_json3_payload():
    payload = {"events": [{"segs": [{"utf8": "Transcript"}, {"utf8": " text"}]}]}

    class _Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            raw = json.dumps(payload)
            return _Response(
                status_code=200,
                text=raw,
                content=raw.encode("utf-8"),
                headers={"content-type": "application/json"},
            )

    session = _Session()
    transcript, metadata = wr.fetch_youtube_transcript(
        "https://youtu.be/VTtC8tAzsOo",
        timeout=5,
        session=session,
    )

    assert transcript == "Transcript text"
    assert metadata["video_id"] == "VTtC8tAzsOo"
    assert metadata["caption_lang"] == "en"
    assert session.calls[0][0] == "https://www.youtube.com/api/timedtext"


def test_fetch_url_content_uses_youtube_transcript(monkeypatch):
    monkeypatch.setattr(
        wr,
        "fetch_youtube_transcript",
        lambda *a, **k: ("Transcript body", {"source_type": "youtube_transcript"}),
    )
    monkeypatch.setattr(wr, "fetch_url", lambda *a, **k: (_ for _ in ()).throw(AssertionError("generic fetch should not run")))

    content = wr.fetch_url_content(
        "https://www.youtube.com/watch?v=VTtC8tAzsOo",
        timeout=5,
        max_bytes=1024,
        file_converter=None,
    )
    assert content == "Transcript body"


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
