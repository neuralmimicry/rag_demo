import pytest

from llm_providers import (
    LLMProvider,
    LLMQuotaError,
    LLMResponse,
    register_event_callback,
    request_category,
    unregister_event_callback,
)


class _SuccessProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.name = "mock"
        self.model = "mock-model"

    def _predict_impl(self, messages, **_kwargs):
        return LLMResponse(text="ok", raw={}, provider=self.name, model=self.model)

    def transcribe(self, file_path: str, timeout=None) -> str:
        raise NotImplementedError

    def health_check(self, timeout=None):
        return {"ok": True}


class _QuotaProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.name = "mock"
        self.model = "mock-model"

    def _predict_impl(self, messages, **_kwargs):
        raise LLMQuotaError("quota exhausted for test")

    def transcribe(self, file_path: str, timeout=None) -> str:
        raise NotImplementedError

    def health_check(self, timeout=None):
        return {"ok": True}


def _capture_events():
    events = []

    def _callback(event):
        if isinstance(event, dict) and event.get("type") == "llm_request":
            events.append(event)

    return events, _callback


def test_predict_emits_successful_llm_request_event():
    events, callback = _capture_events()
    register_event_callback(callback)
    try:
        provider = _SuccessProvider()
        with request_category("codingagent"):
            response = provider.predict(
                [{"role": "user", "content": "Build a parser"}],
                max_tokens=256,
                system="You are precise.",
                reasoning_effort="high",
            )
    finally:
        unregister_event_callback(callback)

    assert response.text == "ok"
    assert len(events) == 1
    event = events[0]
    assert event["provider"] == "mock"
    assert event["model"] == "mock-model"
    assert event["category"] == "codingagent"
    assert event["outcome"] == "success"
    assert event["max_tokens"] == 256
    assert event["reasoning_effort"] == "high"
    assert event["input_chars"] == len("You are precise.") + len("Build a parser")
    assert event["estimated_input_tokens"] >= 1
    assert event["latency_ms"] >= 0


def test_predict_emits_quota_error_llm_request_event():
    events, callback = _capture_events()
    register_event_callback(callback)
    try:
        provider = _QuotaProvider()
        with pytest.raises(LLMQuotaError):
            provider.predict([{"role": "user", "content": "Hello"}], max_tokens=32)
    finally:
        unregister_event_callback(callback)

    assert len(events) == 1
    event = events[0]
    assert event["outcome"] == "quota_error"
    assert event["error_class"] == "LLMQuotaError"
    assert "quota exhausted" in event["error_detail"]
