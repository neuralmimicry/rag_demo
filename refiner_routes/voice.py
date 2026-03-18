from __future__ import annotations

from typing import Callable


def register_voice_routes(
    app,
    *,
    api_voice_tokens: Callable,
    api_voice_token_delete: Callable,
    api_voice_capture: Callable,
    api_voice_siri: Callable,
    api_voice_alexa: Callable,
    api_voice_google: Callable,
    api_voice_stt: Callable,
) -> None:
    """Register voice-related API routes in one place."""
    app.add_url_rule("/api/voice/tokens", view_func=api_voice_tokens, methods=["GET", "POST"])
    app.add_url_rule("/api/voice/tokens/<token_id>", view_func=api_voice_token_delete, methods=["DELETE"])
    app.add_url_rule("/api/voice/capture", view_func=api_voice_capture, methods=["GET", "POST"])
    app.add_url_rule("/api/voice/siri", view_func=api_voice_siri, methods=["GET", "POST"])
    app.add_url_rule("/api/voice/alexa", view_func=api_voice_alexa, methods=["POST"])
    app.add_url_rule("/api/voice/google", view_func=api_voice_google, methods=["POST"])
    app.add_url_rule("/api/voice/stt", view_func=api_voice_stt, methods=["POST"])

