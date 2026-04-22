"""HTTP handlers for extracted RAG routes."""

from __future__ import annotations

from typing import Callable, Dict

from flask import Response, jsonify

from assistant_api.schemas import load_json_object
from assistant_pipeline.contracts import ServiceError
from assistant_pipeline.dependencies import AssistantPipelineDependencies
from assistant_pipeline import service as assistant_service


HandlerMap = Dict[str, Callable[..., Response]]


def _json_response(payload, status_code: int = 200) -> Response:
    return jsonify(payload), status_code


def build_rag_handlers(deps: AssistantPipelineDependencies) -> HandlerMap:
    """Build thin Flask handlers that delegate to the assistant pipeline."""

    def rag_indexes() -> Response:
        try:
            result = assistant_service.rag_indexes(deps, user=deps.current_user())
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    def rag_index_create() -> Response:
        try:
            result = assistant_service.rag_index_create(
                deps,
                user=deps.current_user(),
                payload=load_json_object(),
            )
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    def rag_index_delete(name: str) -> Response:
        try:
            result = assistant_service.rag_index_delete(deps, user=deps.current_user(), name=name)
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    def rag_query() -> Response:
        try:
            result = assistant_service.rag_query(
                deps,
                user=deps.current_user(),
                payload=load_json_object(),
            )
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    rag_indexes.__name__ = "rag_indexes"
    rag_index_create.__name__ = "rag_index_create"
    rag_index_delete.__name__ = "rag_index_delete"
    rag_query.__name__ = "rag_query"

    return {
        "rag_indexes": rag_indexes,
        "rag_index_create": rag_index_create,
        "rag_index_delete": rag_index_delete,
        "rag_query": rag_query,
    }
