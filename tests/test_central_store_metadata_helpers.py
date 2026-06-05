from types import SimpleNamespace

from central_store.assistant import (
    PostgresAssistantConversationStore,
    PostgresAssistantEpisodeStore,
    PostgresAssistantSemanticCacheStore,
    PostgresAssistantTraceStore,
)
from central_store.rag import PostgresRagMetadataStore
from refiner.runtime.central_store import PostgresLLMRequestTelemetry


class _FakeResult:
    def __init__(self, *, rows=None, row=None, rowcount=1):
        self._rows = list(rows or [])
        self._row = row
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._row


class _FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.execute_calls = []
        self.executemany_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return _FakeTransaction()

    def execute(self, query, params=()):
        self.execute_calls.append((" ".join(str(query).split()), params))
        if self.responses:
            return self.responses.pop(0)
        return _FakeResult()

    def executemany(self, query, params_seq):
        self.executemany_calls.append((" ".join(str(query).split()), list(params_seq)))


class _FakePool:
    def __init__(self, connection):
        self._connection = connection

    def connection(self):
        return self._connection


class _FakeStore:
    def __init__(self, connection):
        self.pool = _FakePool(connection)


def test_postgres_assistant_conversation_store_writes_and_reads_recent_turns():
    connection = _FakeConnection()
    store = PostgresAssistantConversationStore(_FakeStore(connection))

    store.ensure_conversation("conv-1", "alice", route="assistant_requirements", scope="draft", title="Draft helper")
    turn_id = store.append_turn("conv-1", "alice", role="user", route="assistant_requirements", content="Draft helper")
    connection.responses = [
        _FakeResult(
            rows=[
                {"turn_id": "turn-2", "role": "assistant", "content": "reply", "created_at": "2026-04-22T12:01:00Z"},
                {"turn_id": "turn-1", "role": "user", "content": "prompt", "created_at": "2026-04-22T12:00:00Z"},
            ]
        )
    ]
    rows = store.recent_turns("conv-1", owner="alice", limit=2)

    assert turn_id
    assert "INSERT INTO nm_assistant_conversations" in connection.execute_calls[0][0]
    assert "INSERT INTO nm_assistant_turns" in connection.execute_calls[1][0]
    assert "UPDATE nm_assistant_conversations" in connection.execute_calls[2][0]
    assert rows[0]["turn_id"] == "turn-1"
    assert rows[1]["turn_id"] == "turn-2"
    assert connection.execute_calls[3][1] == ("conv-1", "alice", 2)


def test_postgres_assistant_conversation_store_lists_and_reads_conversation_headers():
    connection = _FakeConnection(
        responses=[
            _FakeResult(
                rows=[
                    {
                        "conversation_id": "conv-1",
                        "owner": "alice",
                        "route": "assistant_rag_mcp",
                        "title": "Sync help",
                        "metadata": {"mode": "assistant_rag_mcp"},
                    }
                ]
            ),
            _FakeResult(
                row={
                    "conversation_id": "conv-1",
                    "owner": "alice",
                    "route": "assistant_rag_mcp",
                    "title": "Sync help",
                    "metadata": {"mode": "assistant_rag_mcp"},
                }
            ),
        ]
    )
    store = PostgresAssistantConversationStore(_FakeStore(connection))

    rows = store.list_conversations("alice", route="assistant_rag_mcp", limit=3)
    row = store.get_conversation("conv-1", owner="alice")

    assert rows[0]["conversation_id"] == "conv-1"
    assert row["owner"] == "alice"
    assert "FROM nm_assistant_conversations WHERE owner = %s AND route = %s" in connection.execute_calls[0][0]
    assert connection.execute_calls[0][1] == ("alice", "assistant_rag_mcp", 3)
    assert "WHERE conversation_id = %s AND owner = %s" in connection.execute_calls[1][0]
    assert connection.execute_calls[1][1] == ("conv-1", "alice")


