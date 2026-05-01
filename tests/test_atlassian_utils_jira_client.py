from __future__ import annotations

from refiner.integrations.atlassian.utils import JiraClient


def _issue(key: str, summary: str) -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "issuetype": {"name": "Task"},
            "status": {"name": "To Do"},
            "priority": {"name": "Medium"},
            "labels": [],
            "assignee": None,
            "updated": "2026-04-23T10:00:00.000+0000",
            "created": "2026-04-23T09:00:00.000+0000",
            "description": "",
            "reporter": None,
            "comment": {"total": 0, "comments": []},
            "parent": None,
        },
    }


def test_jira_client_fetch_issues_uses_search_jql_pagination(monkeypatch) -> None:
    client = JiraClient("https://neuralmimicry.atlassian.net", ("user", "token"))
    calls = []
    responses = [
        {
            "issues": [_issue("KAN-3", "GitHub inventory adapter")],
            "nextPageToken": "page-2",
        },
        {
            "issues": [_issue("KAN-4", "Ansible estate discovery")],
            "isLast": True,
        },
    ]

    def _fake_get(path: str, params=None):
        calls.append((path, dict(params or {})))
        return responses[len(calls) - 1]

    monkeypatch.setattr(client, "get", _fake_get)

    issues = client.fetch_issues("key in (KAN-3, KAN-4)", limit=2)

    assert [issue.key for issue in issues] == ["KAN-3", "KAN-4"]
    assert calls[0][0] == "/rest/api/3/search/jql"
    assert calls[0][1]["jql"] == "key in (KAN-3, KAN-4)"
    assert "nextPageToken" not in calls[0][1]
    assert calls[1][1]["nextPageToken"] == "page-2"


def test_jira_client_upsert_comment_creates_v3_adf_comment(monkeypatch) -> None:
    client = JiraClient("https://neuralmimicry.atlassian.net", ("user", "token"))
    created = {}

    monkeypatch.setattr(client, "get", lambda path, params=None: {"comments": []})

    def _fake_post(path: str, payload: dict, params=None):
        created["path"] = path
        created["payload"] = payload
        return {"id": "501"}

    monkeypatch.setattr(client, "post", _fake_post)

    result = client.upsert_comment("KAN-3", "marker-kan3", "Phase 1 shipped")

    assert result == {"action": "created", "comment_id": "501"}
    assert created["path"] == "/rest/api/3/issue/KAN-3/comment"
    assert created["payload"]["body"]["type"] == "doc"
    paragraphs = created["payload"]["body"]["content"]
    assert paragraphs[0]["content"][0]["text"] == "Phase 1 shipped"
    assert "marker-kan3" in paragraphs[-1]["content"][0]["text"]
