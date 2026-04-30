from __future__ import annotations

from typing import Callable


def register_assistant_routes(
    app,
    *,
    assistant_rag_mcp: Callable,
    assistant_requirements: Callable,
    assistant_form_fill: Callable,
    playground_plan: Callable,
    execution_plan: Callable,
) -> None:
    """Register assistant-oriented routes in one place."""
    app.add_url_rule("/api/assistant/rag-mcp", view_func=assistant_rag_mcp, methods=["POST"])
    app.add_url_rule("/api/assistant/requirements", view_func=assistant_requirements, methods=["POST"])
    app.add_url_rule("/api/assistant/form-fill", view_func=assistant_form_fill, methods=["POST"])
    app.add_url_rule("/api/playground/plan", view_func=playground_plan, methods=["POST"])
    app.add_url_rule("/api/execution/plan", view_func=execution_plan, methods=["POST"])
