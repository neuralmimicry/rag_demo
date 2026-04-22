import time
from types import SimpleNamespace

from assistant_pipeline.memory.conversation_store import (
    append_turn,
    conversation_id_from_payload,
    ensure_conversation,
    recent_turns,
)
from assistant_pipeline.memory.query_rewriter import rewrite_query
from assistant_pipeline.tracing.recorder import TraceRecorder


class _FakeLogger:
    def __init__(self):
        self.debug_calls = []

    def debug(self, *args, **kwargs):
        self.debug_calls.append((args, kwargs))


class _FakeConversationStore:
    def __init__(self, rows=None, *, fail_reads: bool = False):
        self.rows = list(rows or [])
        self.fail_reads = fail_reads
        self.ensure_calls = []
        self.append_calls = []
        self.recent_calls = []

    def ensure_conversation(self, conversation_id, owner, **kwargs):
        self.ensure_calls.append({"conversation_id": conversation_id, "owner": owner, **kwargs})

    def append_turn(self, conversation_id, owner, **kwargs):
        self.append_calls.append({"conversation_id": conversation_id, "owner": owner, **kwargs})
        return "turn-1"

    def recent_turns(self, conversation_id, *, owner="", limit=12):
        self.recent_calls.append({"conversation_id": conversation_id, "owner": owner, "limit": limit})
        if self.fail_reads:
            raise RuntimeError("read failed")
        return list(self.rows[:limit])


class _FakeTraceStore:
    def __init__(self, *, fail_start: bool = False):
        self.fail_start = fail_start
        self.start_calls = []
        self.span_calls = []
        self.finish_calls = []

    def start_trace(self, trace_id, owner, **kwargs):
        if self.fail_start:
            raise RuntimeError("trace unavailable")
        self.start_calls.append({"trace_id": trace_id, "owner": owner, **kwargs})

    def record_span(self, trace_id, stage, **kwargs):
        self.span_calls.append({"trace_id": trace_id, "stage": stage, **kwargs})

    def finish_trace(self, trace_id, **kwargs):
        self.finish_calls.append({"trace_id": trace_id, **kwargs})


def _deps(*, conversation_store=None, trace_store=None, logger=None):
    logger = logger or _FakeLogger()
    return SimpleNamespace(
        logger=logger,
        new_trace_id=lambda: "trace-1",
        get_assistant_trace_store=lambda: trace_store,
        get_assistant_conversation_store=lambda: conversation_store,
    )


def test_conversation_id_from_payload_strips_whitespace():
    assert conversation_id_from_payload({"conversation_id": "  conv-42  "}) == "conv-42"
    assert conversation_id_from_payload({"conversation_id": "   "}) is None


def test_conversation_helpers_write_and_read_recent_turns():
    logger = _FakeLogger()
    store = _FakeConversationStore(rows=[{"turn_id": "turn-1", "role": "user", "content": "hello"}])
    deps = _deps(conversation_store=store, logger=logger)

    ensure_conversation(
        deps,
        owner="alice",
        conversation_id="conv-1",
        route="assistant_requirements",
        scope="draft",
        title="Draft helper",
        metadata={"mode": "draft"},
    )
    append_turn(
        deps,
        owner="alice",
        conversation_id="conv-1",
        role="user",
        route="assistant_requirements",
        content="Draft a helper",
        metadata={"mode": "draft"},
    )
    rows = recent_turns(deps, owner="alice", conversation_id="conv-1", limit=4)

    assert store.ensure_calls[0]["conversation_id"] == "conv-1"
    assert store.append_calls[0]["role"] == "user"
    assert store.recent_calls[0] == {"conversation_id": "conv-1", "owner": "alice", "limit": 4}
    assert rows == [{"turn_id": "turn-1", "role": "user", "content": "hello"}]
    assert logger.debug_calls == []


def test_rewrite_query_turns_short_follow_up_into_standalone_query():
    rewrite = rewrite_query(
        "What about failures?",
        [{"turn_id": "turn-1", "role": "user", "content": "How does the customer sync work?"}],
    )

    assert rewrite.rewritten is True
    assert rewrite.reason == "follow_up_rewritten"
    assert rewrite.retrieval_query == "How does the customer sync work What about failures?"


def test_recent_turns_returns_empty_and_logs_when_store_read_fails():
    logger = _FakeLogger()
    deps = _deps(conversation_store=_FakeConversationStore(fail_reads=True), logger=logger)

    assert recent_turns(deps, owner="alice", conversation_id="conv-2") == []
    assert logger.debug_calls
    assert "Assistant conversation read skipped" in logger.debug_calls[0][0][0]


def test_trace_recorder_records_spans_and_finish():
    store = _FakeTraceStore()
    deps = _deps(trace_store=store)

    recorder = TraceRecorder(
        deps,
        owner="alice",
        route="assistant_requirements",
        intent="assistant_requirements:draft",
        conversation_id="conv-1",
        request_meta={"mode": "draft"},
    )
    recorder.record_span("generate", time.monotonic() - 0.01, metadata={"provider": "fake"})
    recorder.finish(status="success", provider="fake_provider", model="fake_model", response_meta={"mode": "draft"})

    assert store.start_calls[0]["trace_id"] == "trace-1"
    assert store.span_calls[0]["stage"] == "generate"
    assert store.span_calls[0]["duration_ms"] >= 0
    assert store.finish_calls[0]["provider"] == "fake_provider"
    assert store.finish_calls[0]["response_meta"] == {"mode": "draft"}


def test_trace_recorder_degrades_when_start_fails():
    logger = _FakeLogger()
    deps = _deps(trace_store=_FakeTraceStore(fail_start=True), logger=logger)

    recorder = TraceRecorder(deps, owner="alice", route="rag_query")
    recorder.record_span("rag_search", time.monotonic() - 0.01)
    recorder.finish(status="failed", error_code="boom")

    assert logger.debug_calls
    assert "Assistant trace start skipped" in logger.debug_calls[0][0][0]
