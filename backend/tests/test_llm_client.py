import httpx
import pytest

from app.core import AppError
from app.services.llm_client import LLMClient, parse_json_content


def test_parse_json_content_accepts_plain_json():
    assert parse_json_content('{"ugc_items": []}') == {"ugc_items": []}


def test_parse_json_content_extracts_json_from_markdown_fence():
    content = """
    下面是结果：
    ```json
    {"ugc_items": [{"note_id": "note_001", "mentioned_pois": []}]}
    ```
    """

    assert parse_json_content(content) == {"ugc_items": [{"note_id": "note_001", "mentioned_pois": []}]}


def test_parse_json_content_rejects_text_without_json():
    with pytest.raises(ValueError):
        parse_json_content("没有可解析的 JSON")


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def test_llm_client_respects_env_proxy_by_default(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    monkeypatch.delenv("LLM_TRUST_ENV", raising=False)

    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    payload = client.json_chat([{"role": "user", "content": "ping"}], step="extract_ugc")

    assert payload == {"ok": True}
    assert captured["trust_env"] is True


def test_llm_client_can_disable_env_proxy(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    monkeypatch.setenv("LLM_TRUST_ENV", "false")

    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    payload = client.json_chat([{"role": "user", "content": "ping"}], step="extract_ugc")

    assert payload == {"ok": True}
    assert captured["trust_env"] is False


def test_llm_client_maps_httpx_errors_to_app_error(monkeypatch):
    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)

    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    with pytest.raises(AppError) as exc_info:
        client.json_chat([{"role": "user", "content": "ping"}], step="extract_ugc")

    assert exc_info.value.code == "llm_request_failed"
    assert exc_info.value.step == "extract_ugc"


def test_llm_client_uses_deepseek_json_output_requirements(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")
    monkeypatch.setenv("LLM_READ_TIMEOUT_SECONDS", "650")

    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")
    payload = client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="plan_itinerary")

    assert payload == {"ok": True}
    body = captured["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 8192
    assert body["stream"] is False
    assert captured["timeout"].read == 650


def test_llm_client_retries_deepseek_transient_statuses(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return FakeResponse({"error": {"message": "server overloaded"}}, status_code=503)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda seconds: None)

    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    assert client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="plan_itinerary") == {"ok": True}
    assert len(calls) == 2


def test_llm_client_retries_gateway_timeout(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return FakeResponse({"error": {"message": "gateway timeout"}}, status_code=504)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda seconds: None)
    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    assert client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="plan_itinerary") == {"ok": True}
    assert len(calls) == 2


def test_llm_client_reports_safe_deepseek_error_message(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse({"error": {"message": "Insufficient Balance"}}, status_code=402)

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)

    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    with pytest.raises(AppError) as exc_info:
        client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="plan_itinerary")

    assert exc_info.value.code == "llm_request_failed"
    assert "DeepSeek API 返回 402" in exc_info.value.message
    assert "Insufficient Balance" in exc_info.value.message


def test_llm_client_classifies_empty_json_output(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    with pytest.raises(AppError) as exc_info:
        client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="plan_itinerary_blueprint")

    assert exc_info.value.code == "llm_empty_content"
    assert exc_info.value.details == {"finish_reason": "stop", "content_length": 0}


def test_llm_client_classifies_truncated_json_output(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse({"choices": [{"message": {"content": '{"days": ['}, "finish_reason": "length"}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    with pytest.raises(AppError) as exc_info:
        client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="plan_itinerary_blueprint")

    assert exc_info.value.code == "llm_truncated_json"
    assert exc_info.value.details == {"finish_reason": "length", "content_length": 10}


def test_llm_client_classifies_malformed_success_response(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse({"unexpected": True})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com")

    with pytest.raises(AppError) as exc_info:
        client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="plan_itinerary_blueprint")

    assert exc_info.value.code == "llm_invalid_response"
    assert client.call_metrics[-1]["error_code"] == "llm_invalid_response"


def test_planning_role_explicitly_enables_thinking_and_omits_temperature(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse(
            {
                "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 40,
                    "total_tokens": 160,
                    "prompt_cache_hit_tokens": 80,
                    "prompt_cache_miss_tokens": 40,
                    "completion_tokens_details": {"reasoning_tokens": 25},
                },
            }
        )

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    monkeypatch.delenv("LLM_PLANNING_THINKING", raising=False)
    monkeypatch.delenv("LLM_PLANNING_MAX_TOKENS", raising=False)
    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com", role="planning")

    assert client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="plan_itinerary_blueprint", temperature=0) == {"ok": True}

    body = captured["json"]
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"
    assert body["max_tokens"] == 16384
    assert "temperature" not in body

    metric = client.call_metrics[-1]
    assert metric["step"] == "plan_itinerary_blueprint"
    assert metric["thinking"] is True
    assert metric["prompt_tokens"] == 120
    assert metric["reasoning_tokens"] == 25
    assert metric["prompt_cache_hit_tokens"] == 80
    assert metric["finish_reason"] == "stop"
    assert metric["status"] == "success"
    assert "messages" not in metric


def test_copy_role_disables_thinking_and_uses_role_specific_output_budget(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    monkeypatch.delenv("LLM_COPY_THINKING", raising=False)
    monkeypatch.delenv("LLM_COPY_MAX_TOKENS", raising=False)
    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com", role="copy")

    client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="generate_itinerary_copy", temperature=0.3)

    body = captured["json"]
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 4096
    assert "reasoning_effort" not in body


def test_duration_role_sends_temperature_point_one(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": '{"durations": []}'}, "finish_reason": "stop"}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    client = LLMClient(api_key="test-key", model="test-model", base_url="https://example.com", role="duration")

    client.json_chat([{"role": "user", "content": "请输出 JSON"}], step="estimate_visit_duration", temperature=0.1)

    body = captured["json"]
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.1
    assert body["max_tokens"] == 4096