def test_postgres_assistant_episode_store_snapshot_with_limit_preserves_ascending_order():
    connection = _FakeConnection(
        responses=[
            _FakeResult(
                rows=[
                    {
                        "episode_id": "ep-2",
                        "source_path": "assistant_requirements:draft:requirements",
                        "iteration": 2,
                        "created_at": "2026-04-22T12:01:00Z",
                        "outcome": "success",
                        "summary": "Second draft",
                        "requirement_ids": ["REQ-002"],
                        "modified_files": [],
                        "commands": [],
                        "verification_failures": [],
                        "notes": [],
                        "metadata": {},
                    },
                    {
                        "episode_id": "ep-1",
                        "source_path": "assistant_requirements:draft:requirements",
                        "iteration": 1,
                        "created_at": "2026-04-22T12:00:00Z",
                        "outcome": "success",
                        "summary": "First draft",
                        "requirement_ids": ["REQ-001"],
                        "modified_files": [],
                        "commands": [],
                        "verification_failures": [],
                        "notes": [],
                        "metadata": {},
                    },
                ]
            )
        ]
    )
    store = PostgresAssistantEpisodeStore(_FakeStore(connection))

    rows = store.snapshot("alice", source_path="assistant_requirements:draft:requirements", limit=2)

    assert [row.episode_id for row in rows] == ["ep-1", "ep-2"]
    assert "ORDER BY created_at DESC LIMIT %s" in connection.execute_calls[0][0]


def test_postgres_assistant_trace_store_writes_start_span_and_finish():
    connection = _FakeConnection()
    store = PostgresAssistantTraceStore(_FakeStore(connection))

    store.start_trace("trace-1", "alice", route="rag_query", intent="rag_query", request_meta={"query": "release notes"})
    store.record_span("trace-1", "rag_search", duration_ms=17, meta={"match_count": 2})
    store.finish_trace(
        "trace-1",
        status="success",
        provider="fake_provider",
        model="fake_model",
        cache_hit=False,
        response_meta={"match_count": 2},
    )

    assert "INSERT INTO nm_assistant_traces" in connection.execute_calls[0][0]
    assert connection.execute_calls[0][1][0] == "trace-1"
    assert "INSERT INTO nm_assistant_trace_spans" in connection.execute_calls[1][0]
    assert connection.execute_calls[1][1][2] == "rag_search"
    assert "UPDATE nm_assistant_traces" in connection.execute_calls[2][0]
    assert connection.execute_calls[2][1][-1] == "trace-1"


def test_postgres_assistant_trace_store_lists_trace_headers_details_and_spans():
    connection = _FakeConnection(
        responses=[
            _FakeResult(
                rows=[
                    {
                        "trace_id": "trace-1",
                        "owner": "alice",
                        "route": "rag_query",
                        "status": "success",
                        "conversation_id": "conv-1",
                    }
                ]
            ),
            _FakeResult(
                row={
                    "trace_id": "trace-1",
                    "owner": "alice",
                    "route": "rag_query",
                    "status": "success",
                    "conversation_id": "conv-1",
                }
            ),
            _FakeResult(
                rows=[
                    {
                        "trace_id": "trace-1",
                        "span_id": "span-1",
                        "stage": "rag_search",
                        "status": "success",
                        "duration_ms": 21,
                    }
                ]
            ),
        ]
    )
    store = PostgresAssistantTraceStore(_FakeStore(connection))

    traces = store.list_traces("alice", route="rag_query", status="success", conversation_id="conv-1", limit=5)
    trace = store.get_trace("trace-1", owner="alice")
    spans = store.list_spans("trace-1", limit=20)

    assert traces[0]["trace_id"] == "trace-1"
    assert trace["conversation_id"] == "conv-1"
    assert spans[0]["stage"] == "rag_search"
    assert "FROM nm_assistant_traces WHERE owner = %s AND route = %s AND status = %s AND conversation_id = %s" in connection.execute_calls[0][0]
    assert connection.execute_calls[0][1] == ("alice", "rag_query", "success", "conv-1", 5)
    assert "WHERE trace_id = %s AND owner = %s" in connection.execute_calls[1][0]
    assert connection.execute_calls[1][1] == ("trace-1", "alice")
    assert "FROM nm_assistant_trace_spans WHERE trace_id = %s ORDER BY created_at ASC LIMIT %s" in connection.execute_calls[2][0]
    assert connection.execute_calls[2][1] == ("trace-1", 20)


