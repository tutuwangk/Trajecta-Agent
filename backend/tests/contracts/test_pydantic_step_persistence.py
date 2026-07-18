from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.step_persistence import SqliteStepStore
import pytest

from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter


@pytest.mark.anyio
async def test_sqlite_snapshot_is_provider_valid_and_continuable_after_reopen(tmp_path):
    database = tmp_path / "provider-steps.sqlite3"
    store = SqliteStepStore(database=database)
    persistence = HarnessPersistenceAdapter(store)
    agent = Agent(
        TestModel(call_tools=["read_workspace"]),
        capabilities=[persistence.capability(agent_name="trip_planner")],
    )

    @agent.tool_plain
    def read_workspace() -> str:
        return "workspace-version-3"

    result = await agent.run("Read the workspace once.", conversation_id="business-run-1")
    assert result.output

    runs = await store.list_runs(conversation_id="business-run-1")
    assert len(runs) == 1
    provider_run_id = runs[0].run_id

    reopened = HarnessPersistenceAdapter(SqliteStepStore(database=database))
    messages = await reopened.continuable_messages(provider_run_id)
    assert messages
    assert await reopened.unresolved_effect_ids(provider_run_id) == ()

    open_calls: set[str] = set()
    for message in messages:
        if isinstance(message, ModelResponse):
            open_calls.update(
                part.tool_call_id for part in message.parts if isinstance(part, ToolCallPart)
            )
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    open_calls.discard(part.tool_call_id)
    assert open_calls == set()


@pytest.mark.anyio
async def test_run_without_tools_has_no_unresolved_effects(tmp_path):
    database = tmp_path / "provider-effects.sqlite3"
    store = SqliteStepStore(database=database)
    persistence = HarnessPersistenceAdapter(store)
    agent = Agent(TestModel(call_tools=[]), capabilities=[persistence.capability(agent_name="trip_planner")])

    result = await agent.run("Return without tools.", conversation_id="business-run-2")
    assert result.output
    run = (await store.list_runs(conversation_id="business-run-2"))[0]

    assert await persistence.unresolved_effect_ids(run.run_id) == ()
