from __future__ import annotations

from refiner.integrations.atlassian import actions


def test_execute_atlassian_action_preview_skips_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        actions,
        "load_config",
        lambda _path="config.json": {
            "instances": [{"name": "NeuralMimicryJira", "jira_url": "https://neuralmimicry.atlassian.net"}]
        },
    )

    def _unexpected_credentials(*_args, **_kwargs):
        raise AssertionError("credentials should not be requested for previews")

    monkeypatch.setattr(actions, "get_credentials", _unexpected_credentials)

    result = actions.execute_atlassian_action(
        {
            "product": "jira",
            "action": "create_issue",
            "preview": True,
            "arguments": {"project_key": "OPS", "summary": "Retry backlog follow-up"},
        }
    )

    assert result["status"] == "preview"
    assert result["preview"] is True
    assert result["base_url"] == "https://neuralmimicry.atlassian.net"


def test_execute_atlassian_action_creates_jira_issue(monkeypatch) -> None:
    captured = {}

    class _FakeJiraClient:
        def __init__(self, base_url, auth):
            captured["base_url"] = base_url
            captured["auth"] = auth

        def create_issue(self, **kwargs):
            captured["kwargs"] = kwargs
            return {
                "issue_key": "OPS-321",
                "issue_id": "10001",
                "url": "https://neuralmimicry.atlassian.net/browse/OPS-321",
            }

    monkeypatch.setattr(
        actions,
        "load_config",
        lambda _path="config.json": {
            "instances": [{"name": "NeuralMimicryJira", "jira_url": "https://neuralmimicry.atlassian.net"}]
        },
    )
    monkeypatch.setattr(actions, "get_credentials", lambda _instance=None, allow_prompt=False: ("user", "token"))
    monkeypatch.setattr(actions, "JiraClient", _FakeJiraClient)

    result = actions.execute_atlassian_action(
        {
            "product": "jira",
            "action": "create_issue",
            "instance": "NeuralMimicryJira",
            "arguments": {"project_key": "OPS", "summary": "Retry backlog follow-up"},
        }
    )

    assert captured["base_url"] == "https://neuralmimicry.atlassian.net"
    assert captured["auth"] == ("user", "token")
    assert captured["kwargs"]["project_key"] == "OPS"
    assert result["status"] == "applied"
    assert result["result"]["issue_key"] == "OPS-321"
    assert result["instance"] == "NeuralMimicryJira"


def test_execute_atlassian_action_updates_confluence_page(monkeypatch) -> None:
    captured = {}

    class _FakeConfluenceClient:
        def __init__(self, base_url, auth):
            captured["base_url"] = base_url
            captured["auth"] = auth

        def update_page(self, page_id, *, title, body_storage, parent_id=None):
            captured["page_id"] = page_id
            captured["title"] = title
            captured["body_storage"] = body_storage
            captured["parent_id"] = parent_id
            return {
                "page_id": page_id,
                "title": title or "Existing title",
                "space_key": "OPS",
                "url": "https://neuralmimicry.atlassian.net/wiki/pages/123",
            }

    monkeypatch.setattr(
        actions,
        "load_config",
        lambda _path="config.json": {
            "instances": [{"name": "NeuralMimicryConfluence", "confluence_url": "https://neuralmimicry.atlassian.net"}]
        },
    )
    monkeypatch.setattr(actions, "get_credentials", lambda _instance=None, allow_prompt=False: ("user", "token"))
    monkeypatch.setattr(actions, "ConfluenceClient", _FakeConfluenceClient)

    result = actions.execute_atlassian_action(
        {
            "product": "confluence",
            "action": "update_page",
            "arguments": {"page_id": "123", "title": "Retry Notes", "body": "Updated guidance"},
        }
    )

    assert captured["base_url"] == "https://neuralmimicry.atlassian.net"
    assert captured["page_id"] == "123"
    assert captured["title"] == "Retry Notes"
    assert captured["body_storage"] == "<p>Updated guidance</p>"
    assert result["status"] == "applied"
    assert result["product"] == "confluence"
