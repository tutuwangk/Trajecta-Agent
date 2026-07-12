from app.agents.ugc_reader import SYSTEM_PROMPT, extract_ugc_items


def test_ugc_prompt_requires_recall_for_short_and_symbol_brands():
    assert "短中文品牌" in SYSTEM_PROMPT
    assert "& / · / -" in SYSTEM_PROMPT
    assert "连锁品牌" in SYSTEM_PROMPT


def test_ugc_reader_failure_returns_empty_items_for_text_recovery():
    class UnavailableLLM:
        def json_chat(self, *args, **kwargs):
            raise RuntimeError("temporary failure")

    assert extract_ugc_items("去太古里，顺便喝杯喜茶再买个B&C", UnavailableLLM()) == []
