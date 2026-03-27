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
