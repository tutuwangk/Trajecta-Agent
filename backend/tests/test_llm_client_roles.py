from app.services.llm_client import LLMClient


def test_llm_client_uses_role_specific_model_overrides(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "shared-model")
    monkeypatch.setenv("LLM_PLANNING_MODEL", "planner-model")
    monkeypatch.setenv("LLM_COPY_MODEL", "copy-model")

    planning = LLMClient(api_key="test-key", role="planning")
    copy = LLMClient(api_key="test-key", role="copy")
    default = LLMClient(api_key="test-key")

    assert planning.model == "planner-model"
    assert copy.model == "copy-model"
    assert default.model == "shared-model"
