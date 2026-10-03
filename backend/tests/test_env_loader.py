import os
from pathlib import Path
import subprocess
import sys

from app.env import load_env_file


def test_load_env_file_reads_values_without_overriding_existing_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "LLM_API_KEY=from-file",
                "AMAP_API_KEY='amap-from-file'",
                "LLM_BASE_URL=https://file.example.com",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("AMAP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://existing.example.com")

    load_env_file(env_path)

    assert os.environ["LLM_API_KEY"] == "from-file"
    assert os.environ["AMAP_API_KEY"] == "amap-from-file"
    assert os.environ["LLM_BASE_URL"] == "https://existing.example.com"


def test_package_import_leaves_configuration_loading_to_entry_points():
    script = """
import sys
from types import ModuleType

env_module = ModuleType('app.env')
def load_project_env():
    raise AssertionError('importing app loaded project configuration')
env_module.load_project_env = load_project_env
sys.modules['app.env'] = env_module
import app
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
