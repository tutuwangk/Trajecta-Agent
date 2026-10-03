from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Literal

import httpx
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionToolParam,
)
from openai.types.chat.chat_completion_tool_choice_option_param import (
    ChatCompletionToolChoiceOptionParam,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIChatModelSettings,
)
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from app.core import MissingConfigurationError


DEEPSEEK_V4_PRO = "deepseek-v4-pro"
DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
DeepSeekRole = Literal["root", "lightweight"]
DeepSeekModelVariant = Literal["pro", "flash"]


class DeepSeekV4ChatModel(OpenAIChatModel):
    """DeepSeek V4 model with its exact tool-history wire contract."""

    @dataclass
    class _MapModelResponseContext(
        OpenAIChatModel._MapModelResponseContext
    ):
        def _into_message_param(
            self,
        ) -> ChatCompletionAssistantMessageParam | None:
            message = super()._into_message_param()
            if (
                message is not None
                and message.get("tool_calls")
                and message.get("content") is None
            ):
                message["content"] = ""
            return message

    def _get_tool_choice(
        self,
        model_settings: OpenAIChatModelSettings,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[
        list[ChatCompletionToolParam],
        ChatCompletionToolChoiceOptionParam | None,
    ]:
        tools, _ = super()._get_tool_choice(
            model_settings,
            model_request_parameters,
        )
        return tools, None


def build_deepseek_v4_model(
    role: DeepSeekRole = "root",
    *,
    model_variant: DeepSeekModelVariant | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> DeepSeekV4ChatModel:
    resolved_key = api_key or os.getenv("LLM_API_KEY")
    if not resolved_key:
        raise MissingConfigurationError(
            "LLM_API_KEY",
            step=f"trip_agent_{role}",
        )
    resolved_base_url = (
        base_url or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com"
    ).rstrip("/")
    provider = OpenAIProvider(
        openai_client=AsyncOpenAI(
            api_key=resolved_key,
            base_url=resolved_base_url,
            http_client=http_client,
            max_retries=0,
        )
    )
    profile = OpenAIModelProfile(
        supports_tools=True,
        supports_thinking=True,
        openai_chat_thinking_field="reasoning_content",
        openai_chat_send_back_thinking_parts="field",
        openai_supports_tool_choice_required=False,
        openai_supports_strict_tool_definition=False,
        openai_system_prompt_role="system",
    )
    resolved_variant = model_variant or (
        "pro" if role == "root" else "flash"
    )
    model_name = (
        DEEPSEEK_V4_PRO
        if resolved_variant == "pro"
        else DEEPSEEK_V4_FLASH
    )
    return DeepSeekV4ChatModel(
        model_name,
        provider=provider,
        profile=profile,
    )


def deepseek_v4_settings(
    role: DeepSeekRole = "root",
    *,
    reasoning_effort: Literal["high", "max"] = "high",
) -> OpenAIChatModelSettings:
    settings: dict[str, Any] = {
        "max_tokens": 16_384 if role == "root" else 8_192,
        "timeout": 180.0 if role == "root" else 120.0,
        "extra_body": {
            "thinking": {"type": "enabled"},
            "reasoning_effort": reasoning_effort,
        },
    }
    return settings  # type: ignore[return-value]
