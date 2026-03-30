from document_schema import DocumentElement
from rag_engine import RagDocument, RagIndex, RagStore, chunk_text


def test_chunk_text_respects_overlap():
    text = "abcdefghijklmnopqrstuvwxyz"
    chunks = chunk_text(text, chunk_size=10, overlap=3)
    assert chunks[0] == "abcdefghij"
    assert chunks[1].startswith("hij")
    assert chunks[-1].endswith("z")


def test_rag_index_search_prefers_relevant_chunk():
    docs = [
        RagDocument(doc_id="1", source="alpha.md", text="python flask api security tokens", metadata={"kind": "a"}),
        RagDocument(doc_id="2", source="beta.md", text="gardening flowers soil watering", metadata={"kind": "b"}),
    ]
    index = RagIndex.build("demo", docs)
    matches = index.search("flask security api", limit=2)
    assert matches
    assert matches[0].source == "alpha.md"
    assert matches[0].score > 0


def test_rag_store_round_trip(tmp_path):
    store = RagStore(str(tmp_path))
    docs = [RagDocument(doc_id="1", source="doc.txt", text="retrieval augmented generation", metadata={})]
    index = RagIndex.build("idx", docs)
    store.save_index("alice", index)
    loaded = store.load_index("alice", "idx")
    assert loaded is not None
    assert loaded.name == "idx"
    assert len(loaded.chunks) == len(index.chunks)
    assert store.delete_index("alice", "idx")


def test_rag_index_prefers_layout_aware_chunks_and_citations():
    elements = [
        DocumentElement(
            element_id="p0001-b0001",
            element_type="heading",
            text="Retry Strategy",
            page=1,
            block_index=1,
            markdown="## Retry Strategy",
            heading_path=["Retry Strategy"],
        ),
        DocumentElement(
            element_id="p0001-b0002",
            element_type="paragraph",
            text="Retry logic handles transient failures with bounded exponential backoff and jitter.",
            page=1,
            block_index=2,
            heading_path=["Retry Strategy"],
        ),
        DocumentElement(
            element_id="p0002-b0001",
            element_type="table",
            text="Status  Count\nSuccess  5\nFailure  1",
            page=2,
            block_index=1,
            heading_path=["Operational Metrics"],
        ),
    ]
    docs = [
        RagDocument(
            doc_id="1",
            source="spec.pdf",
            text="fallback text that should not drive chunk boundaries",
            metadata={"kind": "pdf"},
            elements=elements,
        )
    ]
    index = RagIndex.build("demo", docs, chunk_size=100, chunk_overlap=10)

    assert len(index.chunks) >= 2
    matches = index.search("retry backoff jitter", limit=2)
    assert matches
    assert matches[0].metadata["page_start"] == 1
    assert matches[0].metadata["page_end"] == 1
    assert matches[0].metadata["block_start"] == 1
    assert matches[0].citation.startswith("spec.pdf [p.1")


def test_rag_store_round_trip_preserves_citations(tmp_path):
    store = RagStore(str(tmp_path))
    docs = [
        RagDocument(
            doc_id="1",
            source="guide.pdf",
            text="ignored",
            metadata={"kind": "pdf"},
            elements=[
                DocumentElement(
                    element_id="p0003-b0004",
                    element_type="paragraph",
                    text="Rate limits are enforced before requests are queued.",
                    page=3,
                    block_index=4,
                )
            ],
        )
    ]
    index = RagIndex.build("idx", docs, chunk_size=200)
    store.save_index("alice", index)
    loaded = store.load_index("alice", "idx")

    assert loaded is not None
    assert loaded.chunks[0].citation == "guide.pdf [p.3 b.4]"
