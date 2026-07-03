import os

from app.env import load_env_file


def test_load_env_file_reads_values_without_overriding_existing_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "LLM_API_KEY=from-file",
                "AMAP_API_KEY='amap-from-file'",
                "LLM_MODEL=deepseek-chat",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("AMAP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODEL", "existing-model")

    load_env_file(env_path)

    assert os.environ["LLM_API_KEY"] == "from-file"
    assert os.environ["AMAP_API_KEY"] == "amap-from-file"
    assert os.environ["LLM_MODEL"] == "existing-model"
