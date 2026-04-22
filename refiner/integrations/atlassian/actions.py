"""Guarded Jira and Confluence write actions for assistant-initiated flows."""

from __future__ import annotations

import html
from typing import Any, Dict, Mapping, Optional, Tuple

from refiner.config_loader import load_config
from refiner.credentials import get_credentials
from refiner.integrations.atlassian.utils import (
    ConfluenceClient,
    JiraClient,
    normalise_confluence_base_url,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _require(arguments: Mapping[str, Any], key: str) -> str:
    value = _clean(arguments.get(key))
    if not value:
        raise ValueError(f"Atlassian action argument '{key}' is required.")
    return value


def _storage_body(arguments: Mapping[str, Any]) -> str:
    storage_value = _clean(arguments.get("body_storage") or arguments.get("body_html"))
    if storage_value:
        return storage_value
    body = _clean(arguments.get("body") or arguments.get("content") or arguments.get("text"))
    if not body:
        raise ValueError("Atlassian action argument 'body' is required.")
    escaped = html.escape(body).replace("\n", "<br/>")
    return f"<p>{escaped}</p>"


def _load_runtime_config(config: Optional[Mapping[str, Any]], config_path: str) -> Dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    return load_config(config_path)


def _resolve_instance_config(
    product: str,
    *,
    config: Mapping[str, Any],
    instance_name: str = "",
) -> Tuple[str, str]:
    field_name = "jira_url" if product == "jira" else "confluence_url"
    instances = config.get("instances") if isinstance(config.get("instances"), list) else []
    candidates = [entry for entry in instances if isinstance(entry, dict)]

    if instance_name:
        wanted = instance_name.casefold()
        for entry in candidates:
            if _clean(entry.get("name")).casefold() == wanted:
                base_url = _clean(entry.get(field_name) or entry.get("jira_url") or entry.get("confluence_url"))
                if base_url:
                    return _clean(entry.get("name")) or instance_name, base_url
                raise ValueError(f"Configured Atlassian instance '{instance_name}' does not define {field_name}.")
        raise ValueError(f"Unknown Atlassian instance '{instance_name}'.")

    for entry in candidates:
        base_url = _clean(entry.get(field_name))
        if base_url:
            return _clean(entry.get("name")) or product, base_url
    if product == "confluence":
        for entry in candidates:
            base_url = _clean(entry.get("jira_url"))
            if base_url:
                return _clean(entry.get("name")) or product, base_url
    raise ValueError(f"No configured Atlassian {product} instance is available.")


def _load_auth(instance_name: str) -> Tuple[str, str]:
    username, password = get_credentials(instance_name or None, allow_prompt=False)
    if username and password:
        return username, password
    username, password = get_credentials(None, allow_prompt=False)
    if username and password:
        return username, password
    raise ValueError("Jira credentials are not configured for Atlassian write actions.")


def _preview_result(
    *,
    product: str,
    action: str,
    instance: str,
    base_url: str,
    arguments: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "status": "preview",
        "applied": False,
        "preview": True,
        "product": product,
        "action": action,
        "instance": instance,
        "base_url": base_url,
        "arguments": dict(arguments or {}),
    }


def _jira_action(client: JiraClient, action: str, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if action == "create_issue":
        project_key = _require(arguments, "project_key")
        summary = _require(arguments, "summary")
        issue_type = _clean(arguments.get("issue_type") or "Task")
        description = _clean(arguments.get("description"))
        fields = arguments.get("fields")
        fields_dict = dict(fields) if isinstance(fields, Mapping) else {}
        result = client.create_issue(
            project_key=project_key,
            summary=summary,
            issue_type=issue_type,
            description=description,
            fields=fields_dict,
        )
    elif action == "update_issue":
        issue_key = _require(arguments, "issue_key")
        fields = arguments.get("fields")
        fields_dict = dict(fields) if isinstance(fields, Mapping) else {}
        summary = _clean(arguments.get("summary"))
        description = _clean(arguments.get("description"))
        if summary:
            fields_dict.setdefault("summary", summary)
        if description:
            fields_dict.setdefault("description", description)
        if not fields_dict:
            raise ValueError("Atlassian action argument 'fields' is required for Jira issue updates.")
        result = client.update_issue(issue_key, fields=fields_dict)
    elif action == "transition_issue":
        issue_key = _require(arguments, "issue_key")
        result = client.transition_issue(
            issue_key,
            transition_id=_clean(arguments.get("transition_id")) or None,
            transition_name=_clean(arguments.get("transition_name")) or None,
        )
    elif action == "upsert_comment":
        issue_key = _require(arguments, "issue_key")
        marker_id = _require(arguments, "marker_id")
        body = _require(arguments, "body")
        result = client.upsert_comment(issue_key, marker_id, body)
        result["issue_key"] = issue_key
        result["url"] = f"{client.base_url.rstrip('/')}/browse/{issue_key}"
    else:
        raise ValueError(f"Unsupported Jira action '{action}'.")
    return {
        "status": "applied",
        "applied": True,
        "preview": False,
        "product": "jira",
        "action": action,
        "result": result,
    }


def _confluence_action(client: ConfluenceClient, action: str, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if action == "create_page":
        result = client.create_page(
            space_key=_require(arguments, "space_key"),
            title=_require(arguments, "title"),
            body_storage=_storage_body(arguments),
            parent_id=_clean(arguments.get("parent_id")) or None,
        )
    elif action == "update_page":
        result = client.update_page(
            _require(arguments, "page_id"),
            title=_clean(arguments.get("title")) or None,
            body_storage=_storage_body(arguments),
            parent_id=_clean(arguments.get("parent_id")) or None,
        )
    elif action == "upsert_comment":
        page_id = _require(arguments, "page_id")
        marker_id = _require(arguments, "marker_id")
        result = client.upsert_comment(page_id, marker_id, _storage_body(arguments))
        result["page_id"] = page_id
        result["url"] = f"{normalise_confluence_base_url(client.base_url).rstrip('/')}/pages/{page_id}"
    else:
        raise ValueError(f"Unsupported Confluence action '{action}'.")
    return {
        "status": "applied",
        "applied": True,
        "preview": False,
        "product": "confluence",
        "action": action,
        "result": result,
    }


def execute_atlassian_action(
    action_request: Mapping[str, Any],
    *,
    config: Optional[Mapping[str, Any]] = None,
    config_path: str = "config.json",
) -> Dict[str, Any]:
    """Execute one explicit Atlassian write action or return its preview."""

    product = _clean(action_request.get("product")).lower()
    action = _clean(action_request.get("action")).lower()
    if product not in {"jira", "confluence"}:
        raise ValueError("Atlassian action product must be 'jira' or 'confluence'.")
    if not action:
        raise ValueError("Atlassian action name is required.")
    arguments = action_request.get("arguments")
    if arguments is not None and not isinstance(arguments, Mapping):
        raise ValueError("Atlassian action arguments must be an object.")
    arguments_dict = dict(arguments or {})
    preview = _flag(action_request.get("preview")) or _flag(action_request.get("dry_run"))
    instance_name = _clean(action_request.get("instance"))
    runtime_config = _load_runtime_config(config, config_path)
    resolved_instance, base_url = _resolve_instance_config(
        product,
        config=runtime_config,
        instance_name=instance_name,
    )

    if preview:
        return _preview_result(
            product=product,
            action=action,
            instance=resolved_instance,
            base_url=base_url,
            arguments=arguments_dict,
        )

    auth = _load_auth(resolved_instance)
    if product == "jira":
        result = _jira_action(JiraClient(base_url, auth), action, arguments_dict)
    else:
        result = _confluence_action(ConfluenceClient(base_url, auth), action, arguments_dict)
    result["instance"] = resolved_instance
    result["base_url"] = base_url
    return result
