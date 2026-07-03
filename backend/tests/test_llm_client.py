import pytest

from app.services.llm_client import parse_json_content


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
