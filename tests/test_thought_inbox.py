import flask
import pytest
from refiner.thought_inbox import build_route_suggestion, build_thought_item, merge_duplicate_capture, score_query_match

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


def _setup_authenticated_user(monkeypatch, username="alice"):
    monkeypatch.setattr(refiner_web.user_store, "has_users", lambda: True)
    return username


def test_merge_duplicate_capture_accumulates_histories_and_links():
    first = build_thought_item(
        "Follow up on token ledger edge cases",
        now_iso="2026-03-30 10:00:00",
        source="manual",
        device="web",
        meta={"session_id": "session-a"},
        defer_until_idle=True,
    )
    merged = merge_duplicate_capture(
        first,
        text="follow up on token ledger edge cases",
        now_iso="2026-03-30 10:01:00",
        source="voice",
        device="iphone",
        meta={"room_id": "room-a"},
        defer_until_idle=True,
    )

    assert merged["occurrences"] == 2
    assert sorted(merged["source_history"]) == ["manual", "voice"]
    assert sorted(merged["device_history"]) == ["iphone", "web"]
    assert merged["links"]["sessions"] == ["session-a"]
    assert merged["links"]["rooms"] == ["room-a"]
    assert merged["kind"] == "task"


def test_build_route_suggestion_preserves_linked_project_context():
    item = build_thought_item(
        "Implement retry handling for Rust voice requests in refiner_web.py and add tests",
        now_iso="2026-03-30 10:00:00",
        source="manual",
        device="web",
        meta={"project_id": "project-123"},
        defer_until_idle=False,
    )

    route = build_route_suggestion(item)

    assert route["workflow"] == "project_solver"
    assert route["payload"]["project_id"] == "project-123"
    assert route["payload"]["requirements_text"].startswith("Implement retry handling")


def test_score_query_match_prefers_keyword_hits():
    item = build_thought_item(
        "Investigate the Jira pagination fallback and compare candidate fixes",
        now_iso="2026-03-30 10:00:00",
        source="manual",
        device="web",
        defer_until_idle=True,
    )

    assert score_query_match(item, "jira pagination") > score_query_match(item, "rust")


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_todo_store_merges_duplicate_captures(tmp_path):
    store = refiner_web.TodoStore(str(tmp_path / "todos"), claim_ttl_sec=300, retention_days=0)

    first = store.add_item(
        "alice",
        "Follow up on token ledger edge cases",
        source="manual",
        device="web",
        meta={"session_id": "session-a"},
        defer_until_idle=True,
    )
    second = store.add_item(
        "alice",
        "follow up on token ledger edge cases",
        source="voice",
        device="iphone",
        meta={"room_id": "room-a"},
        defer_until_idle=True,
    )

    assert first["id"] == second["id"]
    assert second["occurrences"] == 2
    assert sorted(second["source_history"]) == ["manual", "voice"]
    assert sorted(second["device_history"]) == ["iphone", "web"]
    assert second["links"]["sessions"] == ["session-a"]
    assert second["links"]["rooms"] == ["room-a"]
    assert second["kind"] == "task"

    stored = store.list_items("alice", statuses=["todo"])
    assert len(stored) == 1
    assert stored[0]["occurrences"] == 2


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_api_todo_next_returns_claimed_route(monkeypatch, tmp_path):
    store = refiner_web.TodoStore(str(tmp_path / "todos"), claim_ttl_sec=300, retention_days=0)
    store.add_item(
        "alice",
        "Investigate the Jira pagination fallback and compare candidate fixes",
        source="manual",
        device="web",
        defer_until_idle=True,
    )

    monkeypatch.setattr(refiner_web, "todo_store", store)
    username = _setup_authenticated_user(monkeypatch, "alice")

    with refiner_web.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = username
        response = client.get("/api/todos/next?idle=1&claim=1")

    assert response.status_code == 200
    data = response.get_json()
    assert data["todo"]["execution_state"] == "claimed"
    assert data["route"]["workflow"] == "topic_research"
    assert data["route"]["endpoint"] == "/api/jobs"


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask integration tests require a real Flask runtime")
def test_api_todo_route_projects_code_tasks_into_project_solver(monkeypatch, tmp_path):
    store = refiner_web.TodoStore(str(tmp_path / "todos"), claim_ttl_sec=300, retention_days=0)
    item = store.add_item(
        "alice",
        "Implement retry handling for Rust voice requests in refiner_web.py and add tests",
        source="manual",
        device="web",
        defer_until_idle=False,
    )

    monkeypatch.setattr(refiner_web, "todo_store", store)
    username = _setup_authenticated_user(monkeypatch, "alice")

    with refiner_web.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = username
        response = client.post(f"/api/todos/{item['id']}/route")

    assert response.status_code == 200
    data = response.get_json()
    assert data["route"]["workflow"] == "project_solver"
    assert data["route"]["endpoint"] == "/api/jobs"
    assert data["route"]["payload"]["requirements_text"].startswith("Implement retry handling")