def test_postgres_assistant_semantic_cache_store_upserts_lists_and_marks_hits():
    connection = _FakeConnection(
        responses=[
            _FakeResult(row={"cache_id": "cache-1"}),
            _FakeResult(
                rows=[
                    {
                        "cache_id": "cache-1",
                        "owner": "alice",
                        "route": "rag_query",
                        "intent": "rag_query:retrieval",
                        "scope_key": "rag_query:ops:version-1",
                        "query_hash": "hash-1",
                        "query_text": "How does the sync work?",
                        "normalized_query": "how does the sync work",
                        "query_terms": ["how", "does", "sync", "work"],
                        "response_payload": {"context": "cached"},
                        "metadata": {"version_id": "version-1"},
                    }
                ]
            ),
        ]
    )
    store = PostgresAssistantSemanticCacheStore(_FakeStore(connection))

    cache_id = store.upsert_entry(
        "alice",
        "rag_query",
        "rag_query:ops:version-1",
        intent="rag_query:retrieval",
        query_text="How does the sync work?",
        normalized_query="how does the sync work",
        query_terms=["how", "does", "sync", "work"],
        response_payload={"context": "cached"},
        metadata={"version_id": "version-1"},
        ttl_hours=6.0,
    )
    rows = store.list_candidates("alice", "rag_query", "rag_query:ops:version-1", intent="rag_query:retrieval", limit=5)
    store.record_hit("cache-1")

    assert cache_id == "cache-1"
    assert rows[0]["cache_id"] == "cache-1"
    assert "INSERT INTO nm_assistant_semantic_cache" in connection.execute_calls[0][0]
    assert "FROM nm_assistant_semantic_cache" in connection.execute_calls[1][0]
    assert connection.execute_calls[1][1] == (
        "alice",
        "rag_query",
        "rag_query:ops:version-1",
        "rag_query:retrieval",
        5,
    )
    assert "UPDATE nm_assistant_semantic_cache" in connection.execute_calls[2][0]
    assert connection.execute_calls[2][1] == ("cache-1",)


def test_postgres_rag_metadata_store_records_versions_and_query_audits():
    connection = _FakeConnection()
    store = PostgresRagMetadataStore(_FakeStore(connection))
    documents = [
        SimpleNamespace(
            doc_id="doc-1",
            source="inline",
            text="Alpha release notes",
            metadata={"source_path": "/tmp/source.txt", "source_url": "https://example.invalid/source"},
        )
    ]
    chunks = [
        SimpleNamespace(
            chunk_id="chunk-1",
            doc_id="doc-1",
            source="inline",
            citation="[Doc 1 p.1]",
            text="Alpha release notes",
            metadata={"section": "Overview"},
        )
    ]

    version_id = store.record_collection_version(
        "alice",
        "docs",
        artifact_path="/tmp/alice/docs.json",
        source_count=1,
        documents=documents,
        chunks=chunks,
        metadata={"chunk_size": 256},
        version_id="version-1",
    )
    audit_id = store.record_query_audit(
        "alice",
        "docs",
        route="rag_query",
        query_text="release notes",
        rewritten_query="release notes",
        top_k=3,
        match_count=1,
        version_id=version_id,
        metadata={"min_score": 0.1},
    )

    assert version_id == "version-1"
    assert audit_id
    assert "INSERT INTO nm_rag_collections" in connection.execute_calls[0][0]
    assert "INSERT INTO nm_rag_collection_versions" in connection.execute_calls[1][0]
    assert "DELETE FROM nm_rag_documents" in connection.execute_calls[2][0]
    assert "DELETE FROM nm_rag_chunks" in connection.execute_calls[3][0]
    assert "INSERT INTO nm_rag_query_audits" in connection.execute_calls[4][0]
    assert len(connection.executemany_calls) == 2
    assert len(connection.executemany_calls[0][1]) == 1
    assert len(connection.executemany_calls[1][1]) == 1


