from __future__ import annotations

from pydantic_ai import Agent, CallDeferred, DeferredToolRequests
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.step_persistence import SqliteStepStore
import pytest

from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter


@pytest.mark.anyio
async def test_deferred_clarification_continues_with_original_history_and_tool_call_id(tmp_path):
    store = SqliteStepStore(database=tmp_path / "deferred.sqlite3")
    persistence = HarnessPersistenceAdapter(store)
    agent = Agent(
        TestModel(call_tools=["request_clarification"]),
        output_type=[str, DeferredToolRequests],
        capabilities=[persistence.capability(agent_name="trip_planner")],
    )

    @agent.tool_plain
    def request_clarification(question: str) -> str:
        raise CallDeferred(metadata={"question": question})

    first = await agent.run("Ask one clarification question.", conversation_id="business-run-hitl")
    assert isinstance(first.output, DeferredToolRequests)
    assert len(first.output.calls) == 1
    pending = first.output.calls[0]
    assert pending.tool_name == "request_clarification"

    deferred_results = first.output.build_results(calls={pending.tool_call_id: "Prefer a relaxed second day."})
    second = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=deferred_results,
        conversation_id="business-run-hitl",
    )

    assert isinstance(second.output, str)
    runs = await store.list_runs(conversation_id="business-run-hitl")
    assert len(runs) == 2
    assert runs[0].run_id != runs[1].run_id
    assert await persistence.unresolved_effect_ids(runs[0].run_id) == (pending.tool_call_id,)
    assert await persistence.unresolved_effect_ids(runs[1].run_id) == ()
