import json
import logging
import os
from unittest.mock import MagicMock, patch

from refiner.llm_providers import OpenAIProvider, _openai_model_supports_reasoning_effort


def _mock_openai_response(status_code, payload):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = json.dumps(payload)
    return response


def test_openai_reasoning_support_helper_is_conservative():
    assert _openai_model_supports_reasoning_effort("o3-mini")
    assert _openai_model_supports_reasoning_effort("openai/codex-mini-latest")
    assert not _openai_model_supports_reasoning_effort("gpt-4o-mini")
    assert not _openai_model_supports_reasoning_effort("openai:gpt-4.1-mini")


@patch("llm_providers._http_post")
def test_openai_unsupported_reasoning_model_uses_chat_without_reasoning(mock_post):
    mock_post.return_value = _mock_openai_response(
        200,
        {"choices": [{"message": {"content": "Hello from chat"}}]},
    )

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
        provider = OpenAIProvider(model="gpt-4o-mini")
        response = provider.predict(
            [{"role": "user", "content": "Hi"}],
            system="You are concise.",
            reasoning_effort="medium",
        )

    assert response.text == "Hello from chat"
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0].endswith("/chat/completions")
    payload = mock_post.call_args.kwargs["json_payload"]
    assert payload["model"] == "gpt-4o-mini"
    assert "reasoning" not in payload


@patch("llm_providers._http_post")
def test_openai_supported_reasoning_model_uses_responses_with_reasoning(mock_post):
    mock_post.return_value = _mock_openai_response(
        200,
        {"output_text": "Hello from responses"},
    )

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
        provider = OpenAIProvider(model="o3-mini")
        response = provider.predict(
            [{"role": "user", "content": "Hi"}],
            system="You are concise.",
            reasoning_effort="medium",
        )

    assert response.text == "Hello from responses"
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0].endswith("/responses")
    payload = mock_post.call_args.kwargs["json_payload"]
    assert payload["reasoning"] == {"effort": "medium"}


@patch("llm_providers._http_post")
def test_openai_responses_retry_without_reasoning_when_api_rejects_parameter(mock_post):
    mock_post.side_effect = [
        _mock_openai_response(
            400,
            {
                "error": {
                    "code": "unsupported_parameter",
                    "message": "Unsupported parameter: 'reasoning.effort' is not supported with this model.",
                }
            },
        ),
        _mock_openai_response(
            200,
            {"output_text": "Recovered after dropping reasoning"},
        ),
    ]

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_USE_RESPONSES": "1",
        },
        clear=True,
    ):
        provider = OpenAIProvider(model="o3-mini")
        response = provider.predict(
            [{"role": "user", "content": "Hi"}],
            system="You are concise.",
            reasoning_effort="high",
        )

    assert response.text == "Recovered after dropping reasoning"
    assert mock_post.call_count == 2

    first_payload = mock_post.call_args_list[0].kwargs["json_payload"]
    second_payload = mock_post.call_args_list[1].kwargs["json_payload"]

    assert mock_post.call_args_list[0].args[0].endswith("/responses")
    assert mock_post.call_args_list[1].args[0].endswith("/responses")
    assert first_payload["reasoning"] == {"effort": "high"}
    assert "reasoning" not in second_payload
    assert second_payload["temperature"] == 0.2


@patch("llm_providers._http_post")
def test_openai_unsupported_reasoning_log_emitted_once_per_provider(mock_post, caplog):
    mock_post.return_value = _mock_openai_response(
        200,
        {"choices": [{"message": {"content": "Hello from chat"}}]},
    )

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
        provider = OpenAIProvider(model="gpt-4o-mini")
        caplog.set_level(logging.INFO)
        provider.predict(
            [{"role": "user", "content": "Hi"}],
            system="You are concise.",
            reasoning_effort="medium",
        )
        provider.predict(
            [{"role": "user", "content": "Hi again"}],
            system="You are concise.",
            reasoning_effort="medium",
        )

    assert caplog.text.count("does not support reasoning.effort") == 1
