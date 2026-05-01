import json
import os
import time

from refiner import main as m
def test_cache_compaction_dedups_and_trims(tmp_path, monkeypatch):
    # Point cache file to a temp location
    cache_path = tmp_path / ".issues_cache.jsonl"
    monkeypatch.setattr(m, "ISSUES_CACHE_FILE", str(cache_path), raising=False)
    monkeypatch.setattr(m, "ENABLE_CACHE", True, raising=False)

    now = int(time.time())
    old_ts = now - 10 * 24 * 3600  # 10 days ago

    # Prepare records: two for the same key (old and new), and one old for another key
    recs = [
        {"fetched_at": old_ts, "jql": "J1", "issue": {"key": "PRJ-1", "fields": {}}},
        {"fetched_at": now, "jql": "J2", "issue": {"key": "PRJ-1", "fields": {}}},  # latest should remain
        {"fetched_at": old_ts, "jql": "J3", "issue": {"key": "PRJ-2", "fields": {}}},  # should be trimmed by TTL
    ]

    with cache_path.open("w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")

    # Compact with TTL=7 days → drop old entries, keep latest per key
    written = m._compact_issues_cache(max_age_days=7)
    assert written >= 0
    # Read back and assert only PRJ-1 latest remains
    lines = cache_path.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["issue"]["key"] == "PRJ-1"
    assert int(rec["fetched_at"]) == now
