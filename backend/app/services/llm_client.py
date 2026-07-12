from __future__ import annotations

import json
import os
import time
from time import perf_counter
from typing import Any

import httpx

from app.core import AppError, MissingConfigurationError


TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        trust_env: bool | None = None,
        role: str | None = None,
        thinking: bool | None = None,
    ):
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.role = role or "default"
        self.model = model or _model_for_role(self.role)
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.trust_env = _trust_env() if trust_env is None else trust_env
        self.max_retries = _positive_int_env("LLM_MAX_RETRIES", 2)
        self.thinking = _thinking_for_role(self.role) if thinking is None else thinking
        self.reasoning_effort = _reasoning_effort_for_role(self.role) if self.thinking else None
        self.max_tokens = _max_tokens_for_role(self.role)
        self.call_metrics: list[dict[str, Any]] = []
        self.timeout = httpx.Timeout(
            connect=_positive_float_env("LLM_CONNECT_TIMEOUT_SECONDS", 30.0),
            read=_positive_float_env("LLM_READ_TIMEOUT_SECONDS", 650.0),
            write=_positive_float_env("LLM_WRITE_TIMEOUT_SECONDS", 30.0),
            pool=_positive_float_env("LLM_POOL_TIMEOUT_SECONDS", 30.0),
        )

    def require_configured(self, step: str) -> None:
        if not self.api_key:
            raise MissingConfigurationError("LLM_API_KEY", step=step)

    def increase_output_budget(self) -> int:
        ceiling = _positive_int_env("LLM_MAX_OUTPUT_TOKENS", 32768 if self.role == "planning" else self.max_tokens)
        if ceiling > self.max_tokens:
            self.max_tokens = min(ceiling, self.max_tokens * 2)
        return self.max_tokens

    def json_chat(self, messages: list[dict[str, str]], step: str, temperature: float = 0.2) -> Any:
        self.require_configured(step)
        started_at = perf_counter()
        prompt_characters = sum(len(str(message.get("content") or "")) for message in messages)
        try:
            response, request_attempts = self._post_with_retries(messages, step, temperature)
        except AppError as exc:
            self._record_call(
                step=step,
                status="error",
                duration_ms=round((perf_counter() - started_at) * 1000),
                prompt_characters=prompt_characters,
                error_code=exc.code,
                request_attempts=int(exc.details.get("request_attempts") or self.max_retries + 1),
            )
            raise
        try:
            response_payload = response.json()
            choice = response_payload["choices"][0]
            if not isinstance(choice, dict):
                raise TypeError("choice is not an object")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            metric = self._record_call(
                step=step,
                status="error",
                duration_ms=round((perf_counter() - started_at) * 1000),
                prompt_characters=prompt_characters,
                error_code="llm_invalid_response",
                request_attempts=request_attempts,
            )
            raise AppError(
                "LLM 服务返回格式异常，系统将尝试使用稳定规划方式。",
                code="llm_invalid_response",
                step=step,
                details={"request_attempts": metric["request_attempts"]},
            ) from exc
        content = str(choice.get("message", {}).get("content") or "")
        finish_reason = str(choice.get("finish_reason") or "")
        details = {"finish_reason": finish_reason, "content_length": len(content)}
        usage = response_payload.get("usage") if isinstance(response_payload, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        metric = self._record_call(
            step=step,
            status="success",
            duration_ms=round((perf_counter() - started_at) * 1000),
            prompt_characters=prompt_characters,
            content_length=len(content),
            finish_reason=finish_reason,
            request_attempts=request_attempts,
            usage=usage,
        )
        if not content.strip():
            metric["status"] = "error"
            metric["error_code"] = "llm_empty_content"
            raise AppError("LLM 返回了空内容。", code="llm_empty_content", step=step, details=details)
        try:
            return parse_json_content(content)
        except json.JSONDecodeError as exc:
            code = "llm_truncated_json" if finish_reason == "length" or _looks_truncated_json(content) else "llm_invalid_json"
            message = "LLM 返回的 JSON 内容被截断。" if code == "llm_truncated_json" else "LLM 返回内容不是有效 JSON。"
            metric["status"] = "error"
            metric["error_code"] = code
            raise AppError(message, code=code, step=step, details=details) from exc

    def _post_with_retries(self, messages: list[dict[str, str]], step: str, temperature: float) -> tuple[httpx.Response, int]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                body = {
                    "model": self.model,
                    "messages": messages,
                    "thinking": {"type": "enabled" if self.thinking else "disabled"},
                    "response_format": {"type": "json_object"},
                    "max_tokens": self.max_tokens,
                    "stream": False,
                }
                if self.thinking:
                    body["reasoning_effort"] = self.reasoning_effort
                else:
                    body["temperature"] = temperature
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=self.timeout,
                    trust_env=self.trust_env,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(_retry_delay_seconds(attempt))
                    continue
                raise AppError(
                    "LLM 网络请求失败，请稍后重试。",
                    code="llm_request_failed",
                    step=step,
                    details={"request_attempts": attempt + 1},
                ) from exc

            if response.status_code < 400:
                return response, attempt + 1
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < self.max_retries:
                time.sleep(_retry_delay_seconds(attempt))
                continue
            raise AppError(
                _llm_error_message(response),
                code="llm_request_failed",
                step=step,
                details={"request_attempts": attempt + 1, "http_status": response.status_code},
            )

        raise AppError(
            "LLM 服务调用失败，请稍后重试。",
            code="llm_request_failed",
            step=step,
            details={"request_attempts": self.max_retries + 1},
        ) from last_error

    def _record_call(
        self,
        *,
        step: str,
        status: str,
        duration_ms: int,
        prompt_characters: int,
        content_length: int = 0,
        finish_reason: str = "",
        request_attempts: int = 1,
        error_code: str = "",
        usage: dict | None = None,
    ) -> dict[str, Any]:
        usage = usage or {}
        completion_details = usage.get("completion_tokens_details")
        completion_details = completion_details if isinstance(completion_details, dict) else {}
        metric = {
            "step": step,
            "role": self.role,
            "model": self.model,
            "thinking": self.thinking,
            "reasoning_effort": self.reasoning_effort,
            "max_tokens": self.max_tokens,
            "status": status,
            "error_code": error_code,
            "duration_ms": duration_ms,
            "request_attempts": request_attempts,
            "prompt_characters": prompt_characters,
            "content_length": content_length,
            "finish_reason": finish_reason,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
            "prompt_cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens") or 0),
            "prompt_cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens") or 0),
        }
        self.call_metrics.append(metric)
        return metric


