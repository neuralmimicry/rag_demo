import copy
import time
from types import SimpleNamespace

import flask
import pytest

HAS_REAL_FLASK = hasattr(flask.Flask, "test_client")
if HAS_REAL_FLASK:
    from refiner import refiner_web  # noqa: E402


def _deep_copy(value):
    return copy.deepcopy(value)


class FakeTodoDocuments:
    def __init__(self):
        self.docs = {}

    def load_user(self, username):
        payload = self.docs.get(username)
        return _deep_copy(payload) if payload is not None else None

    def write_user(self, username, data):
        self.docs[username] = _deep_copy(data)


class FakeScheduleDocuments:
    def __init__(self):
        self.doc = None

    def load(self):
        return _deep_copy(self.doc) if self.doc is not None else None

    def write(self, data):
        self.doc = _deep_copy(data)


class FakeSessionRooms:
    def __init__(self):
        self.rooms = {}

    def load_room(self, room_id):
        payload = self.rooms.get(room_id)
        return _deep_copy(payload) if payload is not None else None

    def write_room(self, room_id, data):
        self.rooms[room_id] = _deep_copy(data)

    def persist_snapshot(self, room_id, snapshot):
        data = self.rooms.get(room_id) or {
            "room_id": room_id,
            "created_at": snapshot.get("created_at"),
            "updated_at": snapshot.get("updated_at"),
            "events": [],
        }
        data["snapshot"] = _deep_copy(snapshot)
        data["updated_at"] = snapshot.get("updated_at")
        if snapshot.get("job_id") and not data.get("job_id"):
            data["job_id"] = snapshot.get("job_id")
        if snapshot.get("project_id") and not data.get("project_id"):
            data["project_id"] = snapshot.get("project_id")
        if snapshot.get("created_by") and not data.get("created_by"):
            data["created_by"] = snapshot.get("created_by")
        self.rooms[room_id] = _deep_copy(data)

    def list_rooms(self, limit=50, tail=5):
        rooms = []
        for room_id, data in self.rooms.items():
            events = data.get("events") if isinstance(data.get("events"), list) else []
            rooms.append(
                {
                    "room_id": room_id,
                    "job_id": data.get("job_id"),
                    "project_id": data.get("project_id"),
                    "created_by": data.get("created_by"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "events_count": len(events),
                    "last_event": events[-1] if events else None,
                    "events_tail": events[-tail:] if tail and events else [],
                }
            )
        rooms.sort(key=lambda item: refiner_web._timestamp_sort_key(item.get("updated_at")), reverse=True)
        return rooms[:limit]


class FakeJobStore:
    def __init__(self):
        self.records = {}

    def upsert(self, data):
        job_id = str(data.get("id") or data.get("job_id") or "").strip()
        assert job_id
        self.records[job_id] = _deep_copy(data)

    def list_jobs(self, **_kwargs):
        return [_deep_copy(value) for value in self.records.values()]

    def delete(self, job_id):
        return self.records.pop(job_id, None) is not None


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner tests require a real Flask runtime")
def test_postgres_todo_store_backfills_legacy_file_and_roundtrips(tmp_path):
    legacy = refiner_web.TodoStore(str(tmp_path / "legacy"), claim_ttl_sec=300, retention_days=0)
    item = legacy.add_item(
        "alice",
        "Backfill existing metadata into the central todo store",
        source="manual",
        device="web",
        defer_until_idle=False,
    )

    documents = FakeTodoDocuments()
    migrated = refiner_web.PostgresTodoStore(
        str(tmp_path / "legacy"),
        documents,
        claim_ttl_sec=300,
        retention_days=0,
    )
    loaded = migrated.get_item("alice", item["id"])

    assert loaded is not None
    assert loaded["text"].startswith("Backfill existing metadata")
    assert documents.docs["alice"]["items"][0]["id"] == item["id"]

    replay = refiner_web.PostgresTodoStore(
        str(tmp_path / "empty"),
        documents,
        claim_ttl_sec=300,
        retention_days=0,
    )
    replayed = replay.get_item("alice", item["id"])

    assert replayed is not None
    assert replayed["id"] == item["id"]


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner tests require a real Flask runtime")
def test_postgres_backed_scheduler_executes_due_job(monkeypatch, tmp_path):
    todo_documents = FakeTodoDocuments()
    schedule_documents = FakeScheduleDocuments()
    local_todos = refiner_web.PostgresTodoStore(
        str(tmp_path / "todos"),
        todo_documents,
        claim_ttl_sec=300,
        retention_days=0,
    )
    local_schedules = refiner_web.PostgresScheduleStore(
        str(tmp_path / "schedules"),
        schedule_documents,
        claim_ttl_sec=120,
    )
    local_subtasks = refiner_web.SubtaskManager(workers=1, max_queue=4, task_ttl_sec=600)
    scheduler = refiner_web.TodoScheduler(
        schedule_store=local_schedules,
        todo_store=local_todos,
        subtask_manager=local_subtasks,
        poll_sec=60,
        execution_timeout_sec=30,
        orphan_ttl_sec=120,
    )

    monkeypatch.setattr(refiner_web, "_invoke_internal_post_json", lambda **_kwargs: {"job_id": "job-42", "status": "queued"})

    item = local_todos.add_item(
        "alice",
        "Route this scheduled task through the central metadata stores",
        source="manual",
        device="web",
        defer_until_idle=False,
    )
    schedule = local_schedules.create(user="alice", todo_id=item["id"], run_at=refiner_web._now_iso())

    updated = None
    for _ in range(40):
        scheduler.run_once()
        updated = local_schedules.get_item(schedule["id"], user="alice")
        if updated and updated["status"] == "completed":
            break
        time.sleep(0.02)

    assert updated is not None
    assert updated["status"] == "completed"
    stored = local_todos.get_item("alice", item["id"])
    assert stored["status"] == "done"
    assert stored["last_result"]["job_id"] == "job-42"
    assert todo_documents.docs["alice"]["items"][0]["status"] == "done"
    assert any(entry["id"] == schedule["id"] for entry in schedule_documents.doc["items"])


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner tests require a real Flask runtime")
def test_session_snapshot_rehydrates_from_postgres_room_store(monkeypatch, tmp_path):
    room_store = FakeSessionRooms()
    history = refiner_web.PostgresSessionHistoryStore(str(tmp_path / "sessions"), room_store, max_events=20)
    monkeypatch.setattr(refiner_web, "session_history", history)

    session_store = refiner_web.SessionStore(ttl_sec=600)
    first = session_store.get_or_create("job-7", "project-7", "alice", "owner", room_id="room-7")

    persisted = history.load("room-7")
    assert persisted is not None
    assert persisted["snapshot"]["participants"][0]["user"] == "alice"

    reloaded_store = refiner_web.SessionStore(ttl_sec=600)
    reloaded = reloaded_store.get("room-7")
    assert reloaded is not None
    snapshot = reloaded.snapshot()
    assert snapshot["job_id"] == "job-7"
    assert snapshot["participants"][0]["user"] == "alice"
    assert first.room_id == reloaded.room_id


@pytest.mark.skipif(not HAS_REAL_FLASK, reason="Flask-backed Refiner tests require a real Flask runtime")
def test_job_persist_syncs_metadata_to_central_store(monkeypatch, tmp_path):
    job_store = FakeJobStore()
    monkeypatch.setattr(refiner_web, "CENTRAL_STORE", SimpleNamespace(jobs=job_store))

    job_dir = tmp_path / "job-123"
    job_dir.mkdir(parents=True, exist_ok=True)
    job = refiner_web.Job(
        job_id="job-123",
        payload={"workflow": "project_solver", "project_id": "project-123"},
        owner="alice",
        log_path=str(job_dir / "job.log"),
        events_path=str(job_dir / "events.jsonl"),
        meta_path=str(job_dir / refiner_web.JOB_META_FILENAME),
    )

    job.persist(force=True)

    assert job_store.records["job-123"]["owner"] == "alice"
    assert job_store.records["job-123"]["project_id"] == "project-123"