def test_postgres_rag_metadata_store_tracks_build_lifecycle_states_without_publishing_them() -> None:
    connection = _FakeConnection()
    store = PostgresRagMetadataStore(_FakeStore(connection))

    queued_version = store.start_collection_build(
        "alice",
        "docs",
        version_id="version-queued",
        status="queued",
        metadata={"source_count": 2},
    )
    failed_version = store.fail_collection_build(
        "alice",
        "docs",
        version_id="version-queued",
        status="failed",
        metadata={"error_code": "boom"},
    )

    assert queued_version == "version-queued"
    assert failed_version == "version-queued"
    assert "INSERT INTO nm_rag_collections" in connection.execute_calls[0][0]
    assert "INSERT INTO nm_rag_collection_versions" in connection.execute_calls[1][0]
    assert connection.execute_calls[1][1][3] == "queued"
    assert "INSERT INTO nm_rag_collections" in connection.execute_calls[2][0]
    assert "INSERT INTO nm_rag_collection_versions" in connection.execute_calls[3][0]
    assert connection.execute_calls[3][1][3] == "failed"


def test_postgres_rag_metadata_store_can_stage_publication_before_final_activation() -> None:
    connection = _FakeConnection()
    store = PostgresRagMetadataStore(_FakeStore(connection))

    version_id = store.stage_collection_version(
        "alice",
        "docs",
        version_id="version-stage",
        status="publishing",
        artifact_path="/tmp/rag/collections/alice/docs/version-stage/index.json",
        metadata={"publish_state": "staged"},
    )

    assert version_id == "version-stage"
    assert "INSERT INTO nm_rag_collections" in connection.execute_calls[0][0]
    assert connection.execute_calls[0][1][3] == "publishing"
    assert "INSERT INTO nm_rag_collection_versions" in connection.execute_calls[1][0]
    assert "nm_rag_collection_versions.metadata || EXCLUDED.metadata" in connection.execute_calls[1][0]
    assert connection.execute_calls[1][1][3] == "publishing"
    assert connection.execute_calls[1][1][4] == "/tmp/rag/collections/alice/docs/version-stage/index.json"


def test_postgres_rag_metadata_store_reads_active_version_and_deletes_versions():
    connection = _FakeConnection(
        responses=[
            _FakeResult(row={"owner": "alice", "name": "docs", "active_version_id": "version-1", "status": "ready"}),
            _FakeResult(rows=[{"version_id": "version-1"}, {"version_id": "version-2"}]),
            _FakeResult(rowcount=1),
        ]
    )
    store = PostgresRagMetadataStore(_FakeStore(connection))

    active = store.get_active_version("alice", "docs")
    deleted = store.delete_collection("alice", "docs")

    assert active == {"owner": "alice", "name": "docs", "active_version_id": "version-1", "status": "ready"}
    assert deleted is True
    assert "SELECT c.owner, c.name, c.active_version_id" in connection.execute_calls[0][0]
    assert "SELECT version_id FROM nm_rag_collection_versions" in connection.execute_calls[1][0]
    assert connection.executemany_calls[0][1] == [("version-1",), ("version-2",)]
    assert "DELETE FROM nm_rag_collections" in connection.execute_calls[2][0]


def test_postgres_llm_request_telemetry_summary_avoids_nullable_filter_placeholders():
    connection = _FakeConnection(
        responses=[
            _FakeResult(row={}),
            _FakeResult(rows=[]),
        ]
    )
    telemetry = PostgresLLMRequestTelemetry(_FakeStore(connection))

    payload = telemetry.summary(hours=72, limit=12)

    assert payload["enabled"] is True
    assert len(connection.execute_calls) == 2

    totals_query, totals_params = connection.execute_calls[0]
    grouped_query, grouped_params = connection.execute_calls[1]

    assert "%s::text IS NULL" not in totals_query
    assert "%s::text IS NULL" not in grouped_query
    assert totals_params and len(totals_params) == 1
    assert grouped_params and len(grouped_params) == 2


def test_postgres_llm_request_telemetry_summary_casts_limit_placeholders():
    connection = _FakeConnection(
        responses=[
            _FakeResult(row={}),
            _FakeResult(rows=[]),
            _FakeResult(rows=[]),
        ]
    )
    telemetry = PostgresLLMRequestTelemetry(_FakeStore(connection))

    payload = telemetry.summary(hours=72, limit=12, include_subjects=True, subject_limit=6)

    assert payload["enabled"] is True
    assert len(connection.execute_calls) == 3

    grouped_query, grouped_params = connection.execute_calls[1]
    subject_query, subject_params = connection.execute_calls[2]

    assert "LIMIT %s::integer" in grouped_query
    assert "LIMIT %s::integer" in subject_query
    assert grouped_params[-1] == 12
    assert subject_params[-1] == 6
