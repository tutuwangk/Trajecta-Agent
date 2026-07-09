from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core import AppError, MissingConfigurationError

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        trust_env: bool | None = None,
    ):
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.trust_env = _trust_env() if trust_env is None else trust_env

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
                trust_env=self.trust_env,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "LLM request error: step=%s model=%s base_url=%s error_type=%s",
                step,
                self.model,
                _safe_base_url(self.base_url),
                type(exc).__name__,
            )
            raise AppError(_network_error_message(), code="llm_request_failed", step=step) from exc
        if response.status_code >= 400:
            message = _upstream_error_message(response, self.api_key)
            logger.warning(
                "LLM upstream error: step=%s model=%s base_url=%s status_code=%s detail=%s",
                step,
                self.model,
                _safe_base_url(self.base_url),
                response.status_code,
                _redact_sensitive(_upstream_error_detail(response), self.api_key),
            )
            raise AppError(message, code="llm_request_failed", step=step)
        content = response.json()["choices"][0]["message"]["content"]
        try:
            return parse_json_content(content)
        except json.JSONDecodeError as exc:
            raise AppError("LLM 返回内容不是有效 JSON。", code="llm_invalid_json", step=step) from exc


def default_llm_client() -> LLMClient:
    return LLMClient()


def _trust_env() -> bool:
    value = os.getenv("LLM_TRUST_ENV", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


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


def _network_error_message() -> str:
    return "LLM 服务调用失败：网络连接或请求超时，请检查网络、代理或 LLM_BASE_URL。"


def _upstream_error_message(response: httpx.Response, api_key: str | None) -> str:
    status_code = response.status_code
    detail = _redact_sensitive(_upstream_error_detail(response), api_key)
    reason = _status_reason(status_code)
    if detail:
        return f"LLM 服务调用失败：上游返回 {status_code}，{reason}上游摘要：{detail}"
    return f"LLM 服务调用失败：上游返回 {status_code}，{reason}"


def _status_reason(status_code: int) -> str:
    if status_code in {401, 403}:
        return "Key 无效、无权限或额度不可用，请检查 LLM_API_KEY。"
    if status_code == 400:
        return "请求格式、模型名称或响应格式不被支持，请检查 LLM_MODEL 和 prompt。"
    if status_code == 404:
        return "接口地址或模型不存在，请检查 LLM_BASE_URL 和 LLM_MODEL。"
    if status_code == 429:
        return "请求过于频繁或额度不足，请稍后重试。"
    if status_code >= 500:
        return "模型服务暂时不可用，请稍后重试。"
    return "请检查 Key、模型名称、接口地址或网络配置。"


def _upstream_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = getattr(response, "text", "")
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            payload = error.get("message") or error.get("code") or error
        elif error:
            payload = error
        else:
            payload = payload.get("message") or payload
    text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload or "")
    return re.sub(r"\s+", " ", text).strip()[:240]


def _redact_sensitive(text: str, api_key: str | None) -> str:
    redacted = text
    if api_key:
        redacted = redacted.replace(api_key, "<redacted>")
    return re.sub(r"sk-[A-Za-z0-9_-]+", "<redacted>", redacted)


def _safe_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if not parsed.netloc:
        return base_url
    return f"{parsed.scheme}://{parsed.netloc}"
