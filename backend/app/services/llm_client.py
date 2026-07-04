from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.core import AppError, MissingConfigurationError


class LLMClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")).rstrip("/")

    def require_configured(self, step: str) -> None:
        if not self.api_key:
            raise MissingConfigurationError("LLM_API_KEY", step=step)

    def json_chat(self, messages: list[dict[str, str]], step: str, temperature: float = 0.2) -> Any:
        self.require_configured(step)
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                },
                timeout=60,
                trust_env=False,
            )
        except httpx.HTTPError as exc:
            raise AppError("LLM 服务调用失败，请检查 Key、模型名称或网络连接。", code="llm_request_failed", step=step) from exc
        if response.status_code >= 400:
            raise AppError("LLM 服务调用失败，请检查 Key、模型名称或网络连接。", code="llm_request_failed", step=step)
        content = response.json()["choices"][0]["message"]["content"]
        try:
            return parse_json_content(content)
        except json.JSONDecodeError as exc:
            raise AppError("LLM 返回内容不是有效 JSON。", code="llm_invalid_json", step=step) from exc


def default_llm_client() -> LLMClient:
    return LLMClient()


def parse_json_content(content: str) -> Any:
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        candidate = _extract_json_candidate(text)
        if candidate is None:
            raise
        return json.loads(candidate)


def _extract_json_candidate(text: str) -> str | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return text[index : index + end]
    return None
