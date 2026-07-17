from __future__ import annotations

import os

import httpx
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception, stop_after_attempt, wait_exponential

from app.core import MissingConfigurationError


TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS_CODES
    return isinstance(exc, httpx.TransportError)


def _validate_retry_response(response: httpx.Response) -> None:
    if response.status_code in TRANSIENT_STATUS_CODES:
        response.raise_for_status()


def build_deepseek_chat_model(role: str = "planning") -> OpenAIChatModel:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise MissingConfigurationError("LLM_API_KEY", step=f"{role}_agent")
    retry_attempts = _role_retry_count(role) + 1
    retry_transport = AsyncTenacityTransport(
        RetryConfig(
            retry=retry_if_exception(_is_retryable),
            wait=wait_retry_after(fallback_strategy=wait_exponential(multiplier=1, min=1, max=8), max_wait=30),
            stop=stop_after_attempt(retry_attempts),
            reraise=True,
        ),
        validate_response=_validate_retry_response,
    )
    timeout = httpx.Timeout(
        connect=_positive_float_env("LLM_CONNECT_TIMEOUT_SECONDS", 30.0),
        read=_role_read_timeout(role),
        write=_positive_float_env("LLM_WRITE_TIMEOUT_SECONDS", 30.0),
        pool=_positive_float_env("LLM_POOL_TIMEOUT_SECONDS", 30.0),
    )
    http_client = httpx.AsyncClient(
        transport=retry_transport,
        timeout=timeout,
        trust_env=_boolean_env("LLM_TRUST_ENV", True),
    )
    provider = OpenAIProvider(
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        api_key=api_key,
        http_client=http_client,
    )
    profile = OpenAIModelProfile(
        supports_tools=True,
        supports_thinking=True,
        openai_supports_tool_choice_required=False,
    )
    return OpenAIChatModel(_model_for_role(role), provider=provider, profile=profile)


def planning_model_settings(role: str = "planning") -> OpenAIChatModelSettings:
    thinking = _thinking_for_role(role)
    settings: OpenAIChatModelSettings = {
        "max_tokens": _max_tokens_for_role(role),
        "timeout": _role_read_timeout(role),
        "extra_body": {"thinking": {"type": "enabled" if thinking else "disabled"}},
    }
    if thinking:
        settings["extra_body"] = {
            "thinking": {"type": "enabled"},
            "reasoning_effort": _reasoning_effort_for_role(role),
        }
    else:
        settings["temperature"] = 0.0 if role == "planning" else 0.2
    return settings


def _model_for_role(role: str) -> str:
    normalized = role.strip().lower()
    role_value = os.getenv(f"LLM_{normalized.upper()}_MODEL")
    return role_value or os.getenv("LLM_MODEL", "deepseek-v4-flash")


def _thinking_for_role(role: str) -> bool:
    normalized = role.strip().lower()
    # Structured planning favors predictable JSON over hidden reasoning traces.
    # Deployments can still opt in explicitly for model experiments.
    default = False
    role_name = f"LLM_{normalized.upper()}_THINKING"
    return _boolean_env(role_name, _boolean_env("LLM_THINKING", default))


def _reasoning_effort_for_role(role: str) -> str:
    normalized = role.strip().lower()
    value = os.getenv(f"LLM_{normalized.upper()}_REASONING_EFFORT", "high").strip().lower()
    return value if value in {"high", "max"} else "high"


def _max_tokens_for_role(role: str) -> int:
    normalized = role.strip().lower()
    defaults = {"planning": 8192, "copy": 4096, "duration": 4096}
    role_name = f"LLM_{normalized.upper()}_MAX_TOKENS"
    if os.getenv(role_name):
        return _positive_int_env(role_name, defaults.get(normalized, 8192))
    return _positive_int_env("LLM_MAX_TOKENS", defaults.get(normalized, 8192))


def _role_read_timeout(role: str) -> float:
    normalized = role.strip().upper()
    default = 120.0 if normalized == "PLANNING" else _positive_float_env("LLM_READ_TIMEOUT_SECONDS", 180.0)
    return _positive_float_env(f"LLM_{normalized}_READ_TIMEOUT_SECONDS", default)


def _role_retry_count(role: str) -> int:
    normalized = role.strip().upper()
    default = 1 if normalized == "PLANNING" else _positive_int_env("LLM_MAX_RETRIES", 2)
    return _positive_int_env(f"LLM_{normalized}_MAX_RETRIES", default)


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
