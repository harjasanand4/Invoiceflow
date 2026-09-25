"""Rate limiting, backoff and the Gemini client, without calling any real API."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.extraction.llm import GeminiClient, RateLimiter, call_with_backoff


class ApiError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def flaky(failures: list[Exception], result="ok"):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if failures:
            raise failures.pop(0)
        return result

    return fn, calls


def test_rate_limit_errors_are_retried_with_growing_waits():
    sleeps: list[float] = []
    fn, calls = flaky([ApiError(429), ApiError(429), ApiError(503)])
    assert call_with_backoff(fn, sleep=sleeps.append) == "ok"
    assert calls["n"] == 4
    assert sleeps[0] < sleeps[1] < sleeps[2]  # exponential backoff


def test_bad_api_key_is_not_retried():
    sleeps: list[float] = []
    fn, calls = flaky([ApiError(401)])
    with pytest.raises(ApiError):
        call_with_backoff(fn, sleep=sleeps.append)
    assert calls["n"] == 1 and sleeps == []


def test_gives_up_after_max_attempts():
    fn, calls = flaky([ApiError(429)] * 10)
    with pytest.raises(ApiError):
        call_with_backoff(fn, attempts=3, sleep=lambda s: None)
    assert calls["n"] == 3


def test_rate_limiter_spaces_out_calls():
    now = {"t": 100.0}
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        now["t"] += seconds

    limiter = RateLimiter(6.0, clock=lambda: now["t"], sleep=sleep)
    limiter.wait()          # first call: no wait
    now["t"] += 1.0
    limiter.wait()          # 1s later: must wait 5 more
    assert slept == [pytest.approx(5.0)]


def test_gemini_client_parses_response_and_retries_real_rate_limit_error():
    errors = pytest.importorskip("google.genai.errors")
    payload = {"vendor_name": "Acme Ltd.", "invoice_number": "INV-1", "total": 10.5, "line_items": []}
    attempts = {"n": 0}

    class FakeModels:
        def generate_content(self, model, contents, config):
            attempts["n"] += 1
            if attempts["n"] == 1:  # the real error the SDK raises on a free-tier rate limit
                raise errors.APIError(429, {"error": {"code": 429, "message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}})
            assert model == "gemini-2.5-flash" and "Invoice text" in contents[0]
            return SimpleNamespace(text="```json\n" + json.dumps(payload) + "\n```")

    import app.extraction.llm as llm

    original = llm.call_with_backoff
    llm.call_with_backoff = lambda fn: original(fn, sleep=lambda s: None)
    try:
        client = GeminiClient("gemini-2.5-flash", client=SimpleNamespace(models=FakeModels()))
        assert client.extract_json("some invoice text", None) == payload
    finally:
        llm.call_with_backoff = original
    assert attempts["n"] == 2


def test_gemini_empty_response_is_an_error():
    pytest.importorskip("google.genai")

    class FakeModels:
        def generate_content(self, model, contents, config):
            return SimpleNamespace(text=None)

    client = GeminiClient("gemini-2.5-flash", client=SimpleNamespace(models=FakeModels()))
    with pytest.raises(ValueError, match="empty response"):
        client.extract_json("text", None)


def test_daily_quota_stops_immediately_instead_of_retrying():
    errors = pytest.importorskip("google.genai.errors")
    from app.extraction.llm import QuotaExhaustedError

    # The exact error shape Gemini's free tier returns when the per-day quota is gone.
    daily = errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 20",
        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                     "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
    sleeps: list[float] = []
    fn, calls = flaky([daily])
    with pytest.raises(QuotaExhaustedError):
        call_with_backoff(fn, sleep=sleeps.append)
    assert calls["n"] == 1 and sleeps == []


def test_per_minute_rate_limit_is_still_retried():
    errors = pytest.importorskip("google.genai.errors")
    per_minute = errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded", "details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}})
    fn, calls = flaky([per_minute])
    assert call_with_backoff(fn, sleep=lambda s: None) == "ok"
    assert calls["n"] == 2


def _groq_client(responses: list):
    """A real OpenAI SDK client pointed at a fake Groq server."""
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("openai")
    from app.extraction.llm import OpenAICompatibleClient

    seen: list = []

    def handler(request):
        seen.append(request)
        status, body = responses.pop(0)
        return httpx.Response(status, json=body)

    client = OpenAICompatibleClient(
        "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", "gsk_test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return client, seen


def _chat_ok(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}


def _groq_429(message: str) -> dict:
    return {"error": {"message": message, "type": "tokens", "code": "rate_limit_exceeded"}}


def test_groq_client_sends_json_mode_request_and_parses_answer():
    payload = {"vendor_name": "Acme Ltd.", "invoice_number": "INV-1", "total": 10.5, "line_items": []}
    client, seen = _groq_client([(200, _chat_ok(json.dumps(payload)))])
    assert client.extract_json("some invoice text", None) == payload
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/openai/v1/chat/completions"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["response_format"] == {"type": "json_object"}
    assert "some invoice text" in body["messages"][1]["content"]


def test_groq_per_minute_limit_is_retried(monkeypatch):
    import app.extraction.llm as llm

    original = llm.call_with_backoff
    monkeypatch.setattr(llm, "call_with_backoff", lambda fn: original(fn, sleep=lambda s: None))
    client, seen = _groq_client([
        (429, _groq_429("Rate limit reached for model `openai/gpt-oss-120b` on tokens per minute (TPM): Limit 8000")),
        (200, _chat_ok('{"total": 1.0, "line_items": []}')),
    ])
    assert client.extract_json("text", None)["total"] == 1.0
    assert len(seen) == 2


def test_groq_daily_limit_stops_immediately():
    from app.extraction.llm import QuotaExhaustedError

    client, seen = _groq_client([
        (429, _groq_429("Rate limit reached for model `openai/gpt-oss-120b` on tokens per day (TPD): Limit 200000")),
    ])
    with pytest.raises(QuotaExhaustedError):
        client.extract_json("text", None)
    assert len(seen) == 1


def test_groq_needs_a_key():
    pytest.importorskip("openai")
    from app.extraction.llm import OpenAICompatibleClient

    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        OpenAICompatibleClient("m", "https://api.groq.com/openai/v1", None)
