from central_store import ASSISTANT_SCHEMA_STATEMENTS, RAG_SCHEMA_STATEMENTS


def test_assistant_schema_statements_include_expected_tables():
    joined = "\n".join(ASSISTANT_SCHEMA_STATEMENTS)
    assert "nm_assistant_conversations" in joined
    assert "nm_assistant_turns" in joined
    assert "nm_assistant_episodes" in joined
    assert "nm_assistant_traces" in joined
    assert "nm_assistant_trace_spans" in joined
    assert "nm_assistant_semantic_cache" in joined


def test_rag_schema_statements_include_expected_tables():
    joined = "\n".join(RAG_SCHEMA_STATEMENTS)
    assert "nm_rag_collections" in joined
    assert "nm_rag_collection_versions" in joined
    assert "nm_rag_documents" in joined
    assert "nm_rag_chunks" in joined
    assert "nm_rag_query_audits" in joined
