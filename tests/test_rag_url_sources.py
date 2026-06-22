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
def test_coerce_rag_sources_accepts_structured_records_and_inline_documents():
    sources = refiner_web._coerce_rag_sources(
        {
            "urls": ["https://example.com/docs", "https://example.com/docs"],  # deduplicated
            "documents": [
                "Inline policy text",
                {"id": "doc-2", "content": "Inline FAQ", "source": "faq"},
            ],
            "records": [
                {"id": "cust-1", "name": "Alice", "tier": "gold"},
                {"id": "cust-2", "name": "Bob", "tier": "silver"},
            ],
            "record_text_fields": ["name", "tier"],
        }
    )

    assert {"url": "https://example.com/docs"} in sources
    assert {"text": "Inline policy text", "source": "inline_document"} in sources
    assert {"id": "doc-2", "text": "Inline FAQ", "source": "faq", "metadata": {}} in sources
    record_sources = [entry for entry in sources if isinstance(entry, dict) and isinstance(entry.get("records"), list)]
    assert len(record_sources) == 1
    assert record_sources[0]["text_fields"] == ["name", "tier"]


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


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_build_rag_documents_supports_structured_record_sources():
    docs = refiner_web._build_rag_documents(
        [
            {
                "id": "crm",
                "source": "crm_export",
                "records": [
                    {"id": "cust-1", "name": "Alice", "status": "active"},
                    {"id": "cust-2", "name": "Bob", "status": "trial"},
                ],
                "text_fields": ["name", "status"],
                "metadata": {"system": "salesforce"},
            }
        ],
        max_docs=5,
        max_doc_bytes=10000,
    )

    assert len(docs) == 2
    assert docs[0].doc_id.startswith("crm-cust-1")
    assert "- name: Alice" in docs[0].text
    assert docs[0].metadata["system"] == "salesforce"
    assert docs[0].metadata["source_type"] == "structured_records"
