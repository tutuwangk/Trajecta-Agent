"""Create one provider-valid waiting_user run in the local V2 databases for browser acceptance."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.step_persistence import SqliteStepStore

from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime import TripAgentService
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter


class UnusedKnowledge:
    pass


async def main_async() -> None:
    root = Path(__file__).resolve().parents[1]
    repository = SqliteTripAgentRepository(root / "data" / "trip_agent_v2.sqlite3")
    persistence = HarnessPersistenceAdapter(
        SqliteStepStore(database=root / "data" / "trip_agent_v2_provider.sqlite3"),
        deferred_database=root / "data" / "trip_agent_v2_deferred.sqlite3",
    )

    async def ask(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="request_clarification",
                    tool_call_id="browser-clarification-tool",
                    args=json.dumps({
                        "questions": [
                            {
                                "question_id": "hotel_branch",
                                "prompt": "你希望住春熙路店还是东站店？",
                                "reason": "分店身份会改变每天的出发路线。",
                                "options": ["春熙路店", "东站店"],
                                "allow_other": True,
                            },
                            {
                                "question_id": "appointment",
                                "prompt": "下午三点的预约是否不可调整？",
                                "reason": "这会决定它是否成为发布阻断承诺。",
                                "options": ["不可调整", "可以微调"],
                                "allow_other": True,
                            },
                        ]
                    }, ensure_ascii=False),
                )
            ],
            finish_reason="tool_call",
        )

    service = TripAgentService(
        repository,
        UnusedKnowledge(),  # type: ignore[arg-type]
        persistence,
    )
    try:
        outcome = await service.start(
            raw_request="成都一日游，住全季酒店，下午三点有预约。",
            destination="成都",
            days=1,
            model=FunctionModel(ask),
        )
        print(
            json.dumps(
                {
                    "workspace_id": outcome.workspace_id,
                    "run_id": outcome.run_id,
                    "status": outcome.status.value,
                },
                ensure_ascii=False,
            )
        )
    finally:
        repository.close()


if __name__ == "__main__":
    asyncio.run(main_async())
