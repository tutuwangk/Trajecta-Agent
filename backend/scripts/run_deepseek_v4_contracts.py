"""Run live DeepSeek V4 Pro multi-tool contract canaries without printing reasoning."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
from pathlib import Path
import sys
from time import monotonic
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from pydantic_ai import Agent, UsageLimits

from app.env import load_project_env
from app.trip_agent.adapters.provider import build_deepseek_v4_model, deepseek_v4_settings


async def run_canary(index: int, http_client: httpx.AsyncClient) -> dict[str, Any]:
    tool_sequence: list[str] = []
    agent = Agent(
        build_deepseek_v4_model("root", http_client=http_client),
        model_settings=deepseek_v4_settings("root"),
        instructions=(
            "You are validating a provider tool contract. "
            "Call get_contract_date first. Then call lookup_contract_fact with city Chengdu "
            "and the returned date. Then call submit_contract_candidate with the same city and date "
            "and fact_status fixture_ok. After all three tools return, answer exactly CONTRACT_OK. "
            "Do not invent a tool result and do not expose reasoning."
        ),
    )

    @agent.tool_plain
    def get_contract_date() -> str:
        tool_sequence.append("get_contract_date")
        return date.today().isoformat()

    @agent.tool_plain
    def lookup_contract_fact(city: str, visit_date: str) -> str:
        tool_sequence.append("lookup_contract_fact")
        return json.dumps({"city": city, "visit_date": visit_date, "status": "fixture_ok"})

    @agent.tool_plain
    def submit_contract_candidate(city: str, visit_date: str, fact_status: str) -> str:
        tool_sequence.append("submit_contract_candidate")
        return json.dumps(
            {"city": city, "visit_date": visit_date, "fact_status": fact_status, "accepted": True}
        )

    started = monotonic()
    try:
        result = await agent.run(
            f"Run provider contract canary {index}-{uuid4().hex[:8]}.",
            usage_limits=UsageLimits(request_limit=6, tool_calls_limit=4),
        )
        output = str(result.output).strip()
        usage = result.usage
        return {
            "index": index,
            "ok": tool_sequence
            == ["get_contract_date", "lookup_contract_fact", "submit_contract_candidate"]
            and output == "CONTRACT_OK",
            "tool_sequence": tool_sequence,
            "request_count": usage.requests,
            "tool_call_count": usage.tool_calls,
            "elapsed_seconds": round(monotonic() - started, 2),
            "output_contract_ok": output == "CONTRACT_OK",
        }
    except Exception as exc:
        return {
            "index": index,
            "ok": False,
            "tool_sequence": tool_sequence,
            "elapsed_seconds": round(monotonic() - started, 2),
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


async def async_main(runs: int) -> int:
    results = []
    timeout = httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as http_client:
        for index in range(1, runs + 1):
            result = await run_canary(index, http_client)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    summary = {
        "ok": all(result["ok"] for result in results),
        "runs": runs,
        "passed": sum(1 for result in results if result["ok"]),
        "failed": sum(1 for result in results if not result["ok"]),
        "unhandled_protocol_errors": sum(
            1
            for result in results
            if result.get("error_type") in {"BadRequestError", "UnexpectedModelBehavior", "ValidationError"}
        ),
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)
    return 0 if summary["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    if args.runs < 1 or args.runs > 30:
        parser.error("--runs must be between 1 and 30")
    load_project_env()
    return asyncio.run(async_main(args.runs))


if __name__ == "__main__":
    raise SystemExit(main())
