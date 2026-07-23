from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic_ai import Agent

from app.trip_agent.adapters.provider import (
    DEEPSEEK_V4_FLASH,
    DEEPSEEK_V4_PRO,
    build_deepseek_v4_model,
    deepseek_v4_settings,
    is_complete_final_response,
)


def test_root_authority_can_use_flash_without_changing_production_default():
    production = build_deepseek_v4_model(
        "root", api_key="contract-key", base_url="https://deepseek.invalid"
    )
    acceptance = build_deepseek_v4_model(
        "root",
        model_variant="flash",
        api_key="contract-key",
        base_url="https://deepseek.invalid",
    )

    assert production.model_name == DEEPSEEK_V4_PRO
    assert acceptance.model_name == DEEPSEEK_V4_FLASH


def _completion(message: dict[str, Any], *, finish_reason: str, request_id: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": request_id,
            "object": "chat.completion",
            "created": 1_784_368_000,
            "model": "deepseek-v4-pro",
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


@pytest.mark.anyio
async def test_thinking_tool_round_replays_reasoning_without_tool_choice():
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return _completion(
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "I need the date before planning.",
                    "tool_calls": [
                        {
                            "id": "call-date-1",
                            "type": "function",
                            "function": {"name": "get_date", "arguments": "{}"},
                        }
                    ],
                },
                finish_reason="tool_calls",
                request_id="contract-1",
            )
        assistant = next(message for message in body["messages"] if message["role"] == "assistant")
        assert assistant["reasoning_content"] == "I need the date before planning."
        assert assistant["content"] == ""
        assert body["messages"][-1]["role"] == "tool"
        assert body["messages"][-1]["tool_call_id"] == "call-date-1"
        return _completion(
            {"role": "assistant", "content": "2026-08-03 is ready.", "reasoning_content": "Done."},
            finish_reason="stop",
            request_id="contract-2",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = Agent(
            build_deepseek_v4_model(api_key="contract-key", base_url="https://deepseek.invalid", http_client=client),
            model_settings=deepseek_v4_settings(),
        )

        @agent.tool_plain
        def get_date() -> str:
            return "2026-08-03"

        result = await agent.run("Plan the trip.")

    assert result.output == "2026-08-03 is ready."
    assert len(requests) == 2
    for body in requests:
        assert "tool_choice" not in body
        assert "temperature" not in body
        assert "top_p" not in body
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"


@pytest.mark.anyio
async def test_invalid_tool_arguments_are_rejected_before_execution():
    request_count = 0
    executed: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            arguments = '{"city":"成都","invented":true}'
        elif request_count == 2:
            body = json.loads(request.content)
            assert body["messages"][-1]["role"] == "tool"
            arguments = '{"city":"成都"}'
        else:
            return _completion(
                {"role": "assistant", "content": "Facts acquired.", "reasoning_content": "Done."},
                finish_reason="stop",
                request_id="contract-invalid-3",
            )
        return _completion(
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Need facts.",
                "tool_calls": [
                    {
                        "id": f"call-facts-{request_count}",
                        "type": "function",
                        "function": {"name": "acquire_facts", "arguments": arguments},
                    }
                ],
            },
            finish_reason="tool_calls",
            request_id=f"contract-invalid-{request_count}",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = Agent(
            build_deepseek_v4_model(api_key="contract-key", base_url="https://deepseek.invalid", http_client=client),
            model_settings=deepseek_v4_settings(),
            retries=2,
        )

        @agent.tool_plain
        def acquire_facts(city: str) -> str:
            executed.append(city)
            return "ok"

        result = await agent.run("Acquire facts.")

    assert result.output == "Facts acquired."
    assert executed == ["成都"]
    assert request_count == 3


@pytest.mark.anyio
async def test_parallel_read_only_tool_calls_are_all_returned_before_next_request():
    request_count = 0
    executed: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return _completion(
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "Read both independent facts.",
                    "tool_calls": [
                        {
                            "id": "call-read-a",
                            "type": "function",
                            "function": {"name": "read_fact", "arguments": '{"name":"a"}'},
                        },
                        {
                            "id": "call-read-b",
                            "type": "function",
                            "function": {"name": "read_fact", "arguments": '{"name":"b"}'},
                        },
                    ],
                },
                finish_reason="tool_calls",
                request_id="parallel-1",
            )
        body = json.loads(request.content)
        tool_messages = [message for message in body["messages"] if message["role"] == "tool"]
        assert {message["tool_call_id"] for message in tool_messages} == {"call-read-a", "call-read-b"}
        return _completion(
            {"role": "assistant", "content": "Both read.", "reasoning_content": "Done."},
            finish_reason="stop",
            request_id="parallel-2",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = Agent(
            build_deepseek_v4_model(api_key="contract-key", base_url="https://deepseek.invalid", http_client=client),
            model_settings=deepseek_v4_settings(),
        )

        @agent.tool_plain
        def read_fact(name: str) -> str:
            executed.append(name)
            return name.upper()

        result = await agent.run("Read both facts.")

    assert result.output == "Both read."
    assert sorted(executed) == ["a", "b"]
    assert request_count == 2


@pytest.mark.anyio
async def test_unknown_tool_name_becomes_retry_feedback_not_execution():
    request_count = 0
    executed = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return _completion(
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "Call an unavailable tool.",
                    "tool_calls": [
                        {
                            "id": "call-unknown",
                            "type": "function",
                            "function": {"name": "invented_tool", "arguments": "{}"},
                        }
                    ],
                },
                finish_reason="tool_calls",
                request_id="unknown-1",
            )
        body = json.loads(request.content)
        assert body["messages"][-1]["role"] == "tool"
        assert body["messages"][-1]["tool_call_id"] == "call-unknown"
        return _completion(
            {"role": "assistant", "content": "Recovered.", "reasoning_content": "Use known tools only."},
            finish_reason="stop",
            request_id="unknown-2",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = Agent(
            build_deepseek_v4_model(api_key="contract-key", base_url="https://deepseek.invalid", http_client=client),
            model_settings=deepseek_v4_settings(),
            retries=2,
        )

        @agent.tool_plain
        def known_tool() -> str:
            nonlocal executed
            executed = True
            return "ok"

        result = await agent.run("Recover from an unknown tool.")

    assert result.output == "Recovered."
    assert executed is False
    assert request_count == 2


@pytest.mark.anyio
async def test_length_finish_reason_is_preserved_for_domain_rejection():
    async def handler(request: httpx.Request) -> httpx.Response:
        return _completion(
            {"role": "assistant", "content": "partial", "reasoning_content": "truncated"},
            finish_reason="length",
            request_id="length-1",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = Agent(
            build_deepseek_v4_model(api_key="contract-key", base_url="https://deepseek.invalid", http_client=client),
            model_settings=deepseek_v4_settings(),
        )
        result = await agent.run("Return a truncated answer.")

    assert result.output == "partial"
    assert result.response.finish_reason == "length"
    assert is_complete_final_response(result.response) is False
