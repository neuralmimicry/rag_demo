from __future__ import annotations

from typing import Callable


def register_rag_routes(
    app,
    *,
    rag_indexes: Callable,
    rag_index_create: Callable,
    rag_index_delete: Callable,
    rag_query: Callable,
) -> None:
    """Register RAG-only routes separately from jobs and workspace APIs."""

    app.add_url_rule("/api/rag/indexes", view_func=rag_indexes, methods=["GET"])
    app.add_url_rule("/api/rag/index", view_func=rag_index_create, methods=["POST"])
    app.add_url_rule(
        "/api/rag/index/<name>",
        view_func=rag_index_delete,
        methods=["DELETE"],
    )
    app.add_url_rule("/api/rag/query", view_func=rag_query, methods=["POST"])
