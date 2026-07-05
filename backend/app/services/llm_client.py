from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from app.core import AppError, MissingConfigurationError


TRANSIENT_STATUS_CODES = {429, 500, 503}


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        trust_env: bool | None = None,
    ):
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("LLM_MODEL", "deepseek-v4-flash")
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.trust_env = _trust_env() if trust_env is None else trust_env
        self.max_retries = _positive_int_env("LLM_MAX_RETRIES", 2)
        self.max_tokens = _positive_int_env("LLM_MAX_TOKENS", 8192)
        self.timeout = httpx.Timeout(
            connect=_positive_float_env("LLM_CONNECT_TIMEOUT_SECONDS", 30.0),
            read=_positive_float_env("LLM_READ_TIMEOUT_SECONDS", 650.0),
            write=_positive_float_env("LLM_WRITE_TIMEOUT_SECONDS", 30.0),
            pool=_positive_float_env("LLM_POOL_TIMEOUT_SECONDS", 30.0),
        )

    def require_configured(self, step: str) -> None:
        if not self.api_key:
            raise MissingConfigurationError("LLM_API_KEY", step=step)

    def json_chat(self, messages: list[dict[str, str]], step: str, temperature: float = 0.2) -> Any:
        self.require_configured(step)
        response = self._post_with_retries(messages, step, temperature)
        content = response.json()["choices"][0]["message"]["content"]
        try:
            return parse_json_content(content)
        except json.JSONDecodeError as exc:
            raise AppError("LLM 返回内容不是有效 JSON。", code="llm_invalid_json", step=step) from exc

    def _post_with_retries(self, messages: list[dict[str, str]], step: str, temperature: float) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                        "response_format": {"type": "json_object"},
                        "max_tokens": self.max_tokens,
                        "stream": False,
                    },
                    timeout=self.timeout,
                    trust_env=self.trust_env,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(_retry_delay_seconds(attempt))
                    continue
                raise AppError("LLM 网络请求失败，请稍后重试。", code="llm_request_failed", step=step) from exc

            if response.status_code < 400:
                return response
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < self.max_retries:
                time.sleep(_retry_delay_seconds(attempt))
                continue
            raise AppError(_llm_error_message(response), code="llm_request_failed", step=step)

        raise AppError("LLM 服务调用失败，请稍后重试。", code="llm_request_failed", step=step) from last_error


def default_llm_client() -> LLMClient:
    return LLMClient()


def _trust_env() -> bool:
    value = os.getenv("LLM_TRUST_ENV", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _retry_delay_seconds(attempt: int) -> float:
    return min(4.0, 1.0 * (2**attempt))


def _llm_error_message(response: httpx.Response) -> str:
    detail = ""
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("type") or "").strip()
        elif isinstance(error, str):
            detail = error.strip()
        elif payload.get("message"):
            detail = str(payload.get("message")).strip()
    if detail:
        return f"DeepSeek API 返回 {response.status_code}：{detail}"
    return f"DeepSeek API 返回 {response.status_code}，请检查模型、余额、频率限制或请求参数。"


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