def default_llm_client() -> LLMClient:
    return LLMClient()


def default_planning_llm_client() -> LLMClient:
    return LLMClient(role="planning")


def default_copy_llm_client() -> LLMClient:
    return LLMClient(role="copy")


def default_duration_llm_client() -> LLMClient:
    return LLMClient(role="duration")


def _trust_env() -> bool:
    value = os.getenv("LLM_TRUST_ENV", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _model_for_role(role: str | None) -> str:
    normalized = (role or "default").strip().lower()
    if normalized == "planning":
        return os.getenv("LLM_PLANNING_MODEL") or os.getenv("LLM_MODEL", "deepseek-v4-flash")
    if normalized == "copy":
        return os.getenv("LLM_COPY_MODEL") or os.getenv("LLM_MODEL", "deepseek-v4-flash")
    if normalized == "duration":
        return os.getenv("LLM_DURATION_MODEL") or os.getenv("LLM_MODEL", "deepseek-v4-flash")
    return os.getenv("LLM_MODEL", "deepseek-v4-flash")


def _thinking_for_role(role: str | None) -> bool:
    normalized = (role or "default").strip().lower()
    default = normalized == "planning"
    env_name = f"LLM_{normalized.upper()}_THINKING" if normalized in {"planning", "copy", "duration"} else "LLM_THINKING"
    return _boolean_env(env_name, default)


def _reasoning_effort_for_role(role: str | None) -> str:
    normalized = (role or "default").strip().lower()
    env_name = f"LLM_{normalized.upper()}_REASONING_EFFORT" if normalized in {"planning", "copy", "duration"} else "LLM_REASONING_EFFORT"
    value = os.getenv(env_name, "high").strip().lower()
    return value if value in {"high", "max"} else "high"


def _max_tokens_for_role(role: str | None) -> int:
    normalized = (role or "default").strip().lower()
    defaults = {"planning": 16384, "copy": 4096, "duration": 4096}
    default = defaults.get(normalized, 8192)
    role_env = f"LLM_{normalized.upper()}_MAX_TOKENS" if normalized in {"planning", "copy", "duration"} else "LLM_MAX_TOKENS"
    if role_env != "LLM_MAX_TOKENS" and os.getenv(role_env):
        return _positive_int_env(role_env, default)
    return _positive_int_env("LLM_MAX_TOKENS", default)


def _boolean_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


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


def _looks_truncated_json(content: str) -> bool:
    text = content.strip()
    if not text or text[0] not in "{[":
        return False
    pairs = {"{": "}", "[": "]"}
    return text[-1] != pairs[text[0]]
