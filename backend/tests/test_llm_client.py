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
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_llm_client_disables_env_proxy(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)

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
