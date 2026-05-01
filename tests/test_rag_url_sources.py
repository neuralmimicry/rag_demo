import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_coerce_rag_sources_accepts_strings_and_urls():
    sources = refiner_web._coerce_rag_sources(
        {
            "sources": ["https://youtu.be/VTtC8tAzsOo", "/tmp/local.txt", {"url": "https://example.com/doc"}],
            "urls": ["https://www.youtube.com/watch?v=VTtC8tAzsOo"],
            "paths": ["/tmp/another.txt"],
        }
    )

    assert sources[0] == {"url": "https://youtu.be/VTtC8tAzsOo"}
    assert {"path": "/tmp/local.txt"} in sources
    assert {"url": "https://example.com/doc"} in sources
    assert {"url": "https://www.youtube.com/watch?v=VTtC8tAzsOo"} in sources
    assert {"path": "/tmp/another.txt"} in sources


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_build_rag_documents_supports_youtube_sources(monkeypatch):
    monkeypatch.setattr(
        "assistant_pipeline.ingestion.source_loader.fetch_youtube_transcript",
        lambda url, timeout: (
            "Transcript body",
            {
                "source_type": "youtube_transcript",
                "video_id": "VTtC8tAzsOo",
                "caption_lang": "en",
                "title": "Conference Talk",
                "channel_name": "Digital Leaders",
            },
        ),
    )

    docs = refiner_web._build_rag_documents(
        [{"url": "https://youtu.be/VTtC8tAzsOo"}],
        max_docs=5,
        max_doc_bytes=5000,
    )

    assert len(docs) == 1
    assert docs[0].source == "Conference Talk"
    assert docs[0].text == "Transcript body"
    assert docs[0].metadata["source_url"] == "https://youtu.be/VTtC8tAzsOo"
    assert docs[0].metadata["video_id"] == "VTtC8tAzsOo"
    assert docs[0].metadata["channel_name"] == "Digital Leaders"
