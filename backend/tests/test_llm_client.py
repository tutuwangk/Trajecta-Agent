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
        self.text = str(payload)

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
    assert "网络连接或请求超时" in exc_info.value.message


def test_llm_client_reports_upstream_status_without_leaking_key(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(
            {"error": {"message": "Invalid API key sk-secret"}},
            status_code=401,
        )

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)

    client = LLMClient(api_key="sk-secret", model="test-model", base_url="https://example.com")

    with pytest.raises(AppError) as exc_info:
        client.json_chat([{"role": "user", "content": "ping"}], step="extract_ugc")

    assert exc_info.value.code == "llm_request_failed"
    assert exc_info.value.step == "extract_ugc"
    assert "401" in exc_info.value.message
    assert "Key" in exc_info.value.message
    assert "sk-secret" not in exc_info.value.message
