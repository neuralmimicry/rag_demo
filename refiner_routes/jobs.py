from __future__ import annotations

from typing import Callable, Dict


def register_jobs_routes(app, handlers: Dict[str, Callable]) -> None:
    """Register jobs/workflow/token/rag/mcp/task routes."""
    app.add_url_rule("/api/todos", view_func=handlers["api_todos"], methods=["GET", "POST"])
    app.add_url_rule("/api/todos/next", view_func=handlers["api_todo_next"])
    app.add_url_rule(
        "/api/todos/<todo_id>/route",
        view_func=handlers["api_todo_route"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/todos/<todo_id>",
        view_func=handlers["api_todo_detail"],
        methods=["PATCH", "DELETE"],
    )

    app.add_url_rule("/api/projects", view_func=handlers["api_projects"], methods=["GET", "POST"])
    app.add_url_rule(
        "/api/projects/<project_id>",
        view_func=handlers["api_project_detail"],
        methods=["PATCH", "DELETE"],
    )
    app.add_url_rule("/api/teams", view_func=handlers["api_teams"], methods=["GET", "POST"])
    app.add_url_rule(
        "/api/teams/<team_id>",
        view_func=handlers["api_team_detail"],
        methods=["PATCH", "DELETE"],
    )
    app.add_url_rule(
        "/api/teams/<team_id>/tokens",
        view_func=handlers["api_team_tokens"],
        methods=["GET", "POST"],
    )
    app.add_url_rule("/api/access/tree", view_func=handlers["api_access_tree"])

    app.add_url_rule("/api/sessions", view_func=handlers["api_sessions"], methods=["POST"])
    app.add_url_rule("/api/sessions/<session_id>", view_func=handlers["api_session_detail"])
    app.add_url_rule(
        "/api/sessions/<session_id>/leave",
        view_func=handlers["api_session_leave"],
        methods=["POST"],
    )
    app.add_url_rule("/api/sessions/<session_id>/stream", view_func=handlers["api_session_stream"])
    app.add_url_rule("/api/sessions/<session_id>/history", view_func=handlers["api_session_history"])
    app.add_url_rule("/api/sessions/history", view_func=handlers["api_sessions_history"])

    app.add_url_rule("/api/jobs/estimate", view_func=handlers["job_estimate"], methods=["POST"])
    app.add_url_rule(
        "/api/requirements/import",
        view_func=handlers["import_requirements"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/requirements/export",
        view_func=handlers["export_requirements"],
        methods=["POST"],
    )

    app.add_url_rule("/api/rag/indexes", view_func=handlers["rag_indexes"], methods=["GET"])
    app.add_url_rule("/api/rag/index", view_func=handlers["rag_index_create"], methods=["POST"])
    app.add_url_rule(
        "/api/rag/index/<name>",
        view_func=handlers["rag_index_delete"],
        methods=["DELETE"],
    )
    app.add_url_rule("/api/rag/query", view_func=handlers["rag_query"], methods=["POST"])

    app.add_url_rule("/api/mcp/servers", view_func=handlers["mcp_servers"], methods=["GET", "POST"])
    app.add_url_rule(
        "/api/mcp/servers/<name>",
        view_func=handlers["mcp_server_delete"],
        methods=["DELETE"],
    )
    app.add_url_rule(
        "/api/mcp/servers/<name>/tools",
        view_func=handlers["mcp_server_tools"],
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/mcp/servers/<name>/call",
        view_func=handlers["mcp_server_call"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/mcp/servers/<name>/resources",
        view_func=handlers["mcp_server_resources"],
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/mcp/servers/<name>/resource",
        view_func=handlers["mcp_server_resource"],
        methods=["POST"],
    )

    app.add_url_rule("/api/jobs/<job_id>/refunds", view_func=handlers["request_refund"], methods=["POST"])
    app.add_url_rule("/api/refunds", view_func=handlers["list_refunds"])
    app.add_url_rule(
        "/api/refunds/<job_id>/<request_id>/screen",
        view_func=handlers["screen_refund"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/refunds/<job_id>/<request_id>/decision",
        view_func=handlers["decide_refund"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/refunds/<job_id>/<request_id>/file/<filename>",
        view_func=handlers["refund_file"],
    )

    app.add_url_rule("/api/jobs", view_func=handlers["jobs"], methods=["GET", "POST"])
    app.add_url_rule("/api/jobs/<job_id>", view_func=handlers["job_detail"], methods=["GET", "DELETE"])
    app.add_url_rule(
        "/api/jobs/<job_id>/workspace",
        view_func=handlers["job_workspace"],
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/api/jobs/<job_id>/workspace/open",
        view_func=handlers["job_workspace_open"],
        methods=["POST"],
    )
    app.add_url_rule("/api/jobs/<job_id>/tasks", view_func=handlers["job_tasks"], methods=["GET"])
    app.add_url_rule(
        "/api/jobs/<job_id>/tasks/<task_id>",
        view_func=handlers["job_task_detail"],
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/jobs/<job_id>/tasks/<task_id>/cancel",
        view_func=handlers["job_task_cancel"],
        methods=["POST"],
    )
    app.add_url_rule("/api/jobs/<job_id>/editor/roots", view_func=handlers["job_editor_roots"])
    app.add_url_rule("/api/jobs/<job_id>/editor/list", view_func=handlers["job_editor_list"])
    app.add_url_rule(
        "/api/jobs/<job_id>/editor/file",
        view_func=handlers["job_editor_file"],
        methods=["GET", "PUT"],
    )
    app.add_url_rule(
        "/api/jobs/<job_id>/editor/ops",
        view_func=handlers["job_editor_ops"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/jobs/<job_id>/requirements/progress",
        view_func=handlers["job_requirements_progress"],
    )
    app.add_url_rule(
        "/api/jobs/<job_id>/requirements/summary",
        view_func=handlers["job_requirements_summary"],
    )
    app.add_url_rule("/api/jobs/<job_id>/logs", view_func=handlers["job_logs"])
    app.add_url_rule("/api/jobs/<job_id>/logs/stream", view_func=handlers["job_logs_stream"])
    app.add_url_rule(
        "/api/jobs/<job_id>/actions",
        view_func=handlers["job_actions"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/jobs/<job_id>/transfer",
        view_func=handlers["job_transfer"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/jobs/<job_id>/archive",
        view_func=handlers["job_archive"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/jobs/bulk-delete",
        view_func=handlers["jobs_bulk_delete"],
        methods=["POST"],
    )

    app.add_url_rule("/api/tokens", view_func=handlers["tokens"], methods=["GET", "POST"])
    app.add_url_rule("/api/tokens/ledger", view_func=handlers["tokens_ledger"])
    app.add_url_rule("/api/secrets", view_func=handlers["secrets"], methods=["GET", "POST"])
    app.add_url_rule("/api/secrets/<name>", view_func=handlers["delete_secret"], methods=["DELETE"])
    app.add_url_rule("/api/github/tree", view_func=handlers["github_tree"], methods=["POST"])
