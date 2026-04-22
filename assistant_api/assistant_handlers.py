"""HTTP handlers for extracted assistant routes."""

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


def build_assistant_handlers(deps: AssistantPipelineDependencies) -> HandlerMap:
    """Build thin Flask handlers that delegate to the assistant pipeline."""

    def assistant_rag_mcp() -> Response:
        try:
            result = assistant_service.assistant_rag_mcp(
                deps,
                user=deps.current_user(),
                payload=load_json_object(),
            )
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    def assistant_requirements() -> Response:
        try:
            result = assistant_service.assistant_requirements(
                deps,
                user=deps.current_user(),
                payload=load_json_object(),
            )
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    def assistant_form_fill() -> Response:
        try:
            result = assistant_service.assistant_form_fill(
                deps,
                user=deps.current_user(),
                payload=load_json_object(),
            )
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    def playground_plan() -> Response:
        try:
            result = assistant_service.playground_plan(
                deps,
                user=deps.current_user(),
                payload=load_json_object(),
            )
            return _json_response(result.payload, result.status_code)
        except ServiceError as exc:
            return _json_response(exc.to_payload(), exc.status_code)

    assistant_rag_mcp.__name__ = "assistant_rag_mcp"
    assistant_requirements.__name__ = "assistant_requirements"
    assistant_form_fill.__name__ = "assistant_form_fill"
    playground_plan.__name__ = "playground_plan"

    return {
        "assistant_rag_mcp": assistant_rag_mcp,
        "assistant_requirements": assistant_requirements,
        "assistant_form_fill": assistant_form_fill,
        "playground_plan": playground_plan,
    }
